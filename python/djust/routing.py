"""
URL routing helpers for djust LiveView.

Provides live_session() for grouping views that share a WebSocket connection,
DjustMiddlewareStack for apps without django.contrib.auth,
and emitting a client-side route map for live_redirect navigation.
"""

import re
from typing import Any, Dict, List, Optional

from django.urls import URLPattern, path
from django.urls.resolvers import RegexPattern, RoutePattern, URLResolver
from django.utils.html import format_html
from django.utils.safestring import mark_safe

# Django ``<int:id>`` / ``<slug:x>`` → JS-friendly ``:id`` / ``:x``. Shared by
# ``live_session`` and ``build_route_map_from_urlconf`` so both engines emit the
# identical client-side route shape.
_PARAM_RE = re.compile(r"<(?:\w+:)?(\w+)>")

# Module-level cache for the URLconf-derived route map. The URLconf is static at
# runtime, so we derive once and reuse. ``_reset_route_map_cache()`` clears it
# (used by tests that swap ROOT_URLCONF / FORCE_SCRIPT_NAME via override_settings).
# Keyed by (root_urlconf, script_prefix) so FORCE_SCRIPT_NAME / sub-path mounts
# (which production sets per-request via set_script_prefix) don't collide.
_route_map_cache: Dict[Any, Dict[str, str]] = {}

# Parallel cache, populated by the SAME URLconf walk as the route map:
# ``js_path -> {"login": bool, "perms": tuple}`` for GATED routes only (public
# routes are absent → "not in gating" means public). Used to auth-filter the
# client-emitted map so anonymous/unauthorized clients can't enumerate
# login/permission-gated routes or their view-class paths (#1758).
_route_gating_cache: Dict[Any, Dict[str, dict]] = {}


def _reset_route_map_cache() -> None:
    """Clear the cached URLconf-derived route map (and its gating sidecar).

    The cache assumes the URLconf is static at runtime. Tests that swap
    ``ROOT_URLCONF`` or ``FORCE_SCRIPT_NAME`` via ``override_settings`` must
    call this between cases so a stale map doesn't leak across tests.
    """
    _route_map_cache.clear()
    _route_gating_cache.clear()


def DjustMiddlewareStack(inner: Any, *, validate_origin: bool = True) -> Any:
    """
    ASGI middleware stack for djust that doesn't require django.contrib.auth.

    Use this instead of ``channels.auth.AuthMiddlewareStack`` when your app
    doesn't need authentication. It wraps the inner application with session
    middleware (so ``request.session`` works, but ``request.user`` is not
    populated) and, by default, with
    ``channels.security.websocket.AllowedHostsOriginValidator`` to prevent
    Cross-Site WebSocket Hijacking (CSWSH, #653).

    Args:
        inner: The ASGI application to wrap (typically a ``URLRouter``).
        validate_origin: When True (the default), the returned stack will
            also wrap ``inner`` in ``AllowedHostsOriginValidator``, which
            rejects WebSocket handshakes whose ``Origin`` header is not in
            ``settings.ALLOWED_HOSTS``. Set to False to opt out — NOT
            recommended; only use this for non-browser clients that you
            control end-to-end, or when an upstream proxy already strips
            hostile Origin headers.

    Note:
        ``channels.security.websocket.AllowedHostsOriginValidator`` snapshots
        ``settings.ALLOWED_HOSTS`` at the moment this function runs (i.e. at
        ASGI-application construction time). If you change ``ALLOWED_HOSTS``
        at runtime (e.g. via ``django.test.override_settings`` in tests),
        only the consumer-level ``_is_allowed_origin`` check in
        ``LiveViewConsumer.connect()`` will see the new value; the
        middleware-level validator will keep using the value it captured
        when the stack was built. This matters for tests — prefer the
        consumer-level check over asserting on the middleware in
        override-settings tests.

    Example::

        from djust.routing import DjustMiddlewareStack

        application = ProtocolTypeRouter({
            "http": get_asgi_application(),
            "websocket": DjustMiddlewareStack(
                URLRouter(websocket_urlpatterns)
            ),
        })
    """
    from channels.sessions import SessionMiddlewareStack

    stack = SessionMiddlewareStack(inner)
    if validate_origin:
        # Lazy import keeps the top-level import surface of djust.routing
        # stable (channels.security is importable whenever channels itself
        # is, so the import cost is only paid when the stack is built).
        from channels.security.websocket import AllowedHostsOriginValidator

        stack = AllowedHostsOriginValidator(stack)
    return stack


