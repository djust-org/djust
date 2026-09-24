"""#2963: the ``async_pending`` flag must announce ``start_async`` work.

``start_async`` (and ``@background``, which calls it) queues into
``_async_tasks``, but every site that set the ``async_pending`` wire flag read
only the legacy ``_async_pending`` attribute, which nothing sets. So
``dj-loading`` / ``dj-disable-with`` / ``dj-lock`` ended at the event's first
reply while the work was still running. The flag now reads both, through one
helper, and the work's ``source="async"`` result frame (which carries
``event_name``) ends the loading state, including when the work raises and the
view has no ``handle_async_result``.

Client side: an existing frame field. ``async_pending: true`` keeps loading on
(02-response-handler.js, 03-websocket.js noop arm, 03b-sse.js), pinned by
tests/js/loading-states.test.js and tests/js/sse.test.js.
"""

import asyncio
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

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


class SpinnerView(LiveView):
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

    def _work(self):
        self.status = "done"

    def _fail(self):
        raise RuntimeError("background work failed")


def _session():
    session = SessionStore()
    session.save()
    return session


async def _mounted():
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to({"type": "mount", "view": __name__ + ".SpinnerView", "url": "/p/"})
    assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
    return socket


async def _frames(socket, quiet=0.6):
    frames = []
    while not await socket.receive_nothing(timeout=quiet):
        frames.append(json.loads((await socket.receive_output(timeout=3))["text"]))
    return frames


async def _event(socket, name, ref):
    await socket.send_json_to({"type": "event", "event": name, "params": {}, "ref": ref})
    await asyncio.sleep(0.05)
    return await _frames(socket)


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
            frames = await _event(socket, event, 1)
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
            frames = await _event(socket, "plain", 2)
            assert "async_pending" not in _reply(frames, 2)
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_failing_work_without_handler_still_ends_loading():
    """The announced loading state must end even when the work raises."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted()
        try:
            frames = await _event(socket, "start_failing", 3)
            assert _reply(frames, 3).get("async_pending") is True
            results = [f for f in frames if f.get("source") == "async"]
            assert results and results[-1]["event_name"] == "start_failing", frames
        finally:
            await socket.disconnect()


class _Bare:
    pass


def test_helper_reads_both_formats():
    view = _Bare()
    assert has_pending_async_work(None) is False
    assert has_pending_async_work(view) is False
    view._async_tasks = {}
    assert has_pending_async_work(view) is False
    view._async_tasks = {"t": (lambda: None, (), {})}
    assert has_pending_async_work(view) is True
    view._async_tasks = {}
    view._async_pending = (lambda: None, (), {})
    assert has_pending_async_work(view) is True
