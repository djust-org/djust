"""Shared, secure resolution of client-controlled mount inputs (F22 + F23).

This is the **single chokepoint** every transport's mount entry point calls so
the WebSocket, SSE, and generic ``ViewRuntime`` paths cannot drift apart again
(parallel-path-drift cure, #1646). Two attacker-controlled inputs arrive on the
mount frame:

1. ``url`` — the client's current page path, rebuilt into an ``HttpRequest`` via
   ``RequestFactory``. Validated by :func:`validate_mount_url` (F23 / #1819).
2. ``view`` — the dotted path of the ``LiveView`` class to mount. Resolved by
   :func:`resolve_view_class` (F22), which validates the path *shape*, checks an
   allowlist **before importing anything**, and only then imports + looks up the
   class.

--------------------------------------------------------------------------------
F22 — unsafe reflection on the mount path (CWE-470 / CWE-1188 / CWE-209)
--------------------------------------------------------------------------------
On mount the client picks *which module the server imports*. Importing a
not-yet-loaded module runs its top-level code (import side effects:
``antigravity`` opens a browser, ``os``/``socket``/heavy modules consume
resources). The old guard was **fail-open** (``if allowed_modules:`` — skipped
entirely when ``LIVEVIEW_ALLOWED_MODULES`` was unset, the common case) and used
a **boundary-less** ``view_path.startswith(m)`` prefix match (so ``["myapp"]``
also admitted ``myapp_evil.views.Pwn``). :func:`resolve_view_class` closes all
three sub-issues: shape-check → allowlist-before-import → module-segment match.

**Allowlist semantics (default-behavior change — see CHANGELOG):**

  * If ``settings.LIVEVIEW_ALLOWED_MODULES`` is set (non-empty), the allowed
    prefixes are exactly those entries.
  * If it is **unset / empty**, the allowlist falls back to a **non-breaking**
    set derived from the project itself: the top-level package root of every
    entry in ``settings.INSTALLED_APPS`` **plus ``"djust"``**.

  Rationale: legitimate ``LiveView`` classes live inside installed apps (or the
  framework's own demo/test views under ``djust``). This blocks arbitrary
  ``os`` / ``antigravity`` / site-packages imports **without** breaking the
  overwhelmingly common case of an app that never configured
  ``LIVEVIEW_ALLOWED_MODULES``. It deliberately does NOT fail closed to *nothing*
  (that would break every existing app) and does NOT fail open to *everything*
  (the vulnerability). Apps that need to mount a lazily-imported view living
  outside an installed app must opt in via ``LIVEVIEW_ALLOWED_MODULES``.

The matching is **module-segment boundary** (``path == entry`` or
``path.startswith(entry + ".")``), never a bare ``startswith``, so ``"myapp"``
matches ``myapp`` and ``myapp.views`` but NOT ``myapp_evil``.

The import itself uses ``importlib.import_module(module_path)`` (NO ``fromlist``)
and ``vars(module).get(class_name)`` (a ``__dict__`` lookup, NOT ``getattr``) so
a client-controlled ``class_name`` cannot trigger a module-level ``__getattr__``
(PEP 562) submodule import (GHSA-7prp-2623-8g45 follow-up). The ``LiveView``
subclass check is kept as defense-in-depth *after* resolution.
"""

from __future__ import annotations

import re
from typing import Optional

# View-path shape: a dotted module path of at least two segments, each a valid
# Python identifier (``module[.sub].ClassName``). No leading/trailing dot, no
# empty segment (which is how ``..`` / a doubled dot would appear), no chars
# outside ``[A-Za-z0-9_.]``. Validated BEFORE any import so a malformed /
# traversal-style path is rejected without touching the import machinery.
_VIEW_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")


# ---------------------------------------------------------------------------
# Structured rejection result
# ---------------------------------------------------------------------------
class ViewResolution:
    """Result of :func:`resolve_view_class`.

    Callers turn a rejection into their existing ``_safe_error`` envelope by
    reading :attr:`detail` (the verbose, DEBUG-only message) and
    :attr:`generic` (the production-safe fallback — ``"View not found"`` or
    ``"Invalid view class"``). On success, :attr:`view_class` is the resolved
    ``LiveView`` subclass and the object is truthy.
    """

    __slots__ = ("view_class", "detail", "generic")

    def __init__(
        self,
        view_class: Optional[type] = None,
        detail: str = "",
        generic: str = "View not found",
    ) -> None:
        self.view_class = view_class
        self.detail = detail
        self.generic = generic

    def __bool__(self) -> bool:
        return self.view_class is not None


