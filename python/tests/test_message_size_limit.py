"""A WebSocket frame over ``max_message_size`` gets an actionable error.

The consumer refuses an incoming frame larger than
``LIVEVIEW_CONFIG["max_message_size"]`` (64 KB by default). The error frame
names the limit, carries the machine-readable code ``message_too_large`` and
points at ``UploadMixin``, and the ``connect`` frame announces the limit so
the client can refuse such a frame before sending it.
"""

import json

import pytest
from channels.testing import WebsocketCommunicator

from djust.config import config as djust_config
from djust.websocket import LiveViewConsumer


async def _connected():
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/live/")
    connected, _ = await communicator.connect()
    assert connected
    return communicator, await communicator.receive_json_from()


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_connect_frame_announces_the_limit():
    communicator, frame = await _connected()
    try:
        assert frame["type"] == "connect"
        assert frame["max_message_size"] == djust_config.get("max_message_size")
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_oversized_frame_gets_a_coded_error():
    limit = djust_config.get("max_message_size")
    communicator, _ = await _connected()
    try:
        await communicator.send_to(
            text_data=json.dumps(
                {"type": "event", "event": "save", "params": {"blob": "x" * (limit + 10)}}
            )
        )
        frame = await communicator.receive_json_from()
        assert frame["type"] == "error"
        assert frame["code"] == "message_too_large"
        assert frame["limit"] == limit
        assert frame["error"].startswith("Message too large (")  # parsed by clients
        assert str(limit) in frame["error"]
        assert "UploadMixin" in frame["error"]
    finally:
        await communicator.disconnect()
