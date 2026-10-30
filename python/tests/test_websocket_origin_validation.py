"""
Tests for WebSocket Origin header validation (CSWSH defense, #653).

`LiveViewConsumer.connect()` refuses the handshake (close code 4403) unless
the ``Origin`` is the socket's own origin — scheme from the handshake, host
and port from its ``Host`` header, the host validated against
``settings.ALLOWED_HOSTS`` — or a trusted one (``DJUST_ALLOWED_ORIGINS`` /
``CSRF_TRUSTED_ORIGINS``). A missing Origin is refused unless
``DJUST_ALLOW_MISSING_ORIGIN`` is True (the suite's conftest sets it). See
``test_websocket_full_origin.py`` for the scheme/port/trusted-origin cases.
"""

import pytest
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust.websocket import LiveViewConsumer, _is_allowed_origin


def _scope(host, secure=True):
    """The handshake scope of a socket opened to ``host``."""
    return {"scheme": "wss" if secure else "ws", "headers": [(b"host", host.encode("ascii"))]}


# ---------------------------------------------------------------------------
# Unit tests for the _is_allowed_origin helper
# ---------------------------------------------------------------------------


class TestIsAllowedOriginUnit:
    """Unit tests for ``djust.websocket._is_allowed_origin``."""

    @override_settings(ALLOWED_HOSTS=["example.com"], DJUST_ALLOW_MISSING_ORIGIN=False)
    def test_missing_origin_rejected_by_default(self):
        assert _is_allowed_origin(None, _scope("example.com")) is False
        assert _is_allowed_origin(b"", _scope("example.com")) is False

    @override_settings(ALLOWED_HOSTS=["example.com"], DJUST_ALLOW_MISSING_ORIGIN=True)
    def test_missing_origin_allowed_with_opt_out(self):
        assert _is_allowed_origin(None, _scope("example.com")) is True
        assert _is_allowed_origin(b"", _scope("example.com")) is True

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_matching_origin_allowed(self):
        """The socket's own origin is accepted, over either scheme."""
        assert _is_allowed_origin(b"https://example.com", _scope("example.com")) is True
        assert _is_allowed_origin(b"http://example.com", _scope("example.com", False)) is True

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_port_is_compared(self):
        """The port is part of the origin; it is no longer stripped."""
        assert _is_allowed_origin(b"https://example.com:8443", _scope("example.com:8443")) is True
        assert _is_allowed_origin(b"https://example.com:8443", _scope("example.com")) is False
        assert _is_allowed_origin(b"http://example.com:8080", _scope("example.com", False)) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_scheme_is_compared(self):
        assert _is_allowed_origin(b"http://example.com", _scope("example.com")) is False
        assert _is_allowed_origin(b"https://example.com", _scope("example.com", False)) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_no_scope_only_trusted_origins_match(self):
        assert _is_allowed_origin(b"https://example.com") is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_disallowed_origin_rejected(self):
        """Origin not in ALLOWED_HOSTS is rejected."""
        assert _is_allowed_origin(b"https://evil.example", _scope("example.com")) is False
        assert _is_allowed_origin(b"https://attacker.com", _scope("example.com")) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_non_ascii_origin_rejected(self):
        """Binary / non-ASCII Origin bytes are rejected."""
        assert _is_allowed_origin(b"\xff\xfe\xfd", _scope("example.com")) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_null_origin_rejected(self):
        """`Origin: null` (sandboxed iframe, file://) is rejected."""
        assert _is_allowed_origin(b"null", _scope("example.com")) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_unparseable_origin_rejected(self):
        assert _is_allowed_origin(b"just-a-string", _scope("example.com")) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_scheme_only_origin_rejected(self):
        """``https://`` with no host (scheme only) is rejected."""
        assert _is_allowed_origin(b"https://", _scope("example.com")) is False
        assert _is_allowed_origin(b"http://", _scope("example.com")) is False

    @override_settings(ALLOWED_HOSTS=["target.com"])
    def test_userinfo_in_origin_rejected(self):
        """RFC 6454 §7: a serialized origin carries no userinfo, so one that
        does is not an origin at all."""
        assert _is_allowed_origin(b"https://evil.example@target.com", _scope("target.com")) is False
        assert _is_allowed_origin(b"https://target.com@evil.example", _scope("target.com")) is False

    @override_settings(ALLOWED_HOSTS=["target.com"])
    def test_subdomain_confusion_rejected(self):
        """``target.com.evil.com`` is NOT treated as ``target.com``."""
        assert _is_allowed_origin(b"https://target.com.evil.com", _scope("target.com")) is False
        assert _is_allowed_origin(b"https://evil.com.target.com", _scope("target.com")) is False

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_wildcard_allowed_hosts_does_not_admit_other_origins(self):
        """`*` admits any Host, so any same-origin socket, but a page on one
        host still cannot open a socket to another."""
        assert _is_allowed_origin(b"https://anywhere.test", _scope("anywhere.test")) is True
        assert _is_allowed_origin(b"https://anywhere.evil", _scope("app.test")) is False

    @override_settings(ALLOWED_HOSTS=[".example.com"])
    def test_subdomain_allowed_hosts(self):
        """`.example.com` admits the subdomains as hosts; each is its own origin."""
        assert _is_allowed_origin(b"https://app.example.com", _scope("app.example.com")) is True
        assert _is_allowed_origin(b"https://example.com", _scope("example.com")) is True
        assert _is_allowed_origin(b"https://api.example.com", _scope("app.example.com")) is False
        assert _is_allowed_origin(b"https://evil.com", _scope("evil.com")) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_case_insensitive_hostname(self):
        """Hostname matching is case-insensitive."""
        assert _is_allowed_origin(b"https://EXAMPLE.com", _scope("example.com")) is True
        assert _is_allowed_origin(b"https://example.com", _scope("Example.COM")) is True

    @override_settings(ALLOWED_HOSTS=["[::1]"])
    def test_ipv6_origin(self):
        """IPv6 origin with brackets matches an ipv6 entry in ALLOWED_HOSTS."""
        assert _is_allowed_origin(b"https://[::1]:8080", _scope("[::1]:8080")) is True

    @override_settings(ALLOWED_HOSTS=[], DEBUG=True)
    def test_empty_allowed_hosts_debug_mode_accepts_localhost(self):
        """In DEBUG with empty ALLOWED_HOSTS, fall back to localhost variants."""
        assert _is_allowed_origin(b"https://localhost:8000", _scope("localhost:8000")) is True
        assert _is_allowed_origin(b"http://127.0.0.1:8000", _scope("127.0.0.1:8000", False))
        assert _is_allowed_origin(b"https://evil.example", _scope("evil.example")) is False

    @override_settings(ALLOWED_HOSTS=[], DEBUG=False)
    def test_empty_allowed_hosts_production_fails_closed(self):
        """In production with empty ALLOWED_HOSTS, reject any Origin."""
        assert _is_allowed_origin(b"https://localhost", _scope("localhost")) is False
        assert _is_allowed_origin(b"https://example.com", _scope("example.com")) is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_origin_with_path_rejected(self):
        """A serialized origin has no path."""
        assert _is_allowed_origin(b"https://example.com/some/path", _scope("example.com")) is False