def live_session(
    prefix: str,
    patterns: List[URLPattern],
    session_name: Optional[str] = None,
) -> List[URLPattern]:
    """
    Group LiveView URL patterns into a live session.

    Views within a live_session share the same WebSocket connection.
    Navigating between them via live_redirect() doesn't disconnect/reconnect.

    This function:
    1. Prefixes all URL patterns with the given prefix.
    2. Registers the view paths in a global route map so the client-side
       JS can resolve URL paths to view classes for live_redirect.

    Args:
        prefix: URL prefix (e.g. "/app"). No trailing slash.
        patterns: List of Django URL patterns (each pointing to a LiveView).
        session_name: Optional name for the session group.

    Returns:
        List of URL patterns to include in urlpatterns.

    Example::

        from djust.routing import live_session
        from django.urls import path

        urlpatterns = [
            *live_session("/app", [
                path("", DashboardView.as_view(), name="dashboard"),
                path("settings/", SettingsView.as_view(), name="settings"),
                path("items/<int:id>/", ItemDetailView.as_view(), name="item_detail"),
            ]),
        ]
    """
    # Normalize prefix
    prefix = prefix.rstrip("/")
    if not prefix.startswith("/"):
        prefix = "/" + prefix

    result = []
    route_map_entries = []

    for pattern in patterns:
        # Get the original route string
        # Use isinstance() to differentiate between RoutePattern (path()) and RegexPattern (re_path())
        # RoutePattern._route and RegexPattern._regex are the raw strings without anchors/suffixes
        # This matches Django Channels' approach and has been stable since Django 2.0
        if isinstance(pattern.pattern, RegexPattern):
            route_str = pattern.pattern._regex
            # Regex patterns may have ^ and $ anchors
            clean_route = route_str.lstrip("^").rstrip("$")
        elif isinstance(pattern.pattern, RoutePattern):
            route_str = pattern.pattern._route
            # RoutePattern._route doesn't have anchors, use as-is
            clean_route = route_str
        else:
            # Fallback for custom pattern classes
            route_str = str(pattern.pattern)
            clean_route = route_str.lstrip("^").rstrip("$")

        # Build the full URL path
        # Django path patterns use <type:name> syntax
        full_path = f"{prefix}/{clean_route}".replace("//", "/")

        # Extract the view class path for the route map
        view_cls = None
        if hasattr(pattern, "callback"):
            cb = pattern.callback
            if hasattr(cb, "view_class"):
                view_cls = cb.view_class
            elif hasattr(cb, "__wrapped__"):
                view_cls = getattr(cb.__wrapped__, "view_class", None)

        if view_cls:
            view_path = f"{view_cls.__module__}.{view_cls.__qualname__}"
            # Convert Django URL params to JS-friendly format
            # e.g., <int:id> → :id  (shared _PARAM_RE; see module top)
            js_path = _PARAM_RE.sub(r":\1", full_path)
            route_map_entries.append((js_path, view_path))

        # Create new pattern with prefix
        # Strip leading / for Django's path() which doesn't want it
        django_route = f"{prefix.lstrip('/')}/{clean_route}".lstrip("/")
        new_pattern = path(django_route, pattern.callback, pattern.default_args, pattern.name)
        result.append(new_pattern)

    # Store route map entries for the template tag to emit. ``live_session`` is
    # a module-level function used as an attribute namespace here; ``setattr`` /
    # ``getattr`` keep the dynamic-attribute access type-clean.
    route_maps: Dict[str, Any] = getattr(live_session, "_route_maps", {})
    session_key = session_name or prefix
    route_maps[session_key] = route_map_entries
    setattr(live_session, "_route_maps", route_maps)

    return result


