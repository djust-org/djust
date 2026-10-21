"""One Origin policy for the WebSocket handshake and the SSE event POSTs.

A browser's ``Origin`` is ``scheme://host[:port]``. It is allowed when it is
the origin the request was made to, or when it is listed as trusted — the rule
Django's CSRF check applies to an unsafe POST:

* **Same origin.** The scheme of the request (``wss``/``https``, or a
  ``SECURE_PROXY_SSL_HEADER`` match, is ``https``) and the host and port of its
  ``Host`` header, the host validated against ``ALLOWED_HOSTS`` first.
* **Trusted origins.** ``settings.DJUST_ALLOWED_ORIGINS`` when it is set,
  otherwise ``settings.CSRF_TRUSTED_ORIGINS``. Entries are full origins
  (``https://app.example.com``); ``https://*.example.com`` matches the
  subdomains of ``example.com`` over ``https``.

Scheme and port are part of the comparison, so ``http://localhost:9999`` is a
different origin from ``http://localhost:8001``.

A request with no ``Origin`` is refused unless
``settings.DJUST_ALLOW_MISSING_ORIGIN`` is True. Browsers send ``Origin`` on
every WebSocket handshake; only a non-browser client (a native app, a script,
a Channels ``WebsocketCommunicator`` in tests) omits it.

Settings are read on every call, so ``override_settings`` applies.
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _parse_origin(origin: str) -> Optional[Tuple[str, str, int]]:
    """``(scheme, host, port)`` for a serialized origin, or None if it isn't one.

    An origin has no userinfo, path, query or fragment, and its scheme is
    ``http`` or ``https``. The host is lower-cased and the port made explicit.
    """
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.path or parts.query or parts.fragment:
        return None
    return scheme, parts.hostname.lower(), port if port is not None else _DEFAULT_PORTS[scheme]


def _serialize(scheme: str, host: str, port: int) -> str:
    if ":" in host:
        host = "[%s]" % host
    if port == _DEFAULT_PORTS[scheme]:
        return "%s://%s" % (scheme, host)
    return "%s://%s:%d" % (scheme, host, port)


def trusted_origins() -> List[str]:
    """``DJUST_ALLOWED_ORIGINS`` if set, else ``CSRF_TRUSTED_ORIGINS``."""
    from django.conf import settings

    configured = getattr(settings, "DJUST_ALLOWED_ORIGINS", None)
    if configured is None:
        configured = getattr(settings, "CSRF_TRUSTED_ORIGINS", None)
    return list(configured or [])


def missing_origin_allowed() -> bool:
    """Whether a request without an ``Origin`` header is let through."""
    from django.conf import settings

    return bool(getattr(settings, "DJUST_ALLOW_MISSING_ORIGIN", False))


def _is_trusted(parsed: Tuple[str, str, int], entries: Iterable[str]) -> bool:
    from django.utils.http import is_same_domain

    scheme, host, port = parsed
    for entry in entries:
        if not isinstance(entry, str):
            continue
        wildcard = "://*." in entry
        candidate = _parse_origin(entry.replace("://*.", "://", 1) if wildcard else entry)
        if candidate is None or candidate[0] != scheme or candidate[2] != port:
            continue
        if wildcard:
            if is_same_domain(host, "." + candidate[1]) and host != candidate[1]:
                return True
        elif candidate[1] == host:
            return True
    return False


def origin_rejection(
    origin: Optional[str], own_origin: Optional[str], *, allow_missing: bool
) -> Optional[str]:
    """Why ``origin`` is refused, or None when it is allowed.

    ``own_origin`` is the origin the request was made to (see
    :func:`scope_origin`), or None when it cannot be established.
    """
    if not origin:
        if allow_missing:
            return None
        return (
            "no Origin header (set DJUST_ALLOW_MISSING_ORIGIN = True to accept "
            "non-browser clients that send none)"
        )
    parsed = _parse_origin(origin)
    if parsed is None:
        return "Origin %r is not a serialized http(s) origin" % origin
    if own_origin is not None and _parse_origin(own_origin) == parsed:
        return None
    if _is_trusted(parsed, trusted_origins()):
        return None
    return (
        "Origin %s does not match this server's origin %s and is not listed in "
        "DJUST_ALLOWED_ORIGINS (or CSRF_TRUSTED_ORIGINS when that is unset)"
        % (_serialize(*parsed), own_origin or "(unknown: no valid Host header)")
    )


def _secure_by_proxy_header(headers: Dict[bytes, bytes]) -> bool:
    """Whether ``SECURE_PROXY_SSL_HEADER`` marks the request as https."""
    from django.conf import settings

    proxy_header = getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
    if not proxy_header:
        return False
    meta_name, expected = proxy_header
    if not meta_name.startswith("HTTP_"):
        return False
    name = meta_name[5:].lower().replace("_", "-").encode("latin-1")
    value = headers.get(name)
    if value is None:
        return False
    # Django compares the first comma-separated value.
    return bool(value.decode("latin-1").split(",", 1)[0].strip() == expected)


def scope_origin(scope: Optional[Dict[str, Any]]) -> Optional[str]:
    """The origin an ASGI WebSocket/HTTP request was made to, or None.

    None when the ``Host`` header is missing, malformed or outside
    ``ALLOWED_HOSTS``.
    """
    if not scope:
        return None
    from django.http.request import split_domain_port

    from ..websocket import _host_in_allowed_hosts

    headers = dict(scope.get("headers") or [])
    raw_host = headers.get(b"host")
    if not raw_host:
        return None
    try:
        host_str = raw_host.decode("ascii")
    except UnicodeDecodeError:
        return None
    domain, port = split_domain_port(host_str)
    if not domain or not _host_in_allowed_hosts(domain):
        return None
    scheme = (scope.get("scheme") or "").lower()
    secure = scheme in ("wss", "https") or _secure_by_proxy_header(headers)
    http_scheme = "https" if secure else "http"
    try:
        port_number = int(port) if port else _DEFAULT_PORTS[http_scheme]
    except ValueError:
        return None
    return _serialize(http_scheme, domain.strip("[]").lower(), port_number)


def websocket_origin_rejection(scope: Dict[str, Any]) -> Optional[str]:
    """:func:`origin_rejection` for a WebSocket handshake scope."""
    headers = dict(scope.get("headers") or [])
    raw = headers.get(b"origin")
    if raw is None or raw == b"":
        origin = None
    else:
        try:
            origin = raw.decode("ascii")
        except UnicodeDecodeError:
            return "Origin header is not ASCII"
    return origin_rejection(origin, scope_origin(scope), allow_missing=missing_origin_allowed())