# ---------------------------------------------------------------------------
# Integration tests via WebsocketCommunicator + LiveViewConsumer.as_asgi()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestConnectOriginValidation:
    """End-to-end tests: real Channels handshake with an Origin header.

    Tests that perform a successful handshake + ``disconnect()`` are marked
    ``@pytest.mark.django_db``. Channels' default consumer dispatch invokes
    ``aclose_old_connections`` during disconnect; if a prior django_db-marked
    test left an in-memory SQLite connection in the cache (SQLite ignores
    ``close()`` for in-memory DBs to prevent data loss, so ``.connection`` is
    never reset to None), pytest-django's blocker raises inside
    ``close_if_unusable_or_obsolete`` → ``get_autocommit`` →
    ``ensure_connection``. Marking these tests with ``django_db`` opts them
    into pytest-django's connection management so the DB blocker is lifted
    while the consumer dispatch runs. See #1134.

    Tests that reject the handshake (``connected is False``) do not call
    ``disconnect()`` and therefore do not trigger the cleanup path.
    """

    async def _communicator(self, origin, host=b"example.com"):
        """A communicator opened to ``host`` with (or without) an Origin header.

        The Channels test scope carries no scheme, so the socket's own origin
        is ``http://<host>``.
        """
        headers = [(b"host", host)]
        if origin is not None:
            headers.append((b"origin", origin))
        return WebsocketCommunicator(
            LiveViewConsumer.as_asgi(),
            "/ws/",
            headers=headers,
        )

    @pytest.mark.django_db
    # Not pytest-django's ``settings`` fixture: mixed with ``override_settings``
    # its teardown restores the decorator's settings, leaking ALLOWED_HOSTS.
    @override_settings(ALLOWED_HOSTS=["example.com"], DJUST_ALLOW_MISSING_ORIGIN=True)
    async def test_connect_accepts_missing_origin_with_opt_out(self):
        """Non-browser clients (no Origin header) work with the opt-out."""
        communicator = await self._communicator(None)
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    @pytest.mark.django_db
    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_accepts_allowed_origin(self):
        """A browser on the socket's own origin is accepted."""
        communicator = await self._communicator(b"http://example.com")
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    @pytest.mark.django_db
    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_accepts_allowed_origin_with_port(self):
        """The Origin's port must match the Host header's port."""
        communicator = await self._communicator(b"http://example.com:8443", b"example.com:8443")
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_rejects_disallowed_origin(self):
        """Cross-origin attacker is rejected with close code 4403."""
        communicator = await self._communicator(b"https://evil.example")
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4403

    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_rejects_malformed_origin(self):
        """Malformed (non-ASCII) Origin is rejected with close code 4403."""
        communicator = await self._communicator(b"\xff\xfe")
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4403

    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_rejects_null_origin(self):
        """`Origin: null` from sandboxed iframes is rejected."""
        communicator = await self._communicator(b"null")
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4403

    @pytest.mark.django_db
    @override_settings(ALLOWED_HOSTS=["*"])
    async def test_connect_allows_wildcard_allowed_hosts(self):
        """`*` in ALLOWED_HOSTS admits any host's own origin."""
        communicator = await self._communicator(b"http://whatever.example", b"whatever.example")
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()
