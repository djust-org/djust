"""#3001: a ``server_push`` that finds the session busy is deferred, not dropped.

``server_push`` yields to user events (#560): it skipped when a user event (or
a background result, which borrows the same event context) was in progress,
or when the render lock stayed held past 0.1 s. Nothing re-queued the push.
For a stream of pushes the next one repaired the screen, but the LAST push of
a change was lost for good (a game's "match over" frame). A busy push is now
queued and replayed, in order, once the lock frees.
"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.push import view_group_name
from djust.websocket import LiveViewConsumer

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})
CONSUMERS = []


class RecordingConsumer(LiveViewConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


class ScoreView(LiveView):
    template = "<div dj-root><b>{{ score }}</b></div>"

    def mount(self, request, **kwargs):
        self.score = 0
        self.log = []

    def get_context_data(self, **kwargs):
        return {"score": self.score}

    def handle_score(self, score=0, **kwargs):
        self.log.append(score)
        self.score = score


VIEW = __name__ + ".ScoreView"


def _session():
    session = SessionStore()
    session.save()
    return session


async def _mounted():
    socket = WebsocketCommunicator(RecordingConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to({"type": "mount", "view": VIEW, "url": "/s/"})
    assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
    return socket, CONSUMERS[-1]


async def _push(score):
    await get_channel_layer().group_send(
        view_group_name(VIEW),
        {"type": "server_push", "handler": "handle_score", "payload": {"score": score}},
    )


async def _frames(socket, quiet=0.4):
    frames = []
    while not await socket.receive_nothing(timeout=quiet):
        frames.append(json.loads((await socket.receive_output(timeout=3))["text"]))
    return frames


@pytest.fixture(autouse=True)
def _reset():
    CONSUMERS.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_push_while_lock_held_arrives_after_release():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket, consumer = await _mounted()
        try:
            await consumer._render_lock.acquire()  # e.g. a slow background render
            try:
                await _push(7)
                assert await _frames(socket) == [], "nothing renders while the lock is held"
                assert consumer.view_instance.log == []
            finally:
                consumer._render_lock.release()
            frames = await _frames(socket)
            broadcasts = [f for f in frames if f.get("source") == "broadcast"]
            assert len(broadcasts) == 1, frames
            assert "7" in json.dumps(broadcasts[0]["patches"])
            assert consumer.view_instance.log == [7]
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_push_during_user_event_is_deferred_and_order_is_kept():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket, consumer = await _mounted()
        try:
            await consumer._render_lock.acquire()
            consumer._processing_user_event = True
            try:
                for score in (1, 2, 3):
                    await _push(score)
                await asyncio.sleep(0.3)
                assert consumer.view_instance.log == []
            finally:
                consumer._processing_user_event = False
                consumer._render_lock.release()
            # The lock is free but the drain has not run yet (no await since
            # the release): a push arriving now joins the queue behind the
            # earlier ones instead of overtaking them.
            assert len(consumer._deferred_pushes) == 3
            await consumer.server_push(
                {"type": "server_push", "handler": "handle_score", "payload": {"score": 4}}
            )
            assert len(consumer._deferred_pushes) == 4
            frames = await _frames(socket)
            assert consumer.view_instance.log == [1, 2, 3, 4]
            # The backlog is applied in one turn: one render, not four, so a
            # push stream faster than the render cannot leave a viewer behind.
            broadcasts = [f for f in frames if f.get("source") == "broadcast"]
            assert len(broadcasts) == 1, frames
            assert "4" in json.dumps(broadcasts[0]["patches"])
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
async def test_queue_is_bounded_and_keeps_the_newest():
    consumer = LiveViewConsumer()
    consumer.view_instance = object()
    await consumer._render_lock.acquire()
    try:
        cap = consumer._deferred_pushes.maxlen
        for i in range(cap + 5):
            consumer._defer_server_push({"n": i})
        kept = [event["n"] for _, event in consumer._deferred_pushes]
        assert len(kept) == cap
        assert kept[-1] == cap + 4, "the latest push must survive"
    finally:
        consumer._cancel_deferred_pushes()
        consumer._render_lock.release()


@pytest.mark.asyncio
async def test_identical_pushes_coalesce_and_distinct_ones_keep_order():
    """A clock pushing faster than the render must not build a backlog."""
    consumer = LiveViewConsumer()
    consumer.view_instance = object()
    await consumer._render_lock.acquire()
    try:
        refresh = {"type": "server_push", "handler": "handle_refresh", "payload": {"room": "a"}}
        other = {"type": "server_push", "handler": "handle_refresh", "payload": {"room": "b"}}
        for event in (refresh, other, dict(refresh), dict(refresh)):
            consumer._defer_server_push(event)
        queued = [event["payload"]["room"] for _, event in consumer._deferred_pushes]
        assert queued == ["b", "a"], "one entry per distinct push, at its latest position"
    finally:
        consumer._cancel_deferred_pushes()
        consumer._render_lock.release()


@pytest.mark.asyncio
async def test_push_queued_for_a_replaced_view_is_dropped():
    consumer = LiveViewConsumer()
    old_view = object()
    consumer.view_instance = old_view
    ran = []

    async def run_turn(event):
        ran.append(event)
        consumer._render_lock.release()

    consumer._run_server_push_turn = run_turn
    await consumer._render_lock.acquire()
    consumer._defer_server_push({"n": 1})
    consumer.view_instance = object()  # live_redirect mounted another view
    consumer._render_lock.release()
    await asyncio.wait_for(consumer._push_drain_task, timeout=1)
    assert ran == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_disconnect_cancels_the_drain():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket, consumer = await _mounted()
        await consumer._render_lock.acquire()
        try:
            await _push(9)
            await asyncio.sleep(0.2)
            drain = consumer._push_drain_task
            assert drain is not None and not drain.done(), "the drain waits on the lock"
            await socket.disconnect()
            assert drain.cancelled()
            assert consumer._push_drain_task is None
            assert len(consumer._deferred_pushes) == 0
        finally:
            consumer._render_lock.release()
