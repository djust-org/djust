"""#2963: the ``async_pending`` flag must announce ``start_async`` work.

``start_async`` (and ``@background``, which calls it) queues into
``_async_tasks``, but every site that set the ``async_pending`` wire flag read
only the legacy ``_async_pending`` attribute, which nothing sets. So
``dj-loading`` / ``dj-disable-with`` / ``dj-lock`` ended at the event's first
reply while the work was still running. The flag now reads ``_async_tasks``
through one helper (2.0 removed the legacy attribute's readers), and the work's ``source="async"`` result frame (which carries
``event_name``) ends the loading state, including when the work raises and the
view has no ``handle_async_result``.

Client side: an existing frame field. ``async_pending: true`` keeps loading on
(02-response-handler.js, 03-websocket.js noop arm, 03b-sse.js), pinned by
tests/js/loading-states.test.js and tests/js/sse.test.js.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.mixins.async_work import has_pending_async_work
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, receive_until

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


class SpinnerView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ status }}</span></div>"

    def mount(self, request, **kwargs):
        self.status = "idle"

    def get_context_data(self, **kwargs):
        return {"status": self.status}

    @event_handler()
    def start(self, **kwargs):
        self.status = "working"
        self.start_async(self._work)

    @event_handler()
    def start_quiet(self, **kwargs):
        # No state change: the noop arm.
        self.start_async(self._work)

    @event_handler()
    def start_failing(self, **kwargs):
        self.start_async(self._fail)

    @event_handler()
    def plain(self, **kwargs):
        self.status = "plain"

    @event_handler()
    def start_slow(self, **kwargs):
        self.status = "working"
        self.start_async(self._slow, name="job")

    @event_handler()
    def stop(self, **kwargs):
        self.cancel_async("job")
        self.status = "cancelled"

    def _slow(self):
        import time

        time.sleep(0.3)
        self.status = "done"

    def _work(self):
        self.status = "done"

    def _fail(self):
        raise RuntimeError("background work failed")


def _session():
    session = SessionStore()
    session.save()
    return session


async def _mounted(view="SpinnerView"):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": __name__ + "." + view, "url": "/p/"})
    assert (await socket.receive_json_from())["type"] == "mount"
    return socket


async def _frames(socket, until):
    """Frames until ``until(frames)`` holds (event-driven, #3130), then any
    others before the socket goes quiet. The trailing window can only miss a
    late extra frame, never cut the expected one short."""
    frames = await receive_until(socket, until)
    return frames + await drain_extra(socket)


def _replied(ref, *, async_of=None):
    """``until``: the event's reply arrived and, with ``async_of``, the
    ``source="async"`` frame that ends that event's loading state."""

    def done(frames):
        if not any(f.get("ref") == ref for f in frames):
            return False
        return async_of is None or any(
            f.get("source") == "async" and f.get("event_name") == async_of for f in frames
        )

    return done


async def _event(socket, name, ref, *, work):
    await socket.send_json_to({"type": "event", "event": name, "params": {}, "ref": ref})
    return await _frames(socket, _replied(ref, async_of=name if work else None))


def _reply(frames, ref):
    replies = [f for f in frames if f.get("ref") == ref]
    assert len(replies) == 1, frames
    return replies[0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["start", "start_quiet"])
async def test_reply_announces_start_async_work_and_result_ends_it(event):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted()
        try:
            frames = await _event(socket, event, 1, work=True)
            assert _reply(frames, 1).get("async_pending") is True, frames
            results = [f for f in frames if f.get("source") == "async"]
            assert results, f"the result frame that ends loading never came: {frames}"
            assert results[-1]["event_name"] == event
            assert not results[-1].get("async_pending")
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reply_without_work_does_not_announce_it():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted()
        try:
            frames = await _event(socket, "plain", 2, work=False)
            assert "async_pending" not in _reply(frames, 2)
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_failing_work_without_handler_still_ends_loading():
    """The announced loading state must end even when the work raises: an
    explicit view ends it with the opaque ``async_complete`` frame of the
    reply's ``async_batch``, which names neither a callback nor the failure."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted()
        try:
            await socket.send_json_to(
                {"type": "event", "event": "start_failing", "params": {}, "ref": 3}
            )
            frames = await _frames(
                socket, lambda seen: any(f.get("type") == "async_complete" for f in seen)
            )
            reply = _reply(frames, 3)
            assert reply.get("async_pending") is True, frames
            (complete,) = [f for f in frames if f.get("type") == "async_complete"]
            assert complete["async_batch"] == reply["async_batch"], frames
            assert "background work failed" not in json.dumps(frames), frames
        finally:
            await socket.disconnect()


class _Bare:
    pass


def test_helper_reads_the_task_queue():
    view = _Bare()
    assert has_pending_async_work(None) is False
    assert has_pending_async_work(view) is False
    view._async_tasks = {}
    assert has_pending_async_work(view) is False
    view._async_tasks = {"t": (lambda: None, (), {})}
    assert has_pending_async_work(view) is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cancelled_work_still_ends_the_loading_state():
    """The documented Stop-button pattern: the start event's loading state,
    announced with async_pending, must end even though the task is cancelled."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted()
        try:
            await socket.send_json_to(
                {"type": "event", "event": "start_slow", "params": {}, "ref": 4}
            )
            await socket.send_json_to({"type": "event", "event": "stop", "params": {}, "ref": 5})
            frames = await _frames(socket, _replied(4, async_of="start_slow"))
            assert _reply(frames, 4).get("async_pending") is True, frames
            ends = [
                f
                for f in frames
                if f.get("source") == "async" and f.get("event_name") == "start_slow"
            ]
            assert ends, f"nothing ends start_slow's loading state: {frames}"
        finally:
            await socket.disconnect()
