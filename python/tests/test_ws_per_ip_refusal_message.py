"""The per-IP WebSocket refusal (close code 4429) says which limit applied.

``LIVEVIEW_CONFIG["rate_limit"]["max_connections_per_ip"]`` (default 10)
caps the sockets one client address holds open on one process, and
``reconnect_cooldown`` (default 5 s) bars an address for a while after a
rate-limit disconnect. Behind a proxy whose ``X-Forwarded-For`` is not
trusted (``DJUST_TRUSTED_PROXY_COUNT`` is 0), every client shares the proxy's
address — and so shares the limit. The refusal log names the limit, the
setting, and that proxy case.
"""

import logging

import pytest
from channels.testing import WebsocketCommunicator

from djust.rate_limit import ip_tracker
from djust.websocket import LiveViewConsumer

PEER = "10.9.8.7"


def _with_client(app):
    async def wrapped(scope, receive, send):
        scope = dict(scope, client=(PEER, 50000))
        return await app(scope, receive, send)

    return wrapped


async def _connect(headers=()):
    communicator = WebsocketCommunicator(
        _with_client(LiveViewConsumer.as_asgi()), "/ws/live/", headers=list(headers)
    )
    connected, code = await communicator.connect()
    if not connected:
        return code
    # Accepted first; a refused address is then closed with 4429.
    message = await communicator.receive_output()
    if message["type"] == "websocket.close":
        return message["code"]
    await communicator.disconnect()
    return None


@pytest.fixture
def full_address(monkeypatch):
    monkeypatch.setattr(ip_tracker, "connect", lambda ip, limit: False)


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_refusal_names_the_limit_setting(full_address, caplog, settings):
    settings.DJUST_TRUSTED_PROXY_COUNT = 0
    with caplog.at_level(logging.WARNING, logger="djust.websocket"):
        assert await _connect() == 4429
    text = caplog.text
    assert PEER in text
    assert "max_connections_per_ip" in text
    assert "X-Forwarded-For" not in text


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_refusal_flags_an_untrusted_proxy(full_address, caplog, settings):
    settings.DJUST_TRUSTED_PROXY_COUNT = 0
    with caplog.at_level(logging.WARNING, logger="djust.websocket"):
        assert await _connect([(b"x-forwarded-for", b"203.0.113.5")]) == 4429
    assert "DJUST_TRUSTED_PROXY_COUNT" in caplog.text
    assert "X-Forwarded-For" in caplog.text


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_refusal_during_cooldown_says_so(caplog, settings):
    settings.DJUST_TRUSTED_PROXY_COUNT = 0
    ip_tracker.add_cooldown(PEER, 60)
    try:
        with caplog.at_level(logging.WARNING, logger="djust.websocket"):
            assert await _connect() == 4429
        assert "reconnect_cooldown" in caplog.text
    finally:
        ip_tracker._cooldowns.pop(PEER, None)