def validate_mount_url(url: Optional[str]) -> str:
    """Validate a client-supplied mount/redirect URL, falling back to ``"/"``.

    Moved here verbatim from ``websocket.py`` so the WebSocket, SSE, and
    ``ViewRuntime`` paths share one validator and cannot drift (#1646 / F23).
    ``websocket.py`` re-exports this under its historical name
    ``_validate_mount_url``.

    The client sends the current page URL in the mount / ``live_redirect``
    frames so the server can rebuild a faithful ``HttpRequest`` (via
    ``RequestFactory``). That value is fully attacker-controlled, so it must be
    a *site-relative path* before it is fed to ``RequestFactory.get()``,
    ``resolve()``, log statements, or string concatenation with a query string.

    This is defense-in-depth. Django's WSGI path parsing already strips bare
    ``\\r``/``\\n`` from the path and discards the scheme/host of an absolute
    URL, but it does NOT normalize ``..`` segments (``"../../admin/"`` lands in
    ``request.path`` as ``/..../admin/`` and in ``request.path_info`` verbatim
    as ``../../admin/``), and it silently *accepts* an absolute or
    protocol-relative URL by dropping the authority. A view that inspects
    ``request.path`` for an auth/routing decision would see the traversed path.
    We reject all of those shapes here rather than relying on every downstream
    consumer to re-sanitize.

    Rejected (all fall back to ``"/"``):
      * empty / non-string / not starting with ``"/"`` (relative path,
        traversal such as ``"../../admin/"``)
      * protocol-relative (``"//evil.com/page"``) -- ``urlparse`` reports a netloc
      * absolute (``"https://evil.com/page"``) -- ``urlparse`` reports a scheme
      * contains a ``\\r`` or ``\\n`` (CRLF / header / log injection)
      * contains a ``".."`` path segment (path traversal)

    Accepted (returned unchanged): a site-relative path, optionally with a
    query string and/or fragment, e.g. ``"/dashboard?q=1"``.

    See #1819 (unvalidated mount URL -- path traversal / CRLF / log injection)
    and #1825 (encoded-traversal bypass: ``RequestFactory.get()`` percent-decodes
    once, so the ``..`` check decodes once here first).
    """
    if not url or not isinstance(url, str):
        return "/"
    # CRLF / log / header injection -- reject before any further parsing.
    if "\r" in url or "\n" in url:
        return "/"
    # Must be a site-relative absolute path. Rejects relative paths
    # ("../../admin/", "foo") and is the first half of the protocol-relative
    # ("//evil.com") rejection (completed by the netloc check below).
    if not url.startswith("/"):
        return "/"
    from urllib.parse import unquote, urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return "/"
    # Absolute ("https://evil.com/page") or protocol-relative ("//evil.com").
    if parsed.scheme or parsed.netloc:
        return "/"
    # Path traversal: reject any ".." path segment. ``RequestFactory.get()``
    # percent-DECODES the path once, so a raw-segment check on ``parsed.path``
    # would miss "/%2e%2e/admin/" -- which lands in ``request.path`` as
    # "/../admin/" after Django decodes it (#1825 encoded-traversal bypass).
    # Decode once here (matching RequestFactory's single decode) before the
    # segment check, and reject backslashes / control bytes that decode into
    # alternate separators or null bytes ("/..%5cadmin", "/foo/..%00/admin").
    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or any(ord(ch) < 0x20 for ch in decoded_path):
        return "/"
    if ".." in decoded_path.split("/"):
        return "/"
    return url


def _allowed_prefixes() -> list[str]:
    """The set of module prefixes a client view path may resolve under.

    ``settings.LIVEVIEW_ALLOWED_MODULES`` when set; otherwise the non-breaking
    fallback: every installed-app package root + ``"djust"``. See module
    docstring for the rationale (default-behavior change).
    """
    from django.conf import settings

    allowed = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", None) or []
    explicit = [entry for entry in allowed if isinstance(entry, str) and entry]
    if explicit:
        return explicit

    # Non-breaking fallback: the project's own apps + the framework package.
    # INSTALLED_APPS entries may be either a dotted app path ("myproj.blog") or
    # an AppConfig path ("myproj.blog.apps.BlogConfig"); we want the *package
    # root* the app's views live under. Use the top-level package segment so a
    # segment-boundary match still admits "myproj.blog.views.X". "djust" covers
    # the framework's own demo/test/built-in views.
    prefixes = {"djust"}
    for app in getattr(settings, "INSTALLED_APPS", []) or []:
        if isinstance(app, str) and app:
            root = app.split(".", 1)[0]
            if root:
                prefixes.add(root)
    return sorted(prefixes)


def _segment_match(module_path: str, entry: str) -> bool:
    """True iff ``module_path`` equals ``entry`` or is a dotted child of it.

    Segment-boundary match (NOT ``startswith``): ``"app"`` matches ``"app"`` and
    ``"app.views"`` but NOT ``"app_evil"`` / ``"apphacks"``. A trailing dot on
    the allowlist entry (``"app."`` — a common configuration style, e.g.
    ``LIVEVIEW_ALLOWED_MODULES = ["myapp."]``) is normalized away so it matches
    the same set as ``"app"`` rather than the empty set.
    """
    entry = entry.rstrip(".")
    if not entry:
        return False
    return module_path == entry or module_path.startswith(entry + ".")


