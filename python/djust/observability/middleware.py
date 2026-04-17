"""
Localhost-only gate for the observability endpoint.

These endpoints expose live server state (view assigns, tracebacks,
SQL queries, logs). In DEBUG mode that's acceptable for developer
introspection but we don't want them reachable from the LAN even if
DEBUG slips through to a non-production environment.

The check runs for every request whose path starts with the
OBSERVABILITY_URL_PREFIX. Non-localhost clients get a 403 regardless
of ALLOWED_HOSTS.
"""

from __future__ import annotations

import logging

from django.http import HttpResponseForbidden

logger = logging.getLogger("djust.observability")

OBSERVABILITY_URL_PREFIX = "/_djust/observability/"

_LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}


def _client_ip(request) -> str:
    """Extract the client IP. In dev this is reliable (no proxy chain)."""
    return request.META.get("REMOTE_ADDR", "")


def is_localhost(request) -> bool:
    """True iff the request came from loopback."""
    return _client_ip(request) in _LOCALHOST_ADDRS


class LocalhostOnlyObservabilityMiddleware:
    """Reject non-localhost requests to /_djust/observability/.

    Install in MIDDLEWARE **before** any auth middleware so unauthenticated
    but localhost-origin MCP calls succeed (auth isn't the security
    boundary here; network location is).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(OBSERVABILITY_URL_PREFIX) and not is_localhost(request):
            logger.warning(
                "Rejected observability request from non-localhost: path=%s ip=%s",
                request.path,
                _client_ip(request),
            )
            return HttpResponseForbidden("Observability endpoints are localhost-only.")
        return self.get_response(request)