def _resolve_view_class(pattern: URLPattern) -> Optional[type]:
    """Return the view class behind a ``URLPattern`` callback, or ``None``.

    Mirrors the resolution logic in :func:`live_session` (routing.py): handles
    both Django CBV ``as_view()`` (``callback.view_class``) and decorator-wrapped
    callbacks such as ``login_required(View.as_view())`` whose original view is
    reachable via ``callback.__wrapped__.view_class`` (functools.wraps).
    """
    cb = getattr(pattern, "callback", None)
    if cb is None:
        return None
    if hasattr(cb, "view_class"):
        view_class: Optional[type] = cb.view_class
        return view_class
    wrapped = getattr(cb, "__wrapped__", None)
    if wrapped is not None:
        return getattr(wrapped, "view_class", None)
    return None


def _view_auth_requirement(pattern: URLPattern, view_cls: type) -> Optional[dict]:
    """Return a LiveView route's auth requirement, or ``None`` if it is public.

    Detects gating from (a) the djust ``LiveView`` class attrs ``login_required``
    / ``permission_required`` (see ``live_view.py``), and (b) a decorator-gated
    callback such as ``login_required(View.as_view())`` — which presents as a
    callback with ``__wrapped__`` but no direct ``view_class``. Returns
    ``{"login": bool, "perms": tuple[str, ...]}``; ``perms`` implies login.
    Used to auth-filter the client route map (#1758).
    """
    login = getattr(view_cls, "login_required", None) is True
    perms_attr = getattr(view_cls, "permission_required", None)
    if isinstance(perms_attr, str):
        perms: tuple = (perms_attr,)
    elif perms_attr:
        perms = tuple(perms_attr)
    else:
        perms = ()
    # Decorator-gated callback, e.g. ``login_required(View.as_view())``.
    # functools.wraps copies ``view_class`` onto the wrapper, so its presence
    # can't distinguish a wrapped from a bare callback. The reliable signal:
    # a decorator AROUND as_view() has ``cb.__wrapped__`` that is itself the
    # as_view result (carrying ``view_class``), whereas a *bare* as_view's
    # ``__wrapped__`` is ``cls.dispatch`` (no ``view_class``). Treat any such
    # wrap as login-gated — fail closed (we can't prove the decorator is
    # auth-related, so an unauthorized client doesn't get the route).
    cb = getattr(pattern, "callback", None)
    inner = getattr(cb, "__wrapped__", None)
    if inner is not None and getattr(inner, "view_class", None) is not None:
        login = True
    # Django's class-based auth mixins gate via dispatch and set no class attr
    # (LoginRequiredMixin) — detect them by MRO name so a view gated only that
    # way isn't leaked. (PermissionRequiredMixin uses the same
    # ``permission_required`` attr already read above.) Detection is by name to
    # avoid importing django.contrib.auth at module load. NOTE: a purely
    # runtime gate — a view that only overrides ``check_permissions()`` /
    # ``mount()`` with no attr or mixin — cannot be detected statically here;
    # such a route would still appear in the map (it remains access-protected
    # at mount). Prefer the ``login_required`` / ``permission_required`` attrs.
    mro_names = {c.__name__ for c in getattr(view_cls, "__mro__", ())}
    if "LoginRequiredMixin" in mro_names or "PermissionRequiredMixin" in mro_names:
        login = True
    if login or perms:
        return {"login": True, "perms": perms}
    return None


def _user_satisfies(request: Any, requirement: dict) -> bool:
    """True iff ``request.user`` satisfies a gated route's auth requirement.

    Fails closed (#1758): a missing request, missing user, or unauthenticated
    user never satisfies a gated route, so gated routes are omitted from the
    client map for anonymous/unknown callers. Permission checks use
    ``user.has_perms`` (all required perms).
    """
    user = getattr(request, "user", None) if request is not None else None
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    perms = requirement.get("perms") or ()
    if perms and not user.has_perms(perms):
        return False
    return True


def _pattern_route(pattern: Any) -> str:
    """Return the raw route string for a URLPattern/URLResolver.

    Uses the same isinstance idioms as :func:`live_session` so ``path()`` and
    ``re_path()`` patterns are both handled.
    """
    p = pattern.pattern
    if isinstance(p, RegexPattern):
        return str(p._regex).lstrip("^").rstrip("$")
    if isinstance(p, RoutePattern):
        return str(p._route)
    return str(p).lstrip("^").rstrip("$")


