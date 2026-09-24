"""#2945: a view whose ``tick_interval`` is shorter than its mount time must tick.

The runtime starts the tick task inside the mount (``on_view_mounted``), but
the consumer reads the view back only after ``dispatch_mount`` returns.
``_run_tick`` treated the missing view as "disconnected" and stopped for good
if its first beat landed during mount, so a fast tick on a slow mount never
ticked, with no error.
"""

import asyncio
import time

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.websocket import LiveViewConsumer

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})
TICKS = []


class SlowMountTickView(LiveView):
    template = "<div dj-root>{{ n }}</div>"
    tick_interval = 20  # far shorter than the mount below

    def mount(self, request, **kwargs):
        time.sleep(0.3)
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    def handle_tick(self):
        TICKS.append(1)
        self.n += 1


def _session():
    session = SessionStore()
    session.save()
    return session


async def _socket():
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    return socket


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tick_shorter_than_mount_still_ticks():
    TICKS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _socket()
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".SlowMountTickView", "url": "/t/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            frame = await socket.receive_json_from(timeout=3)
            assert frame.get("source") == "tick", frame
            assert TICKS, "handle_tick never ran"
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tick_does_not_run_before_mount_finishes():
    """Waiting out the mount must not tick a half-mounted view."""
    TICKS.clear()
    seen_during_mount = []

    class Probe(SlowMountTickView):
        def mount(self, request, **kwargs):
            time.sleep(0.2)
            seen_during_mount.append(len(TICKS))
            self.n = 0

    Probe.__module__ = __name__
    globals()["Probe2945"] = Probe
    Probe.__qualname__ = Probe.__name__ = "Probe2945"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _socket()
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".Probe2945", "url": "/t/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            assert seen_during_mount == [0]
        finally:
            await socket.disconnect()


class _Runtime:
    def __init__(self, view):
        self.view_instance = view


async def _loop_exits(consumer, timeout=1):
    try:
        await asyncio.wait_for(consumer._run_tick(5), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


@pytest.mark.asyncio
async def test_loop_waits_only_for_its_own_mounting_view():
    """The wait lasts while the runtime holds the view this loop was started
    for; once the runtime moves on (refused mount, re-mount), the loop ends."""
    consumer = LiveViewConsumer()
    mounting = object()
    consumer._runtime = _Runtime(mounting)
    consumer._tick_once = _no_tick
    task = asyncio.ensure_future(consumer._run_tick(5))
    await asyncio.sleep(0.05)
    assert not task.done(), "must wait while its view is still mounting"
    consumer._runtime.view_instance = object()  # a different mount took over
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_loop_stops_when_another_view_is_mounted_on_the_socket():
    """A second mount on the same socket starts its own tick; the old loop
    must not keep ticking the new view (it did on main: double ticks)."""
    consumer = LiveViewConsumer()
    first = object()
    consumer._runtime = _Runtime(first)
    consumer.view_instance = first
    beats = []

    async def tick_once():
        beats.append(consumer.view_instance)
        return False

    consumer._tick_once = tick_once
    task = asyncio.ensure_future(consumer._run_tick(5))
    await asyncio.sleep(0.05)
    assert beats and all(v is first for v in beats)
    consumer.view_instance = consumer._runtime.view_instance = object()
    await asyncio.wait_for(task, timeout=1)


async def _no_tick():
    return False
