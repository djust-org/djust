"""``cancel_async_on_disconnect = True``: a legacy root's work stops with its socket.

An explicit (ADR-038) root is disposed with its subtree on disconnect, which
cancels its ``start_async`` work. A legacy root (the default exposure) lets its
background work run to completion and discards the late result (#3234 review:
owner decision), which is right for work that must finish, such as an export.
Work that only serves the open page (a follower of a live run, a feed) then
kept running for a page nobody could see, holding the view and whatever it
awaited, and every app cancelled its own tasks from ``disconnected()``.

A view now opts in with ``cancel_async_on_disconnect = True``. The default is
unchanged, and navigation does not cancel either way.
"""

from __future__ import annotations

import asyncio

import pytest
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler

pytest.importorskip("channels")

EVENTS: list = []
GATE: dict = {}


class _Follower(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>follow</span></div>"

    def mount(self, request, **kwargs):
        pass

    @event_handler()
    def follow(self, **kwargs):
        self.start_async(self._follow, name="follow")

    async def _follow(self):
        EVENTS.append("following")
        try:
            await GATE["gate"].wait()
        except asyncio.CancelledError:
            EVENTS.append("cancelled")
            raise
        EVENTS.append("finished")

    def handle_async_result(self, name, result=None, error=None):
        pass


class _CancelsOnDisconnect(_Follower):
    cancel_async_on_disconnect = True


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


async def _follow_then_disconnect(view_path):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    await comm.send_json_to({"type": "mount", "view": view_path, "url": "/follow/"})
    for _ in range(8):
        if (await comm.receive_json_from()).get("type") == "mount":
            break
    await comm.send_json_to({"type": "event", "event": "follow", "params": {}})
    await _until(lambda: "following" in EVENTS, "the follower to start")
    await comm.disconnect()


@pytest.fixture(autouse=True)
def _reset():
    EVENTS.clear()
    GATE["gate"] = asyncio.Event()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        yield
    GATE["gate"].set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_view_that_opts_in_has_its_work_cancelled_on_disconnect():
    await _follow_then_disconnect(f"{__name__}._CancelsOnDisconnect")
    await _until(lambda: "cancelled" in EVENTS, "the follower to be cancelled")
    GATE["gate"].set()
    await asyncio.sleep(0.05)
    assert "finished" not in EVENTS


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_by_default_a_legacy_roots_work_runs_to_completion():
    """#3234's default is kept: the opt-in changes nothing for other views."""
    await _follow_then_disconnect(f"{__name__}._Follower")
    await asyncio.sleep(0.05)
    assert "cancelled" not in EVENTS
    GATE["gate"].set()
    await _until(lambda: "finished" in EVENTS, "the follower to finish")
