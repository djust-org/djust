"""Compatibility shim — superseded by ``djust.security.mount`` (F22 + F23).

This module historically held the fail-closed view-import gate. The mount-path
consolidation (F22 + F23) moved the single source of truth to
``djust.security.mount`` so the WebSocket, SSE, and ``ViewRuntime`` paths share
one resolver and cannot drift (#1646).

``is_view_import_allowed`` is kept here as a thin alias delegating to
``djust.security.mount.is_view_path_allowed`` so any out-of-tree caller keeps
working AND automatically gets the new, more principled allowlist semantics:

  * The allowed module prefixes are ``settings.LIVEVIEW_ALLOWED_MODULES`` when
    set, ELSE a non-breaking fallback of the project's own installed-app
    package roots + ``"djust"``.
  * Matching is module-segment-boundary (``path == entry`` or
    ``path.startswith(entry + ".")``), never bare ``startswith``.

This replaces the previous ``sys.modules``-membership rule (allow if the module
was already imported). The new rule is independent of import order: a path is
allowed iff it is shape-valid AND matches the allowlist (explicit or the
INSTALLED_APPS fallback) — closing the arbitrary-import primitive without a
default-open hole.
"""

from __future__ import annotations

from .security.mount import is_view_path_allowed


def is_view_import_allowed(view_path: str) -> bool:
    """Deprecated alias for :func:`djust.security.mount.is_view_path_allowed`.

    Kept for backward compatibility. See module docstring for the (new)
    allowlist semantics.
    """
    return is_view_path_allowed(view_path)
