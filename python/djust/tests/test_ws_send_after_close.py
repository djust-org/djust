"""Regression tests — send-after-close RuntimeError noise.

A client that sends ``mount`` and immediately disconnects (page reload, quick
navigation away) leaves the mount frame queued in Channels; by the time
``dispatch_mount`` sends the mount response, the ASGI server has already
completed the close handshake and rejects the send with::

    RuntimeError: Unexpected ASGI message 'websocket.send', after sending
    'websocket.close'.

The generic exception handler in ``receive()`` then tries ``send_json`` again
on the same dead socket, raising a second time and surfacing as
"ERROR: Exception in ASGI application" (observed in a production chat
deployment behind uvicorn; the same shape exists for every close initiator:
origin-reject 4403, per-IP limit 4429, auth-verdict close).

Fix: every outbound frame flows through ``LiveViewConsumer._send_frame``,
which (a) short-circuits once the connection is known-closed and (b)
downgrades the ASGI server's send-after-close rejection to a debug log while
remembering the closed state. Unrelated ``RuntimeError`` s still propagate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from djust.websocket import LiveViewConsumer

# Exact message raised by uvicorn (websockets/wsproto/sansio impls all share
# the format); daphne ignores post-close sends so only the flag path matters
# there.
_UVICORN_SEND_AFTER_CLOSE = (
    "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'."
)


def _consumer(base_send: AsyncMock) -> LiveViewConsumer:
    consumer = LiveViewConsumer()
    consumer.base_send = base_send
    return consumer


@pytest.mark.asyncio
async def test_send_json_swallows_send_after_close_runtime_error():
    """The ASGI server's send-after-close rejection must not propagate."""
    consumer = _consumer(AsyncMock(side_effect=RuntimeError(_UVICORN_SEND_AFTER_CLOSE)))

    # Must not raise — this is the exact double-failure from the production
    # trace (dispatch_mount send, then the receive() error-response send).
    await consumer.send_json({"type": "mount_success", "html": "<div></div>"})

    assert consumer._ws_close_sent is True


@pytest.mark.asyncio
async def test_sends_short_circuit_once_connection_known_closed():
    """After the first rejection the consumer stops hitting the transport."""
    base_send = AsyncMock(side_effect=RuntimeError(_UVICORN_SEND_AFTER_CLOSE))
    consumer = _consumer(base_send)

    await consumer.send_json({"type": "mount_success"})
    await consumer.send_json({"type": "error", "error": "Internal server error"})

    assert base_send.await_count == 1


@pytest.mark.asyncio
async def test_unrelated_runtime_error_still_propagates():
    """Only the send-after-close rejection is downgraded — nothing else."""
    consumer = _consumer(AsyncMock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        await consumer.send_json({"type": "pong"})

    assert getattr(consumer, "_ws_close_sent", False) is False


@pytest.mark.asyncio
async def test_close_marks_connection_so_queued_frames_are_dropped():
    """``close()`` (origin reject, per-IP 4429, auth verdict) sets the flag.

    A frame the client queued before our close frame reached it must be
    dropped instead of hitting the transport (uvicorn would raise).
    """
    base_send = AsyncMock()
    consumer = _consumer(base_send)

    await consumer.close(code=4429)
    await consumer.send_json({"type": "mount_success"})

    sent_types = [call.args[0]["type"] for call in base_send.await_args_list]
    assert sent_types == ["websocket.close"]


@pytest.mark.asyncio
async def test_binary_frame_send_is_guarded_too():
    """The binary patch path (use_binary) flows through the same guard."""
    base_send = AsyncMock(side_effect=RuntimeError(_UVICORN_SEND_AFTER_CLOSE))
    consumer = _consumer(base_send)

    await consumer._send_frame(bytes_data=b"\x93\x01\x02\x03")
    await consumer._send_frame(bytes_data=b"\x93\x01\x02\x03")

    assert base_send.await_count == 1


@pytest.mark.asyncio
async def test_disconnect_marks_connection_so_late_sends_are_dropped():
    """``disconnect()`` sets the flag too — parity with the ``close()`` path.

    Both server-side termination paths must mark the connection closed
    (#1104: N similar sites need N tests). The scenario this guards: a
    background task (``start_async``) finishes and tries to push a result
    frame *after* the client disconnected — it must be dropped, not hit the
    transport. Only the minimal group-cleanup state ``disconnect()`` touches
    is stubbed; the flag-set is its first statement and is what this pins.
    """
    base_send = AsyncMock()
    consumer = _consumer(base_send)
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "test.channel"
    consumer._view_group = None
    consumer._presence_group = None
    consumer.view_instance = None

    await consumer.disconnect(1006)
    assert consumer._ws_close_sent is True

    # A late async-result frame must be dropped, not raised on the dead socket.
    await consumer.send_json({"type": "async_result", "html": "<div></div>"})
    assert base_send.await_count == 0, "a frame sent after disconnect must be dropped"