def _walk_liveview_routes(
    patterns: List[Any],
    prefix: str,
    route_map: Dict[str, str],
    gating: Optional[Dict[str, dict]] = None,
) -> None:
    """Recursively collect ``{js_path: "module.QualName"}`` for LiveView routes.

    Descends into ``URLResolver`` includes, accumulating the route prefix. For
    every ``URLPattern`` whose callback resolves to a :class:`~djust.LiveView`
    subclass, adds an entry keyed by the JS-friendly path (``<int:id>`` → ``:id``).
    When ``gating`` is provided, also records the auth requirement of any GATED
    route into it (public routes are left absent) for the #1758 auth filter.
    """
    # Imported here (not at module top) to avoid a circular import: live_view
    # imports from routing-adjacent modules at package init time.
    from djust import LiveView

    for pattern in patterns:
        route = _pattern_route(pattern)
        full = (prefix + route).replace("//", "/")
        if isinstance(pattern, URLResolver):
            _walk_liveview_routes(pattern.url_patterns, full, route_map, gating)
            continue
        view_cls = _resolve_view_class(pattern)
        if view_cls is None:
            continue
        try:
            is_liveview = issubclass(view_cls, LiveView)
        except TypeError:
            is_liveview = False
        if not is_liveview:
            continue
        # Ensure a leading slash, then convert Django params to ``:name``.
        js_path = full if full.startswith("/") else "/" + full
        js_path = _PARAM_RE.sub(r":\1", js_path)
        view_path = f"{view_cls.__module__}.{view_cls.__qualname__}"
        route_map[js_path] = view_path
        if gating is not None:
            requirement = _view_auth_requirement(pattern, view_cls)
            if requirement is not None:
                gating[js_path] = requirement


def build_route_map_from_urlconf(urlconf: Any = None) -> Dict[str, str]:
    """Derive the client route map by walking the Django URLconf.

    Walks ``django.urls.get_resolver(urlconf).url_patterns`` recursively,
    descending into ``include()`` resolvers, and returns
    ``{js_path: "module.QualName"}`` for every route whose callback resolves to
    a :class:`~djust.LiveView` subclass. This is what powers zero-wiring
    ``dj-navigate`` (#1733, ADR-021 Stage 1) — no ``live_session()`` required.

    Django URL params are converted to the JS-friendly form (``<int:id>`` →
    ``:id``), and the ``FORCE_SCRIPT_NAME`` / sub-path mount prefix is applied
    via :func:`django.urls.get_script_prefix` so sub-path deploys resolve the
    same paths the browser sees (mirrors ``_resolve_api_prefix`` in
    ``live_tags.py``).

    The result is cached at module level keyed by ``(root_urlconf,
    script_prefix)`` (the URLconf is static at runtime). Call
    :func:`_reset_route_map_cache` to clear it (used by tests).

    Args:
        urlconf: Optional URLconf module/string passed to ``get_resolver``.
            Defaults to ``settings.ROOT_URLCONF``.

    Returns:
        A ``dict`` mapping JS route paths to dotted view paths. Empty when the
        app has no LiveView routes (so the emitting tag can stay empty-safe).
    """
    from django.conf import settings
    from django.urls import get_resolver, get_script_prefix

    script_prefix = get_script_prefix().rstrip("/")
    # Resolve None → the actual ROOT_URLCONF for the cache key. get_resolver(None)
    # internally resolves to settings.ROOT_URLCONF, so caching on the raw arg
    # would (a) give two callers passing None vs the explicit ROOT_URLCONF
    # different cache entries for identical work, and (b) make override_settings
    # tests (which swap ROOT_URLCONF but pass urlconf=None) collide on a single
    # (None, prefix) key — a cross-test pollution hazard. Key on the resolved
    # value instead.
    resolved_urlconf = urlconf if urlconf is not None else getattr(settings, "ROOT_URLCONF", None)
    cache_key = (resolved_urlconf, script_prefix)
    cached = _route_map_cache.get(cache_key)
    if cached is not None:
        return cached

    resolver = get_resolver(urlconf)
    route_map: Dict[str, str] = {}
    gating: Dict[str, dict] = {}
    _walk_liveview_routes(resolver.url_patterns, script_prefix + "/", route_map, gating)

    _route_map_cache[cache_key] = route_map
    _route_gating_cache[cache_key] = gating
    return route_map


