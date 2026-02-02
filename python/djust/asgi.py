"""
djust.asgi — ASGI application helper

Provides a one-liner to create a fully configured ASGI application with:
- Static files serving via ASGIStaticFilesHandler (DEBUG mode)
- WebSocket routing for LiveView
- HTTP via Django's ASGI handler

Usage::

    # myproject/asgi.py
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

    from djust.asgi import get_application
    application = get_application()
"""

import os
import logging

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.urls import path

logger = logging.getLogger(__name__)


def get_application(websocket_path="/ws/live/", use_auth_middleware=True):
    """Return a fully-configured ASGI application for a djust project.

    This replaces the ~15 lines of boilerplate typically needed in asgi.py.

    Args:
        websocket_path: URL path for the LiveView WebSocket endpoint.
            Defaults to ``/ws/live/``. Must include trailing slash.
        use_auth_middleware: Wrap WebSocket routing with
            ``AuthMiddlewareStack`` for session/cookie auth. Defaults to True.

    Returns:
        A ``ProtocolTypeRouter`` ASGI application, optionally wrapped with
        ``ASGIStaticFilesHandler`` when ``DEBUG=True``.

    Example::

        from djust.asgi import get_application
        application = get_application()

        # Custom WebSocket path:
        application = get_application(websocket_path="/ws/my-live/")
    """
    from djust.websocket import LiveViewConsumer

    # Build WebSocket routing
    ws_routing = URLRouter([
        path(websocket_path.lstrip("/"), LiveViewConsumer.as_asgi()),
    ])

    if use_auth_middleware:
        ws_routing = AuthMiddlewareStack(ws_routing)

    # Build the base HTTP handler
    http_application = get_asgi_application()

    # Wrap with static files handler in DEBUG mode
    try:
        from django.conf import settings
        if getattr(settings, "DEBUG", False):
            from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
            http_application = ASGIStaticFilesHandler(http_application)
            logger.debug("[djust] Static files served via ASGIStaticFilesHandler (DEBUG=True)")
    except Exception:
        # If settings aren't configured yet, skip static wrapping
        pass

    application = ProtocolTypeRouter({
        "http": http_application,
        "websocket": ws_routing,
    })

    return application
