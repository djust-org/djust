"""
URL routing helpers for djust LiveView.

Provides live_session() for grouping views that share a WebSocket connection,
DjustMiddlewareStack for apps without django.contrib.auth,
and emitting a client-side route map for live_redirect navigation.
"""

from typing import Any, List, Optional

from django.urls import URLPattern, path
from django.urls.resolvers import RegexPattern, RoutePattern
from django.utils.html import format_html
from django.utils.safestring import mark_safe


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
            # e.g., <int:id> → :id
            import re

            js_path = re.sub(r"<(?:\w+:)?(\w+)>", r":\1", full_path)
            route_map_entries.append((js_path, view_path))

        # Create new pattern with prefix
        # Strip leading / for Django's path() which doesn't want it
        django_route = f"{prefix.lstrip('/')}/{clean_route}".lstrip("/")
        new_pattern = path(django_route, pattern.callback, pattern.default_args, pattern.name)
        result.append(new_pattern)

    # Store route map entries for the template tag to emit
    if not hasattr(live_session, "_route_maps"):
        live_session._route_maps = {}

    session_key = session_name or prefix
    live_session._route_maps[session_key] = route_map_entries

    return result


def get_route_map_script() -> str:
    """
    Return a <script> tag that populates window.djust._routeMap.

    Include this in your base template (or use the {% djust_route_map %} tag).
    """
    import json

    route_maps = getattr(live_session, "_route_maps", {})
    all_routes = {}
    for entries in route_maps.values():
        for js_path, view_path in entries:
            all_routes[js_path] = view_path

    if not all_routes:
        return ""

    route_json = json.dumps(all_routes)
    return format_html(
        "<script>window.djust=window.djust||{{}};window.djust._routeMap={};</script>",
        mark_safe(route_json),  # json.dumps escapes <, >, quotes; data is developer-defined
    )
