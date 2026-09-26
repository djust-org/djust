"""The event-driven frame helper itself (#3130): a close is never a quiet pass."""

import json

import pytest

from ._ws_frames import has_type, receive_until


class _Socket:
    """Just enough of ``WebsocketCommunicator`` for ``receive_until``."""

    def __init__(self, outputs):
        self._outputs = list(outputs)

    async def receive_nothing(self, timeout=0.1, interval=0.01):
        return not self._outputs

    async def receive_output(self, timeout=3):
        return self._outputs.pop(0)


def _text(frame):
    return {"type": "websocket.send", "text": json.dumps(frame)}


_CLOSE = {"type": "websocket.close", "code": 4403}


@pytest.mark.asyncio
async def test_a_close_before_the_expected_frame_fails():
    socket = _Socket([_text({"type": "noop"}), _CLOSE])
    with pytest.raises(AssertionError, match="closed before"):
        await receive_until(socket, has_type("error"), what="an error frame")


@pytest.mark.asyncio
async def test_a_caller_that_reads_the_close_can_opt_in():
    socket = _Socket([_text({"type": "noop"}), _CLOSE])
    frames = await receive_until(socket, has_type("error"), allow_close=True)
    assert [f["type"] for f in frames] == ["noop", "websocket.close"]


@pytest.mark.asyncio
async def test_a_close_the_caller_waits_for_ends_collection():
    socket = _Socket([_text({"type": "error"}), _CLOSE])
    frames = await receive_until(socket, lambda fs: fs[-1:] == [_CLOSE])
    assert [f["type"] for f in frames] == ["error", "websocket.close"]


@pytest.mark.asyncio
async def test_the_expected_frame_before_the_close_is_enough():
    socket = _Socket([_text({"type": "navigate"}), _CLOSE])
    frames = await receive_until(socket, has_type("navigate"))
    assert [f["type"] for f in frames] == ["navigate"]
