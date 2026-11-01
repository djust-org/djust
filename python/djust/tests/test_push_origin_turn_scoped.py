"""The originator tag of a push covers the handler's turn, not work it started.

``push_to_view`` tags a push with the sending socket's channel while an event
handler runs (#1677), so that socket skips its own redundant self-broadcast.
The tag lives in a ContextVar, and an asyncio task copies the context when it
is created: a task started during the turn (a pump or follower launched by a
service the handler awaited) kept the tag after the turn ended. Its later
pushes were then dropped on the originating socket as "own self-broadcast",
although they were news to that page.

Now the tag is honoured only while the turn that set it is still running.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust import push as _push
from djust.push import _server_push_message, apush_to_view, origin_channel

pytest.importorskip("channels")

VIEW = f"{__name__}._PumpView"
#: The pump task each test's handler starts, and the gate that releases it.
STATE: dict = {}


def test_a_push_inside_the_turn_is_tagged():
    with _push.originating_turn("session.A"):
        assert _server_push_message(None, None, None)["sender_channel"] == "session.A"
    assert _server_push_message(None, None, None)["sender_channel"] is None


@pytest.mark.asyncio
async def test_a_task_started_in_the_turn_pushes_untagged_after_it():
    gate = asyncio.Event()
    seen = []

    async def pump():
        seen.append(_server_push_message(None, None, None)["sender_channel"])
        await gate.wait()
        seen.append(_server_push_message(None, None, None)["sender_channel"])

    with _push.originating_turn("session.A"):
        task = asyncio.ensure_future(pump())
        await asyncio.sleep(0)  # the pump's first push happens inside the turn
    gate.set()
    await task
    assert seen == ["session.A", None]


def test_an_explicitly_set_tag_is_still_honoured():
    """Code that sets ``origin_channel`` itself (no turn) keeps its tag."""
    token = origin_channel.set("session.B")
    try:
        assert _server_push_message(None, None, None)["sender_channel"] == "session.B"
    finally:
        origin_channel.reset(token)


@pytest.mark.asyncio
async def test_a_task_that_sets_its_own_tag_after_the_turn_keeps_it():
    gate = asyncio.Event()
    seen = []

    async def pump():
        await gate.wait()
        origin_channel.set("session.C")
        seen.append(_server_push_message(None, None, None)["sender_channel"])

    with _push.originating_turn("session.A"):
        task = asyncio.ensure_future(pump())
    gate.set()
    await task
    assert seen == ["session.C"]


# --------------------------------------------------------------------------- #
# Through a real socket
# --------------------------------------------------------------------------- #


class _PumpView(LiveView):
    exposure_policy = "legacy"
    template = (
        '<div dj-root dj-view="djust.tests.test_push_origin_turn_scoped._PumpView">'
        "<i>{{ news }}</i></div>"
    )

    def mount(self, request, **kwargs):
        self.news = "none"

    @event_handler()
    async def start_pump(self, **kwargs):
        # A service the handler awaits launches a long-lived pump: the task is
        # created inside the handler's turn, so it copies the turn's context.
        STATE["pump"] = asyncio.ensure_future(_pump())

    def handle_news(self, news: str = "", **kwargs):
        self.news = news


async def _pump():
    await STATE["gate"].wait()
    await apush_to_view(VIEW, handler="handle_news", payload={"news": "from-the-pump"})


async def _receive_until(communicator, predicate, *, tries=8, timeout=5):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if predicate(last):
            return last
    return last


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_originating_socket_receives_a_later_push_from_work_it_started():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    STATE.clear()
    STATE["gate"] = asyncio.Event()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await comm.connect()
        assert connected
        try:
            await comm.receive_json_from()
            await comm.send_json_to({"type": "mount", "view": VIEW, "url": "/pump/"})
            await _receive_until(comm, lambda f: f.get("type") == "mount")
            await comm.send_json_to(
                {"type": "event", "event": "start_pump", "params": {}, "ref": 1}
            )
            # The event's own response, then the turn is over.
            await _receive_until(comm, lambda f: f.get("ref") == 1)
            STATE["gate"].set()
            await STATE["pump"]
            frame = await _receive_until(comm, lambda f: f.get("source") == "broadcast")
        finally:
            await comm.disconnect()
    assert frame.get("source") == "broadcast", frame
    assert "from-the-pump" in json.dumps(frame.get("patches") or frame.get("html"))