def is_view_path_allowed(view_path: str) -> bool:
    """Allowlist gate (no import): may this client view path be resolved?

    Validates the path *shape* first, then matches the module portion against
    :func:`_allowed_prefixes` on a module-segment boundary. Returns ``False``
    (reject — do NOT import) otherwise. This runs BEFORE any import in
    :func:`resolve_view_class`; it is exported so callers can reject early with
    a clean frame before instantiation as defense-in-depth.
    """
    if not isinstance(view_path, str) or not _VIEW_PATH_RE.match(view_path):
        return False
    module_path = view_path.rsplit(".", 1)[0]
    if not module_path:
        return False
    return any(_segment_match(module_path, entry) for entry in _allowed_prefixes())


def resolve_view_class(view_path: str) -> ViewResolution:
    """Safely resolve a client-supplied dotted view path to a ``LiveView`` class.

    The single resolver shared by every mount entry point (WebSocket, SSE,
    ``ViewRuntime``). Order of operations — each step gates the next so no
    attacker-controlled import ever runs ahead of validation:

      1. **Shape** — reject anything that isn't ``module[.sub].ClassName`` of
         valid identifiers (catches ``..``, leading/trailing dots, bad chars)
         BEFORE touching the import machinery.
      2. **Allowlist (before import)** — the module must match
         ``LIVEVIEW_ALLOWED_MODULES`` (if set) or an installed-app root + djust
         (fallback), on a module-segment boundary.
      3. **Import** — ``importlib.import_module(module_path)`` (no ``fromlist``).
      4. **Class lookup** — ``vars(module).get(class_name)`` (``__dict__``, not
         ``getattr``) so a module-level ``__getattr__`` (PEP 562) cannot be
         triggered by the client class name.
      5. **Subclass check (defense-in-depth)** — the resolved object must be a
         ``LiveView`` subclass.

    Returns a :class:`ViewResolution`. On success it is truthy and carries
    ``view_class``. On any rejection it is falsy and carries a verbose ``detail``
    (DEBUG-only) plus the production-safe ``generic`` the caller passes to its
    existing ``_safe_error(detail, generic)`` envelope — preserving each
    transport's current error UX.
    """
    if not isinstance(view_path, str) or not view_path:
        return ViewResolution(detail="Missing view path", generic="View not found")

    # (1) shape + (2) allowlist — neither imports anything.
    if not is_view_path_allowed(view_path):
        return ViewResolution(
            detail=f"View {view_path} is not allowed",
            generic="View not found",
        )

    # (3) import + (4) class lookup — only reached after the allowlist passed.
    module_path, class_name = view_path.rsplit(".", 1)
    import importlib

    try:
        # import_module (NO fromlist), NOT __import__(fromlist=[class_name]):
        # __import__'s fromlist does hasattr(module, class_name), which triggers
        # a module-level __getattr__ (PEP 562) / submodule import for a
        # client-controlled class_name (GHSA-7prp-2623-8g45 follow-up).
        module = importlib.import_module(module_path)
    except (ImportError, ValueError) as exc:
        return ViewResolution(
            detail=f"Failed to import module {module_path}: {exc}",
            generic="View not found",
        )

    # __dict__ lookup, NOT getattr — a client-controlled class_name must not
    # trigger a module-level __getattr__ (PEP 562), which on some already-loaded
    # modules imports a submodule. Real LiveView classes are top-level-defined /
    # imported into the module, so they live in __dict__; a name served only by
    # __getattr__ resolves to None and is rejected as "class not found".
    view_class = vars(module).get(class_name)
    if view_class is None:
        return ViewResolution(
            detail=f"Class {class_name} not found in module {module_path}",
            generic="View not found",
        )

    # (5) defense-in-depth: must be a LiveView subclass.
    from ..live_view import LiveView

    if not (isinstance(view_class, type) and issubclass(view_class, LiveView)):
        return ViewResolution(
            detail=(
                f"Security: {view_path} is not a LiveView subclass. "
                f"Only LiveView classes can be mounted."
            ),
            generic="Invalid view class",
        )

    return ViewResolution(view_class=view_class)


def available_liveview_names(view_path: str) -> Optional[list[str]]:
    """DEBUG-only hint: the ``LiveView`` class names defined in the path's module.

    Returns ``None`` when the module cannot be inspected. Enumerates via
    ``vars(module)`` (``__dict__``), NOT ``inspect.getmembers`` / ``getattr``, so
    a module-level ``__getattr__`` (PEP 562) is never triggered during the error
    hint (GHSA-7prp-2623-8g45 follow-up). Only call this once the allowlist /
    import has already succeeded for ``view_path``.
    """
    try:
        module_path = view_path.rsplit(".", 1)[0]
        import sys

        module = sys.modules.get(module_path)
        if module is None:
            return None
        from ..live_view import LiveView

        names = [
            name
            for name, obj in vars(module).items()
            if isinstance(obj, type) and issubclass(obj, LiveView) and obj is not LiveView
        ]
        return sorted(names) or None
    except Exception:
        return None
