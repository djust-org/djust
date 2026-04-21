"""djust HTTP API transport (ADR-008).

Opt-in HTTP+JSON transport for ``@event_handler(expose_api=True)`` handlers.

The same handler runs with identical validation, permissions, and rate limiting
regardless of transport. This package is a thin adapter over the existing
WebSocket handler pipeline; it does not duplicate any safety check.

See docs/adr/008-auto-generated-http-api-from-event-handlers.md for design rationale.
"""

from djust.api.auth import BaseAuth, SessionAuth
from djust.api.dispatch import DjustAPIDispatchView, dispatch_api
from djust.api.openapi import OpenAPISchemaView, build_schema
from djust.api.registry import (
    get_api_view_registry,
    iter_exposed_handlers,
    register_api_view,
    resolve_api_view,
)
from djust.api.urls import api_patterns, default_api_urlpatterns

__all__ = [
    "BaseAuth",
    "SessionAuth",
    "DjustAPIDispatchView",
    "dispatch_api",
    "OpenAPISchemaView",
    "build_schema",
    "register_api_view",
    "resolve_api_view",
    "get_api_view_registry",
    "iter_exposed_handlers",
    "api_patterns",
    "default_api_urlpatterns",
]
