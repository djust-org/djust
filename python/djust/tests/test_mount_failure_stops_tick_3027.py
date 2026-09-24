"""#3027: a view whose mount fails must not keep its tick task running.

``on_view_mounted`` starts the tick task before ``mount()`` runs. When
``mount()`` (or ``handle_params()``) then raised, the runtime sent the error
frame and returned, but the task kept calling ``handle_tick`` on the
half-mounted view every beat until the socket closed.
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


class MountRaisesTickView(LiveView):
    template = "<div dj-root>{{ n }}</div>"
    tick_interval = 20

    def mount(self, request, **kwargs):
        time.sleep(0.1)  # let the tick's first beats land during mount (#2945 wait)
        raise RuntimeError("mount failed on purpose")

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    def handle_tick(self):
        TICKS.append(1)
        self.n += 1  # AttributeError on the half-mounted view


class HandleParamsRaisesTickView(MountRaisesTickView):
    def mount(self, request, **kwargs):
        self.n = 0

    def handle_params(self, params, uri):
        raise RuntimeError("handle_params failed on purpose")


class HealthyTickView(MountRaisesTickView):
    def mount(self, request, **kwargs):
        self.n = 0


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


async def _mount_and_count_ticks(view_name):
    TICKS.clear()
    socket = await _socket()
    try:
        await socket.send_json_to(
            {"type": "mount", "view": f"{__name__}.{view_name}", "url": "/t/"}
        )
        first = await socket.receive_json_from(timeout=3)
        before = len(TICKS)
        await asyncio.sleep(0.2)  # ten beats of a live tick
        return first, len(TICKS) - before
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("view_name", ["MountRaisesTickView", "HandleParamsRaisesTickView"])
async def test_failed_mount_stops_its_tick(view_name):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        first, ticks_after = await _mount_and_count_ticks(view_name)
    assert first["type"] == "error", first
    assert ticks_after == 0, f"handle_tick ran {ticks_after} times after the failed mount"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_healthy_mount_still_ticks():
    """Control: the same view with a working mount keeps ticking, so the test
    above is not green merely because ticks never run in this harness."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        first, ticks_after = await _mount_and_count_ticks("HealthyTickView")
    assert first["type"] == "mount", first
    assert ticks_after > 0


@pytest.mark.asyncio
async def test_hook_leaves_another_views_tick_alone():
    """A tick task started for a later mount is not cancelled by an earlier
    view's failure report."""
    from djust.runtime import WSConsumerTransport

    class _Consumer:
        pass

    consumer = _Consumer()
    task = asyncio.ensure_future(asyncio.sleep(10))
    other, failed = object(), object()
    task._djust_tick_view = other
    consumer._tick_task = task
    await WSConsumerTransport(consumer).on_mount_failed(failed)
    assert not task.done()
    await WSConsumerTransport(consumer).on_mount_failed(other)
    assert task.cancelled()
    assert consumer._tick_task is None
