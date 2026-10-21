"""The WebSocket Origin check compares the full origin, and refuses a missing one.

A browser's ``Origin`` is ``scheme://host[:port]``. The check used to compare
only the host with ``ALLOWED_HOSTS``, so a page served from another port or
over the other scheme on the same host (``http://localhost:9999`` against an
app on ``http://localhost:8001``) could open a socket. A handshake with no
Origin at all was let through too.

The rule now matches the one Django applies to an unsafe POST: the Origin must
equal the origin the socket was opened on (scheme from the handshake, host and
port from its ``Host`` header, the host validated against ``ALLOWED_HOSTS``),
or be listed in ``DJUST_ALLOWED_ORIGINS`` (default: ``CSRF_TRUSTED_ORIGINS``).
A missing Origin is refused unless ``DJUST_ALLOW_MISSING_ORIGIN = True``.

The suite-wide conftest sets ``DJUST_ALLOW_MISSING_ORIGIN`` because most
consumer tests open communicators without headers; the default-path tests
here delete it.
"""

import pytest
from channels.testing import WebsocketCommunicator

from djust.routing import DjustMiddlewareStack
from djust.security.origin import origin_rejection, scope_origin
from djust.websocket import LiveViewConsumer

APP_HOST = b"localhost:8001"


def _headers(origin, host=APP_HOST, extra=()):
    headers = []
    if host is not None:
        headers.append((b"host", host))
    if origin is not None:
        headers.append((b"origin", origin))
    headers.extend(extra)
    return headers


async def _connect(app, origin, host=APP_HOST, extra=()):
    communicator = WebsocketCommunicator(app, "/ws/live/", headers=_headers(origin, host, extra))
    connected, code = await communicator.connect()
    if connected:
        await communicator.disconnect()
    return connected, code


@pytest.fixture(autouse=True)
def _hosts(settings):
    settings.ALLOWED_HOSTS = ["localhost", ".example.com"]
    settings.CSRF_TRUSTED_ORIGINS = []
    for name in ("DJUST_ALLOWED_ORIGINS", "SECURE_PROXY_SSL_HEADER"):
        if hasattr(settings, name):
            delattr(settings, name)


@pytest.fixture
def default_missing_origin(settings):
    """Run with the shipped default: no DJUST_ALLOW_MISSING_ORIGIN setting."""
    if hasattr(settings, "DJUST_ALLOW_MISSING_ORIGIN"):
        del settings.DJUST_ALLOW_MISSING_ORIGIN
    return settings


# ---------------------------------------------------------------------------
# The consumer's own check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db
class TestConsumerFullOrigin:
    async def test_same_origin_accepted(self):
        connected, _ = await _connect(LiveViewConsumer.as_asgi(), b"http://localhost:8001")
        assert connected is True

    async def test_other_port_rejected(self):
        connected, code = await _connect(LiveViewConsumer.as_asgi(), b"http://localhost:9999")
        assert (connected, code) == (False, 4403)

    async def test_other_scheme_rejected(self):
        connected, code = await _connect(LiveViewConsumer.as_asgi(), b"https://localhost:8001")
        assert (connected, code) == (False, 4403)

    async def test_default_port_is_implicit(self):
        connected, _ = await _connect(
            LiveViewConsumer.as_asgi(), b"http://localhost", host=b"localhost:80"
        )
        assert connected is True

    async def test_host_outside_allowed_hosts_rejected(self):
        """The same-origin rule trusts the Host header only after ALLOWED_HOSTS."""
        connected, code = await _connect(
            LiveViewConsumer.as_asgi(), b"http://evil.test:8001", host=b"evil.test:8001"
        )
        assert (connected, code) == (False, 4403)

    async def test_missing_origin_rejected_by_default(self, default_missing_origin):
        connected, code = await _connect(LiveViewConsumer.as_asgi(), None)
        assert (connected, code) == (False, 4403)

    async def test_missing_origin_allowed_with_opt_out(self, settings):
        settings.DJUST_ALLOW_MISSING_ORIGIN = True
        connected, _ = await _connect(LiveViewConsumer.as_asgi(), None)
        assert connected is True

    async def test_csrf_trusted_origin_accepted(self, settings):
        settings.CSRF_TRUSTED_ORIGINS = ["https://app.example.com"]
        connected, _ = await _connect(LiveViewConsumer.as_asgi(), b"https://app.example.com")
        assert connected is True

    async def test_djust_allowed_origins_replaces_csrf_trusted_origins(self, settings):
        settings.CSRF_TRUSTED_ORIGINS = ["https://app.example.com"]
        settings.DJUST_ALLOWED_ORIGINS = ["https://other.example.com"]
        ok, _ = await _connect(LiveViewConsumer.as_asgi(), b"https://other.example.com")
        refused, code = await _connect(LiveViewConsumer.as_asgi(), b"https://app.example.com")
        assert ok is True
        assert (refused, code) == (False, 4403)

    async def test_secure_proxy_header_sets_the_scheme(self, settings):
        """Behind a TLS-terminating proxy the handshake arrives as ``ws``."""
        settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
        connected, _ = await _connect(
            LiveViewConsumer.as_asgi(),
            b"https://localhost:8001",
            extra=[(b"x-forwarded-proto", b"https")],
        )
        assert connected is True


