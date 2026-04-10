"""
Tests for WebSocket Origin header validation (CSWSH defense, #653).

`LiveViewConsumer.connect()` validates the ``Origin`` header against
``settings.ALLOWED_HOSTS`` before accepting the handshake. Missing Origin is
allowed (non-browser clients); disallowed origins are rejected with close
code 4403.
"""

import pytest
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust.websocket import LiveViewConsumer, _is_allowed_origin


# ---------------------------------------------------------------------------
# Unit tests for the _is_allowed_origin helper
# ---------------------------------------------------------------------------


class TestIsAllowedOriginUnit:
    """Unit tests for ``djust.websocket._is_allowed_origin``."""

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_missing_origin_allowed(self):
        """Non-browser clients (no Origin header) are allowed."""
        assert _is_allowed_origin(None) is True
        assert _is_allowed_origin(b"") is True

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_matching_origin_allowed(self):
        """Origin matching ALLOWED_HOSTS exactly is accepted."""
        assert _is_allowed_origin(b"https://example.com") is True
        assert _is_allowed_origin(b"http://example.com") is True

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_matching_origin_with_port_allowed(self):
        """Port is stripped from Origin before matching."""
        assert _is_allowed_origin(b"https://example.com:8443") is True
        assert _is_allowed_origin(b"http://example.com:8080") is True

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_disallowed_origin_rejected(self):
        """Origin not in ALLOWED_HOSTS is rejected."""
        assert _is_allowed_origin(b"https://evil.example") is False
        assert _is_allowed_origin(b"https://attacker.com") is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_non_ascii_origin_rejected(self):
        """Binary / non-ASCII Origin bytes are rejected."""
        assert _is_allowed_origin(b"\xff\xfe\xfd") is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_null_origin_rejected(self):
        """`Origin: null` (sandboxed iframe, file://) is rejected."""
        assert _is_allowed_origin(b"null") is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_unparseable_origin_rejected(self):
        """Garbage that urlparse can't resolve to a hostname is rejected."""
        # No scheme, no host — urlparse returns hostname=None.
        assert _is_allowed_origin(b"just-a-string") is False

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_wildcard_allows_anything(self):
        """`*` in ALLOWED_HOSTS allows any origin (matches Django HTTP semantics)."""
        assert _is_allowed_origin(b"https://anywhere.evil") is True
        assert _is_allowed_origin(b"https://literally-anything.test") is True

    @override_settings(ALLOWED_HOSTS=[".example.com"])
    def test_subdomain_wildcard(self):
        """`.example.com` allows example.com and any subdomain (Django semantics)."""
        assert _is_allowed_origin(b"https://app.example.com") is True
        assert _is_allowed_origin(b"https://api.example.com") is True
        assert _is_allowed_origin(b"https://example.com") is True
        assert _is_allowed_origin(b"https://evil.com") is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_case_insensitive_hostname(self):
        """Hostname matching is case-insensitive."""
        assert _is_allowed_origin(b"https://EXAMPLE.com") is True
        assert _is_allowed_origin(b"https://Example.COM") is True

    @override_settings(ALLOWED_HOSTS=["[::1]"])
    def test_ipv6_origin(self):
        """IPv6 origin with brackets matches an ipv6 entry in ALLOWED_HOSTS."""
        assert _is_allowed_origin(b"https://[::1]:8080") is True

    @override_settings(ALLOWED_HOSTS=[], DEBUG=True)
    def test_empty_allowed_hosts_debug_mode_accepts_localhost(self):
        """In DEBUG with empty ALLOWED_HOSTS, fall back to localhost variants."""
        assert _is_allowed_origin(b"https://localhost:8000") is True
        assert _is_allowed_origin(b"http://127.0.0.1:8000") is True
        assert _is_allowed_origin(b"https://evil.example") is False

    @override_settings(ALLOWED_HOSTS=[], DEBUG=False)
    def test_empty_allowed_hosts_production_fails_closed(self):
        """In production with empty ALLOWED_HOSTS, reject any Origin."""
        assert _is_allowed_origin(b"https://localhost") is False
        assert _is_allowed_origin(b"https://example.com") is False

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_origin_with_path_stripped(self):
        """URL path component is stripped before hostname comparison."""
        assert _is_allowed_origin(b"https://example.com/some/path") is True


# ---------------------------------------------------------------------------
# Integration tests via WebsocketCommunicator + LiveViewConsumer.as_asgi()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestConnectOriginValidation:
    """End-to-end tests: real Channels handshake with an Origin header."""

    async def _communicator(self, origin):
        """Build a WebsocketCommunicator with (or without) an Origin header."""
        headers = [(b"origin", origin)] if origin is not None else []
        return WebsocketCommunicator(
            LiveViewConsumer.as_asgi(),
            "/ws/",
            headers=headers,
        )

    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_accepts_missing_origin(self):
        """Non-browser clients (no Origin header) continue to work."""
        communicator = await self._communicator(None)
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_accepts_allowed_origin(self):
        """Browser with Origin in ALLOWED_HOSTS is accepted."""
        communicator = await self._communicator(b"https://example.com")
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    async def test_connect_accepts_allowed_origin_with_port(self):
        """Origin with port matches ALLOWED_HOSTS host-only entry."""
        communicator = await self._communicator(b"https://example.com:8443")
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

    @override_settings(ALLOWED_HOSTS=["*"])
    async def test_connect_allows_wildcard_allowed_hosts(self):
        """`*` in ALLOWED_HOSTS opens the door to any origin."""
        communicator = await self._communicator(b"https://whatever.example")
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()
