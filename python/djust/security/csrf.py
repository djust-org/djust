"""Bind the browser's CSRF secret to a request djust rebuilt for a live mount.

The WebSocket mount path (and the live_redirect sticky re-check) rebuilds the
view's request with ``RequestFactory``: it carries the session and user, but
no cookies, and Django's ``CsrfViewMiddleware`` never runs on it. Without the
browser's secret bound, ``get_token()`` invents a new one the browser never
receives, so every ``{% csrf_token %}`` form rendered over the socket posts a
token that fails with "CSRF verification failed" (#2998).

:func:`bind_csrf_cookie` does what the middleware would have done on a real
HTTP request: it copies the browser's CSRF cookie from the ASGI scope onto the
request and runs ``CsrfViewMiddleware.process_request``, which validates the
value, unmasks legacy 64-character cookies, and reads the session instead when
``CSRF_USE_SESSIONS`` is set.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _cookie_from_scope(scope: Optional[Mapping[str, Any]], name: str) -> Optional[str]:
    if not scope:
        return None
    # Channels' CookieMiddleware (part of AuthMiddlewareStack / the session
    # stack) parses the header into scope["cookies"].
    cookies = scope.get("cookies")
    if cookies:
        return cookies.get(name) or None
    # A bare consumer (no cookie middleware) still has the raw header.
    from django.http.cookie import parse_cookie

    # HTTP/2 may split cookies across several headers; RFC 9113 §8.2.3 says to
    # join them with "; " before parsing.
    raw = "; ".join(
        value.decode("latin1") for key, value in scope.get("headers") or () if key == b"cookie"
    )
    if not raw:
        return None
    try:
        return parse_cookie(raw).get(name) or None
    except Exception:  # noqa: BLE001 — a malformed header binds nothing
        return None


def bind_csrf_cookie(request: Any, scope: Optional[Mapping[str, Any]] = None) -> None:
    """Bind the browser's CSRF secret onto ``request`` the way the middleware would.

    ``scope`` is the ASGI scope carrying the browser's cookies (the WebSocket
    handshake scope). Pass ``None`` for a request that already holds its
    cookies (the real SSE stream request): the middleware logic still runs on
    ``request.COOKIES``.

    No-op when the request is already bound (``request.META["CSRF_COOKIE"]``
    is set — e.g. a real request that went through ``CsrfViewMiddleware``).
    Never raises: a CSRF edge case must not break the mount. When nothing can
    be bound, ``get_token()`` falls back to a new secret, as before.

    Call it AFTER ``request.session`` is attached, so ``CSRF_USE_SESSIONS``
    can read the secret from the session.
    """
    if request is None or "CSRF_COOKIE" in getattr(request, "META", {}):
        return
    try:
        from django.conf import settings
        from django.middleware.csrf import CsrfViewMiddleware

        name = settings.CSRF_COOKIE_NAME
        value = _cookie_from_scope(scope, name)
        if value and name not in request.COOKIES:
            request.COOKIES[name] = value
        CsrfViewMiddleware(lambda _request: None).process_request(request)
    except Exception:  # noqa: BLE001 — e.g. CSRF_USE_SESSIONS without a session
        logger.debug("Could not bind the browser CSRF secret to a rebuilt request", exc_info=True)


async def abind_csrf_cookie(request: Any, scope: Optional[Mapping[str, Any]] = None) -> None:
    """:func:`bind_csrf_cookie` for async callers (the socket mount paths).

    With ``CSRF_USE_SESSIONS`` the secret is read from the session, which may
    hit the database, so the bind runs in a thread. Otherwise it only reads
    cookies and runs inline.
    """
    from django.conf import settings

    if getattr(settings, "CSRF_USE_SESSIONS", False):
        from asgiref.sync import sync_to_async

        await sync_to_async(bind_csrf_cookie)(request, scope)
    else:
        bind_csrf_cookie(request, scope)
