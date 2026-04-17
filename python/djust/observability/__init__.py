"""
AI-observability module for djust. Exposes live LiveView state via
HTTP endpoints so the djust Python MCP (running in a separate process
from Django) can introspect sessions, tracebacks, timings, and logs
without shared memory.

Security: all endpoints are DEBUG-gated + localhost-only. This matches
django-debug-toolbar's model. In production (DEBUG=False), the URL
include registers no routes and the module is effectively inert.

Usage (from a project's urls.py):

    from django.conf import settings
    if settings.DEBUG:
        urlpatterns = [
            path("_djust/", include("djust.observability.urls")),
            # ... your routes
        ]

Subsequent Phase 7.x items add endpoints under the base
"/_djust/observability/" prefix. This module provides only the
foundation (registry + middleware + base URL include).
"""

from djust.observability.registry import (
    get_registered_session_count,
    get_view_for_session,
    register_view,
    unregister_view,
)

__all__ = [
    "get_registered_session_count",
    "get_view_for_session",
    "register_view",
    "unregister_view",
]
