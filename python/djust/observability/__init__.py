"""
AI-observability module for djust. Exposes live LiveView state via
HTTP endpoints so the djust Python MCP (running in a separate process
from Django) can introspect sessions, tracebacks, timings, and logs
without shared memory.

Security: all endpoints are DEBUG-gated AND localhost-only. The localhost
check is enforced IN every view (``views._gate``), so the boundary holds even
if the middleware below is not installed; ``LocalhostOnlyObservabilityMiddleware``
is an additional outer layer. In production (DEBUG=False) every endpoint 404s.
This matches django-debug-toolbar's model.

Usage (from a project's urls.py + settings.py):

    # urls.py
    from django.conf import settings
    if settings.DEBUG:
        urlpatterns = [
            path("_djust/", include("djust.observability.urls")),
            # ... your routes
        ]

    # settings.py — recommended outer layer (the in-view gate is the
    # authoritative check; this rejects non-localhost before the view runs):
    if DEBUG:
        MIDDLEWARE.insert(
            0, "djust.observability.middleware.LocalhostOnlyObservabilityMiddleware"
        )

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
