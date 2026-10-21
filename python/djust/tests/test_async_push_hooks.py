"""An ``async def`` server-push hook runs on the event loop.

A push hook (``handle_*``, ``@event_handler`` or ``@push_handler``) used to be
called through ``sync_to_async`` on the stock path and called bare on the
worker-pool thread (#3074), so an ``async def`` hook raised
``TypeError: sync_to_async can only be applied to sync functions`` on one path
and, on the other, returned a coroutine that never ran. Push hooks therefore
had to be sync, and a hook that wanted to await (an async ORM read, an async
service) had to build its data elsewhere and assign it in the sync hook.

Now an async hook is awaited on the event loop, on both paths; a sync hook
keeps running off the loop as before. Each test runs against a real
``WebsocketCommunicator`` session, with the worker pool off and on.
"""

from __future__ import annotations

import json
import threading

import pytest
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.decorators import event_handler
from djust.push import apush_to_view

pytest.importorskip("channels")

VIEW = f"{__name__}._PushView"
_SEEN: dict = {}
_BUSY: dict = {}


class _PushView(LiveView):
    exposure_policy = "legacy"
    template = (
        '<div dj-root dj-view="djust.tests.test_async_push_hooks._PushView">'
        "<i>{{ label }}</i></div>"
    )

    def mount(self, request, **kwargs):
        self.label = "start"
        _BUSY["view"] = self

    async def handle_async_push(self, label: str = "", **kwargs):
        _SEEN.setdefault("async_hook", []).append(threading.get_ident())
        # A hook that awaits: proves the coroutine body runs to completion.
        await _yield()
        self.label = label

    @event_handler()
    async def hold(self, **kwargs):
        """Keep the session busy until the test opens the gate."""
        _BUSY["inside"].set()
        await _BUSY["gate"].wait()

    def handle_sync_push(self, label: str = "", **kwargs):
        _SEEN.setdefault("sync_hook", []).append(threading.get_ident())
        self.label = label


async def _yield():
    import asyncio

    await asyncio.sleep(0)


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    _SEEN.clear()
    yield request.param
    _SEEN.clear()


async def _receive_until(communicator, wanted, *, tries=8, timeout=5):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted:
            return last
    return last


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to({"type": "mount", "view": VIEW, "url": "/push/"})
    frame = await _receive_until(communicator, "mount")
    assert frame.get("type") == "mount", frame
    return communicator


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_push_hook_runs_on_the_loop(pool):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            await apush_to_view(VIEW, handler="handle_async_push", payload={"label": "awaited"})
            frame = await _receive_until(comm, "patch")
        finally:
            await comm.disconnect()
    assert frame.get("type") == "patch", frame
    assert frame["source"] == "broadcast"
    assert "awaited" in json.dumps(frame["patches"])
    assert _SEEN["async_hook"] == [loop_thread]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sync_push_hook_still_runs_off_the_loop(pool):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            await apush_to_view(VIEW, handler="handle_sync_push", payload={"label": "threaded"})
            frame = await _receive_until(comm, "patch")
        finally:
            await comm.disconnect()
    assert "threaded" in json.dumps(frame["patches"])
    (hook_thread,) = _SEEN["sync_hook"]
    assert hook_thread != loop_thread


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_and_sync_hooks_in_one_drained_turn(pool):
    """Pushes that arrive while the session is busy are drained in one turn
    (#3001): an async hook among them must not send that turn to the worker
    thread, where its coroutine would never run."""
    import asyncio

    loop_thread = threading.get_ident()
    _BUSY["gate"] = asyncio.Event()
    _BUSY["inside"] = asyncio.Event()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            await comm.send_json_to({"type": "event", "event": "hold", "params": {}, "ref": 1})
            await asyncio.wait_for(_BUSY["inside"].wait(), timeout=5)
            # The event turn holds the session: both pushes are deferred.
            await apush_to_view(VIEW, handler="handle_sync_push", payload={"label": "first"})
            await apush_to_view(VIEW, handler="handle_async_push", payload={"label": "second"})
            _BUSY["gate"].set()
            frame = await _receive_until_text(comm, "second")
        finally:
            _BUSY["gate"].set()
            await comm.disconnect()
    assert "second" in json.dumps(frame), frame
    assert _SEEN["async_hook"] == [loop_thread]
    assert len(_SEEN["sync_hook"]) == 1


async def _receive_until_text(communicator, text, *, tries=8, timeout=5):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if text in json.dumps(last):
            return last
    return last


def _only_consumer():
    """The consumer of the view this test mounted last."""
    consumer = _BUSY["view"]._ws_consumer
    assert consumer is not None and consumer.view_instance is _BUSY["view"]
    return consumer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_handle_info_runs_on_the_loop():
    """``handle_info`` (a database NOTIFY) is the same kind of hook."""
    loop_thread = threading.get_ident()
    seen = []

    class _InfoView(_PushView):
        async def handle_info(self, message):
            seen.append(threading.get_ident())
            await _yield()
            self.label = message["payload"]

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            consumer = _only_consumer()
            consumer.view_instance.__class__ = _InfoView
            await consumer.db_notify({"type": "db_notify", "channel": "c", "payload": "notified"})
            frame = await _receive_until(comm, "patch")
        finally:
            await comm.disconnect()
    assert seen == [loop_thread]
    assert "notified" in json.dumps(frame["patches"])
