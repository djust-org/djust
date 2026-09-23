"""
Access checks for the observability endpoints.

These endpoints expose live server state (view assigns, tracebacks,
SQL queries, logs). In DEBUG mode that's acceptable for developer
introspection but we don't want them reachable from the LAN even if
DEBUG slips through to a non-production environment.

A request is served only when all of these hold (see ``views._gate``):

* ``settings.DEBUG`` is on;
* it arrived straight from a loopback peer: ``REMOTE_ADDR`` is loopback
  AND it carries none of the headers a reverse proxy adds
  (``X-Forwarded-For``, ``Forwarded``, ``X-Real-IP``, ``X-Forwarded-Host``,
  ``X-Forwarded-Proto``). A proxy on the same host connects from loopback,
  so ``REMOTE_ADDR`` alone cannot tell a local client from a proxied one;
* it carries the project's observability token in the
  ``X-Djust-Observability-Token`` header. The token is derived from
  ``SECRET_KEY`` (or taken from the ``DJUST_OBSERVABILITY_TOKEN`` environment
  variable when set), so local tooling that loads the project's settings --
  the djust MCP server started with ``manage.py djust_mcp`` -- sends it
  automatically, while a client that can only reach the port cannot. Other
  tools can print it with ``python manage.py djust_observability_token``.

The middleware below applies the network-location check for every request
whose path starts with OBSERVABILITY_URL_PREFIX (403 on refusal), before
any view runs.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Callable

from django.http import HttpResponseForbidden

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger("djust.observability")

OBSERVABILITY_URL_PREFIX = "/_djust/observability/"

_LOCALHOST_ADDRS = {"127.0.0.1", "::1", "localhost"}

# Headers a reverse proxy adds. A request carrying any of them was relayed,
# whatever its REMOTE_ADDR says.
_PROXY_HEADERS = (
    "HTTP_X_FORWARDED_FOR",
    "HTTP_FORWARDED",
    "HTTP_X_REAL_IP",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PROTO",
)

TOKEN_HEADER = "X-Djust-Observability-Token"
TOKEN_META_KEY = "HTTP_X_DJUST_OBSERVABILITY_TOKEN"
TOKEN_ENV_VAR = "DJUST_OBSERVABILITY_TOKEN"
_TOKEN_SALT = "djust.observability.token"


def _client_ip(request: "HttpRequest") -> str:
    """The socket peer address (``REMOTE_ADDR``)."""
    ip: str = request.META.get("REMOTE_ADDR", "")
    return ip


def is_localhost(request: "HttpRequest") -> bool:
    """True iff the socket peer is loopback."""
    return _client_ip(request) in _LOCALHOST_ADDRS


def has_proxy_headers(request: "HttpRequest") -> bool:
    """True if the request carries any header a reverse proxy adds."""
    return any(request.META.get(key) for key in _PROXY_HEADERS)


def is_direct_local_request(request: "HttpRequest") -> bool:
    """True iff the peer is loopback and the request was not relayed by a proxy."""
    return is_localhost(request) and not has_proxy_headers(request)


def get_observability_token() -> str:
    """Return the token the observability endpoints expect.

    ``DJUST_OBSERVABILITY_TOKEN`` from the environment when set, otherwise an
    HMAC of ``SECRET_KEY`` -- stable across restarts and worker processes of
    the same project, and computable by any process that loads its settings.
    """
    from_env = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if from_env:
        return from_env

    from django.conf import settings
    from django.utils.crypto import salted_hmac

    token: str = salted_hmac(
        _TOKEN_SALT, "observability-access", secret=settings.SECRET_KEY, algorithm="sha256"
    ).hexdigest()
    return token


def has_valid_token(request: "HttpRequest") -> bool:
    """True iff the request carries the observability token."""
    from django.utils.crypto import constant_time_compare

    supplied = request.META.get(TOKEN_META_KEY, "")
    if not supplied:
        return False
    try:
        expected = get_observability_token()
    except Exception:  # noqa: BLE001 - e.g. an empty SECRET_KEY: refuse
        logger.warning("Observability token unavailable; refusing request", exc_info=True)
        return False
    return bool(constant_time_compare(supplied, expected))


class LocalhostOnlyObservabilityMiddleware:
    """Reject requests to /_djust/observability/ that are not direct local requests.

    Install in MIDDLEWARE **before** any auth middleware so unauthenticated
    local MCP calls succeed (auth isn't the access check here; network
    location and the observability token are).
    """

    def __init__(self, get_response: Callable[["HttpRequest"], "HttpResponse"]) -> None:
        self.get_response = get_response

    def __call__(self, request: "HttpRequest") -> "HttpResponse":
        if request.path.startswith(OBSERVABILITY_URL_PREFIX) and not is_direct_local_request(
            request
        ):
            logger.warning(
                "Rejected observability request that is not a direct local request: ip=%s",
                _client_ip(request),
            )
            return HttpResponseForbidden("Observability endpoints are localhost-only.")
        return self.get_response(request)