def _route_gating_from_urlconf(urlconf: Any = None) -> Dict[str, dict]:
    """Return the auth-requirement sidecar (``js_path -> requirement``) for
    gated routes, populated by the same cached walk as
    :func:`build_route_map_from_urlconf` (#1758). Public routes are absent.
    """
    from django.conf import settings
    from django.urls import get_script_prefix

    script_prefix = get_script_prefix().rstrip("/")
    resolved_urlconf = urlconf if urlconf is not None else getattr(settings, "ROOT_URLCONF", None)
    cache_key = (resolved_urlconf, script_prefix)
    if cache_key not in _route_gating_cache:
        # Populating the route map populates the gating sidecar too.
        build_route_map_from_urlconf(urlconf)
    return _route_gating_cache.get(cache_key, {})


def get_route_map_script(request: Any = None) -> str:
    """
    Return a ``<script>`` tag that populates ``window.djust._routeMap``.

    As of #1733 (ADR-021 Stage 1) the route map is **auto-emitted** by
    ``{% djust_client_config %}`` (the tag already present in every scaffolded
    base ``<head>``), so ``dj-navigate`` works with zero wiring. This function
    remains available for manual / custom emission (e.g. a base template that
    does not use ``{% djust_client_config %}``).

    The emitted map merges the routes auto-derived from the URLconf
    (:func:`build_route_map_from_urlconf` — every ``LiveView`` route) with any
    entries registered by :func:`live_session`. The two sources are an
    idempotent union (live_session entries win on key collision; for shared
    routes the values are equal).

    Args:
        request: An optional Django ``HttpRequest``. When set and
            ``request.csp_nonce`` is available (django-csp with
            ``CSP_INCLUDE_NONCE_IN``), the emitted ``<script>`` carries a
            ``nonce`` attribute so apps can drop ``'unsafe-inline'`` from
            their CSP ``script-src`` (see #655). When no nonce is
            available, the script is emitted without a nonce attribute —
            backward compatible with apps still allowing
            ``'unsafe-inline'``.
    """
    import json

    # Auto-derived URLconf routes (zero-wiring, #1733) merged with any
    # live_session registrations. live_session entries are applied last so
    # they win on key collision (values are equal for shared routes).
    all_routes: Dict[str, str] = dict(build_route_map_from_urlconf())
    route_maps = getattr(live_session, "_route_maps", {})
    for entries in route_maps.values():
        for js_path, view_path in entries:
            all_routes[js_path] = view_path

    # Auth-filter (#1758): never emit a login/permission-gated route — or its
    # view-class path — to a client that can't access it. Public routes (absent
    # from the gating sidecar) always emit; gated routes emit only when
    # request.user satisfies them (fails closed for anonymous/unknown callers).
    gating = _route_gating_from_urlconf()
    if gating:
        all_routes = {
            js_path: view_path
            for js_path, view_path in all_routes.items()
            if js_path not in gating or _user_satisfies(request, gating[js_path])
        }

    if not all_routes:
        return ""

    route_json = json.dumps(all_routes)
    # CSP nonce support (#655)
    from .utils import get_csp_nonce

    # Safety note: json.dumps escapes ", \, and control chars — it does NOT
    # escape angle brackets, so a literal "</script>" / "<!--" would pass
    # through verbatim. That is safe HERE only because the route data is
    # developer-defined URL config (view module paths + URL patterns), which
    # structurally cannot contain those script-breakout sequences. The
    # route_json is therefore mark_safe()'d for interpolation inside the
    # <script> body; the nonce is escaped by format_html. (Do NOT mark_safe
    # user-derived data into a <script> body — that WOULD be an XSS surface.)
    nonce = get_csp_nonce(request)
    if nonce:
        return str(
            format_html(
                '<script nonce="{}">window.djust=window.djust||{{}};window.djust._routeMap={};</script>',
                nonce,
                mark_safe(route_json),  # developer-defined URL config; see safety note above
            )
        )
    return str(
        format_html(
            "<script>window.djust=window.djust||{{}};window.djust._routeMap={};</script>",
            mark_safe(route_json),  # developer-defined URL config; see safety note above
        )
    )
