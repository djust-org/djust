"""The event-driven frame helper itself (#3130): a close is never a quiet pass.

And (#3256) a slow first frame is waited for, never mistaken for silence."""

import json

import pytest

from ._ws_frames import has_frame, has_type, receive_settled, receive_until


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


class _SlowSocket(_Socket):
    """A socket whose first frame is late: ``quiet_polls`` polls see nothing.

    Stands in for a loaded runner, where the mount (or event reply) takes
    longer than a 0.3 s quiet window to produce its first frame (#3256).
    """

    def __init__(self, outputs, quiet_polls):
        super().__init__(outputs)
        self._quiet_polls = quiet_polls

    async def receive_nothing(self, timeout=0.1, interval=0.01):
        if self._quiet_polls:
            self._quiet_polls -= 1
            return True
        return not self._outputs


async def _quiet_window_drain(socket):
    """The #3256 pattern: read until the socket is quiet once."""
    frames = []
    while not await socket.receive_nothing(timeout=0.3):
        frames.append(json.loads((await socket.receive_output())["text"]))
    return frames


@pytest.mark.asyncio
async def test_a_quiet_window_drain_returns_nothing_when_the_first_frame_is_late():
    """The reproduction: the old drain gives up before a late mount frame."""
    socket = _SlowSocket([_text({"type": "mount"})], quiet_polls=1)
    assert await _quiet_window_drain(socket) == []


@pytest.mark.asyncio
async def test_receive_settled_waits_for_a_late_frame_then_drains_what_follows():
    socket = _SlowSocket([_text({"type": "mount"}), _text({"type": "push_event"})], quiet_polls=5)
    frames = await receive_settled(socket, has_type("mount"), quiet=0.01)
    assert [f["type"] for f in frames] == ["mount", "push_event"]


@pytest.mark.asyncio
async def test_receive_settled_stops_at_a_close_the_caller_waited_through():
    socket = _Socket([_text({"type": "error"}), _CLOSE])
    frames = await receive_settled(socket, has_type("error"), quiet=0.01)
    assert [f["type"] for f in frames] == ["error", "websocket.close"]


def test_has_frame_matches_type_and_every_field():
    frames = [{"type": "patch", "ref": 1, "source": "tick"}, {"type": "noop", "ref": 2}]
    assert has_frame("patch", source="tick")(frames)
    assert has_frame(ref=2)(frames)
    assert not has_frame("patch", ref=2)(frames)
    assert not has_frame("noop", source="event")(frames)
    assert not has_frame("error")(frames)
