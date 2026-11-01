"""Tasks a view creates itself follow its ``start_async`` work's lifetime.

``cancel_async_on_disconnect`` and an explicit root's disposal cancel the work
a view scheduled with ``start_async``. A task the view's own async code spawns
(a follower started from an ``async def mount`` or a push handler) was not
known to the framework, so nothing cancelled it: apps kept a manual cancel in
``disconnected()``. ``self.create_task(coro)`` (or ``self.track_task(task)``
for a task made elsewhere) registers it, and it is cancelled wherever the
view's ``start_async`` work is: ``cancel_async_all()``, a legacy root's close
with ``cancel_async_on_disconnect = True``, an explicit root's disposal on
disconnect or navigation.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.decorators import state

pytest.importorskip("channels")

EVENTS: list = []
GATE: dict = {}


async def _follow(tag):
    EVENTS.append(("following", tag))
    try:
        await GATE["gate"].wait()
    except asyncio.CancelledError:
        EVENTS.append(("cancelled", tag))
        raise
    EVENTS.append(("finished", tag))


class _LegacyFollower(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>follow</span></div>"

    async def mount(self, request, **kwargs):
        self.create_task(_follow(type(self).__name__))


class _LegacyCancelling(_LegacyFollower):
    cancel_async_on_disconnect = True


class _LegacyTracking(_LegacyFollower):
    """A task the view's code made with asyncio itself, then registered."""

    cancel_async_on_disconnect = True

    async def mount(self, request, **kwargs):
        self.track_task(asyncio.ensure_future(_follow(type(self).__name__)))


class _ExplicitFollower(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ n }}</span></div>"
    n = state(0)

    async def mount(self, request, **kwargs):
        self.create_task(_follow(type(self).__name__))

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)


class _Other(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>other</span></div>"


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


async def _receive_until(comm, wanted):
    for _ in range(8):
        frame = await comm.receive_json_from()
        if frame.get("type") in wanted:
            return frame
    raise AssertionError("no %s frame" % (wanted,))


async def _mounted(view):
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    key = await sync_to_async(_create_session)()

    class _ScopeSession:
        session_key = key

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = _ScopeSession()
    from django.contrib.auth.models import AnonymousUser

    comm.scope["user"] = AnonymousUser()
    assert (await comm.connect())[0]
    await comm.receive_json_from()
    await comm.send_json_to({"type": "mount", "view": f"{__name__}.{view}", "url": "/f/"})
    frame = await _receive_until(comm, ("mount", "error"))
    assert frame["type"] == "mount", frame
    await _until(lambda: ("following", view) in EVENTS, "the follower to start")
    return comm


@pytest.fixture(autouse=True)
def _reset():
    EVENTS.clear()
    GATE["gate"] = asyncio.Event()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        yield
    GATE["gate"].set()


pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("view", ["_LegacyCancelling", "_LegacyTracking"])
async def test_opted_in_legacy_view_cancels_its_own_tasks_on_disconnect(view):
    comm = await _mounted(view)
    await comm.disconnect()
    await _until(lambda: ("cancelled", view) in EVENTS, "the follower to be cancelled")


async def test_by_default_a_legacy_views_own_task_runs_on():
    comm = await _mounted("_LegacyFollower")
    await comm.disconnect()
    await asyncio.sleep(0.05)
    assert ("cancelled", "_LegacyFollower") not in EVENTS
    GATE["gate"].set()
    await _until(lambda: ("finished", "_LegacyFollower") in EVENTS, "the follower to finish")


async def test_explicit_view_cancels_its_own_tasks_on_disconnect():
    comm = await _mounted("_ExplicitFollower")
    await comm.disconnect()
    await _until(
        lambda: ("cancelled", "_ExplicitFollower") in EVENTS, "the follower to be cancelled"
    )


async def test_explicit_view_cancels_its_own_tasks_on_navigation():
    comm = await _mounted("_ExplicitFollower")
    try:
        await comm.send_json_to(
            {
                "type": "live_redirect_mount",
                "view": f"{__name__}._Other",
                "url": "/o/",
                "params": {},
            }
        )
        await _receive_until(comm, ("mount",))
        await _until(
            lambda: ("cancelled", "_ExplicitFollower") in EVENTS, "the follower to be cancelled"
        )
    finally:
        await comm.disconnect()


async def test_cancel_async_all_cancels_tracked_tasks_and_finished_ones_are_forgotten():
    view = _LegacyFollower()
    done = view.create_task(asyncio.sleep(0))
    await done
    pending = view.create_task(_follow("direct"))
    await _until(lambda: ("following", "direct") in EVENTS, "the task to start")
    assert view._tracked_tasks == {pending}
    view.cancel_async_all()
    await _until(lambda: ("cancelled", "direct") in EVENTS, "the task to be cancelled")
    assert pending.cancelled()


def test_create_task_needs_a_running_loop():
    view = _LegacyFollower()

    async def noop():
        pass

    coro = noop()
    with pytest.raises(RuntimeError, match="event loop"):
        view.create_task(coro)