# ---------------------------------------------------------------------------
# DjustMiddlewareStack's validator applies the same policy
# ---------------------------------------------------------------------------


async def _echo_app(scope, receive, send):
    """Accepts any socket, so only the validator in front of it decides."""
    message = await receive()
    assert message["type"] == "websocket.connect"
    await send({"type": "websocket.accept"})
    while (await receive())["type"] != "websocket.disconnect":
        pass


@pytest.mark.asyncio
@pytest.mark.django_db
class TestStackValidator:
    async def test_other_port_rejected(self):
        connected, _ = await _connect(DjustMiddlewareStack(_echo_app), b"http://localhost:9999")
        assert connected is False

    async def test_same_origin_accepted(self):
        connected, _ = await _connect(DjustMiddlewareStack(_echo_app), b"http://localhost:8001")
        assert connected is True

    async def test_missing_origin_rejected_by_default(self, default_missing_origin):
        connected, _ = await _connect(DjustMiddlewareStack(_echo_app), None)
        assert connected is False

    async def test_missing_origin_opt_out_reaches_the_stack(self, settings):
        settings.DJUST_ALLOW_MISSING_ORIGIN = True
        connected, _ = await _connect(DjustMiddlewareStack(_echo_app), None)
        assert connected is True

    async def test_settings_are_read_per_handshake(self, settings):
        """Built once, the stack still sees a later CSRF_TRUSTED_ORIGINS."""
        app = DjustMiddlewareStack(_echo_app)
        settings.CSRF_TRUSTED_ORIGINS = ["https://app.example.com"]
        connected, _ = await _connect(app, b"https://app.example.com")
        assert connected is True


# ---------------------------------------------------------------------------
# The policy function
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_scope_origin(self):
        scope = {"scheme": "wss", "headers": [(b"host", b"Example.com:443")]}
        assert scope_origin(scope) == "https://example.com"

    def test_scope_origin_none_without_valid_host(self):
        assert scope_origin({"headers": [(b"host", b"evil.test")]}) is None
        assert scope_origin({"headers": []}) is None

    def test_wildcard_trusted_origin(self, settings):
        settings.CSRF_TRUSTED_ORIGINS = ["https://*.example.com"]
        assert origin_rejection("https://a.example.com", None, allow_missing=False) is None
        assert origin_rejection("http://a.example.com", None, allow_missing=False)
        assert origin_rejection("https://example.org", None, allow_missing=False)

    @pytest.mark.parametrize(
        "origin",
        [
            "null",
            "http://localhost:8001/path",
            "http://user@localhost:8001",
            "ftp://localhost:8001",
            "http://localhost:notaport",
            "localhost:8001",
        ],
    )
    def test_malformed_rejected(self, origin):
        assert origin_rejection(origin, "http://localhost:8001", allow_missing=True)

    def test_rejection_names_both_origins(self):
        reason = origin_rejection(
            "http://localhost:9999", "http://localhost:8001", allow_missing=False
        )
        assert "http://localhost:9999" in reason
        assert "http://localhost:8001" in reason
