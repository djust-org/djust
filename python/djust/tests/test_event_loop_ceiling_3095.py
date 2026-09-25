"""Less work on the asyncio event loop per frame (#3095).

With free-threaded CPython and ``LIVEVIEW_CONFIG["worker_threads"]``, the
event-loop thread is the single-process ceiling. These tests pin the
per-frame savings:

* no thread hop for the handler-permission check when the handler has no
  ``@permission_required``, nor for the object-permission check when the
  view does not override ``get_object`` (both are pure metadata checks
  then); any pool setting;
* the handler's ``inspect.signature`` is computed once per function;
* a free render lock is taken without arming a ``wait_for`` timer;
* pool on: Channels' per-frame ``close_old_connections`` hop is replaced by
  a check the session's thread runs before its next task;
* pool on: a tick's change-detection snapshots run in the ``handle_tick``
  hop, not on the loop.
"""

from __future__ import annotations

import asyncio
import gc
import json
import threading

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.decorators import event_handler, permission_required

pytest.importorskip("channels")

MOD = __name__
_SEEN: dict = {}


def _note(key):
    _SEEN.setdefault(key, []).append(threading.get_ident())


class _CeilingView(LiveView):
    template = f'<div dj-root dj-view="{MOD}._CeilingView"><b>{{{{ count }}}}</b></div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        _note("handler")
        self.count += 1

    @event_handler()
    def still(self, **kwargs):
        _note("handler")
        self._skip_render = True


class _TickView(LiveView):
    template = f'<div dj-root dj-view="{MOD}._TickView"><b>{{{{ count }}}}</b></div>'
    tick_interval = 30

    def mount(self, request, **kwargs):
        self.count = 0
        self.mode = request.GET.get("mode", "bump")

    def handle_tick(self):
        _note("tick")
        if self.mode == "skip":
            self._skip_render = True
        elif self.mode == "same":
            pass  # changes nothing: no render
        elif self.mode == "force":
            self._force_full_html = True
        elif self.count < 1:
            self.count += 1


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    _SEEN.clear()
    yield request.param
    _SEEN.clear()


@pytest.fixture
def spies(monkeypatch):
    import channels.consumer
    import django.db

    import djust.websocket as ws

    calls = {"aclose": 0}
    real_snapshot = ws._snapshot_assigns
    real_close = django.db.close_old_connections
    real_aclose = channels.consumer.aclose_old_connections

    def snapshot(view):
        _note("snapshot")
        return real_snapshot(view)

    def close_old_connections(**kwargs):
        _note("close_old_connections")
        return real_close(**kwargs)

    async def aclose_old_connections():
        calls["aclose"] += 1
        return await real_aclose()

    monkeypatch.setattr(ws, "_snapshot_assigns", snapshot)
    monkeypatch.setattr(django.db, "close_old_connections", close_old_connections)
    monkeypatch.setattr(channels.consumer, "aclose_old_connections", aclose_old_connections)
    return calls


async def _connect(view, query=""):
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

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to(
        {"type": "mount", "view": f"{MOD}.{view}", "url": f"/ceiling/?{query}"}
    )
    frame = await _receive_until(communicator, "mount")
    assert frame.get("type") == "mount", frame
    return communicator


async def _receive_until(communicator, wanted, *, tries=8, timeout=5):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") in wanted if isinstance(wanted, tuple) else last.get("type") == wanted:
            return last
    return last


# ---------------------------------------------------------------------------
# Event frames: the connection check and the permission checks
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_event_frame_makes_no_connection_check_hop_with_the_pool(pool, spies):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _connect("_CeilingView")
        try:
            _SEEN.clear()
            before = spies["aclose"]
            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
            frame = await _receive_until(comm, "patch")
            aclose_calls = spies["aclose"] - before
        finally:
            await comm.disconnect()
    assert "1" in json.dumps(frame["patches"])
    (handler,) = _SEEN["handler"]
    assert handler != loop_thread
    if pool:
        # The deferred check ran on the session's thread before the handler.
        assert aclose_calls == 0
        assert _SEEN["close_old_connections"][0] == handler
    else:
        assert aclose_calls == 1


def test_a_deferred_check_runs_once_before_the_next_task(monkeypatch):
    import django.db

    from djust import worker_pool

    checks = []
    monkeypatch.setattr(django.db, "close_old_connections", lambda: checks.append(1))
    slot = worker_pool._Slot(99)
    executor = worker_pool._SlotExecutor(slot)
    try:
        assert executor.submit(lambda: "a").result() == "a"
        assert checks == []
        slot.db_check_due = True
        assert executor.submit(lambda x: x * 2, 21).result() == 42
        assert checks == [1]
        assert executor.submit(lambda: "b").result() == "b"
        assert checks == [1], "one mark, one check"
    finally:
        executor.shutdown()


def test_a_failing_deferred_check_never_fails_the_task(monkeypatch, caplog):
    import django.db

    from djust import worker_pool

    def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(django.db, "close_old_connections", boom)
    slot = worker_pool._Slot(98)
    slot.db_check_due = True
    executor = worker_pool._SlotExecutor(slot)
    try:
        with caplog.at_level("WARNING", logger="djust.worker_pool"):
            assert executor.submit(lambda: "ok").result() == "ok"
    finally:
        executor.shutdown()
    assert "close_old_connections() failed" in caplog.text


def test_mark_db_check_due_is_false_outside_a_pool_session():
    from djust.worker_pool import mark_db_check_due

    assert mark_db_check_due() is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_undecorated_handler_makes_no_permission_hops(pool, monkeypatch):
    import djust.websocket_utils as wu

    hops = []
    real = wu.sync_to_async

    def counting(fn, *a, **k):
        hops.append(getattr(fn, "__name__", repr(fn)))
        return real(fn, *a, **k)

    monkeypatch.setattr(wu, "sync_to_async", counting)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _connect("_CeilingView")
        try:
            hops.clear()
            await comm.send_json_to({"type": "event", "event": "still", "params": {}, "ref": 1})
            frame = await _receive_until(comm, "noop")
        finally:
            await comm.disconnect()
    assert frame.get("ref") == 1
    assert "check_handler_permission" not in hops
    assert "check_object_permission" not in hops


@pytest.mark.asyncio
async def test_permission_and_object_checks_still_hop_when_they_can_touch_the_db(monkeypatch, rf):
    from unittest.mock import AsyncMock, MagicMock

    import djust.websocket_utils as wu

    hops = []
    real = wu.sync_to_async

    def counting(fn, *a, **k):
        hops.append(getattr(fn, "__name__", repr(fn)))
        return real(fn, *a, **k)

    monkeypatch.setattr(wu, "sync_to_async", counting)

    class _Guarded(LiveView):
        template = "<div dj-root>x</div>"

        def get_object(self):
            return None

        @permission_required("auth.view_user")
        @event_handler()
        def guarded(self, **kwargs):
            pass

    view = _Guarded()
    request = rf.get("/")
    request.user = MagicMock(has_perms=MagicMock(return_value=True))
    view.request = request
    ws = MagicMock(send_error=AsyncMock(), close=AsyncMock(), _client_ip="127.0.0.1")
    handler = await wu._validate_event_security(ws, "guarded", view, MagicMock())
    assert handler is not None
    assert hops == ["check_handler_permission", "check_object_permission"]


# ---------------------------------------------------------------------------
# Signature cache
# ---------------------------------------------------------------------------


def test_the_handler_signature_is_computed_once_per_function(monkeypatch):
    import inspect

    from djust import validation

    calls = []
    real = inspect.signature

    def counting(obj, *a, **k):
        calls.append(obj)
        return real(obj, *a, **k)

    class _V:
        def handler(self, amount: int = 0, **kwargs):
            pass

    monkeypatch.setattr(validation.inspect, "signature", counting)
    a, b = _V(), _V()
    for v in (a, b, a):
        result = validation.validate_handler_params(v.handler, {"amount": "3"}, "handler")
        assert result["valid"], result
    assert len(calls) == 1, "one signature per function, shared by instances"
    bound = validation._handler_signature(a.handler)
    unbound = validation._handler_signature(_V.handler)
    assert "self" not in bound.parameters and "self" in unbound.parameters


def test_the_signature_cache_does_not_keep_functions_alive():
    from djust import validation

    def make():
        def handler(self, x=1):
            pass

        return handler

    import weakref

    fn = make()
    validation._handler_signature(fn)
    assert fn in validation._SIGNATURES
    ref = weakref.ref(fn)
    del fn
    gc.collect()
    assert ref() is None, "the cache kept the function alive"


# ---------------------------------------------------------------------------
# Render lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_free_render_lock_is_taken_without_a_timer(monkeypatch):
    import djust.websocket as ws

    async def no_wait_for(*a, **k):
        raise AssertionError("wait_for used for a free lock")

    lock = asyncio.Lock()
    monkeypatch.setattr(ws.asyncio, "wait_for", no_wait_for)
    await ws._acquire_render_lock(lock, 0.1)
    assert lock.locked()
    lock.release()


@pytest.mark.asyncio
async def test_a_held_render_lock_still_times_out():
    import djust.websocket as ws

    lock = asyncio.Lock()
    await lock.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await ws._acquire_render_lock(lock, 0.05)
    lock.release()


@pytest.mark.asyncio
async def test_a_free_lock_with_a_queued_waiter_goes_through_wait_for(monkeypatch):
    """Released but a woken waiter is about to take it: acquire() would queue
    behind it, so the timeout must still apply."""
    import djust.websocket as ws

    lock = asyncio.Lock()
    await lock.acquire()
    waiter = asyncio.ensure_future(lock.acquire())
    await asyncio.sleep(0)  # the waiter is queued
    used = []
    real = ws.asyncio.wait_for

    async def spy(aw, timeout):
        used.append(timeout)
        return await real(aw, timeout)

    monkeypatch.setattr(ws.asyncio, "wait_for", spy)
    lock.release()  # unlocked, but the waiter is next
    with pytest.raises(asyncio.TimeoutError):
        await ws._acquire_render_lock(lock, 0.05)
    assert used == [0.05]
    await waiter
    lock.release()


# ---------------------------------------------------------------------------
# Ticks
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tick_snapshots_run_with_handle_tick_with_the_pool(pool, spies):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _connect("_TickView", "mode=bump")
        try:
            _SEEN.clear()
            frame = await _receive_until(comm, "patch")
        finally:
            await comm.disconnect()
    assert frame.get("source") == "tick"
    assert "1" in json.dumps(frame["patches"])
    tick = _SEEN["tick"][0]
    first_snapshot = _SEEN["snapshot"][0]
    assert tick != loop_thread
    if pool:
        assert first_snapshot == tick
    else:
        assert first_snapshot == loop_thread


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["skip", "same"])
async def test_a_tick_that_changes_nothing_sends_nothing(pool, mode):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _connect("_TickView", f"mode={mode}")
        try:
            _SEEN.clear()
            for _ in range(200):  # until a few ticks ran
                if len(_SEEN.get("tick", [])) >= 3:
                    break
                await asyncio.sleep(0.02)
            assert len(_SEEN.get("tick", [])) >= 3
            assert await comm.receive_nothing(timeout=0.2)
        finally:
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_tick_that_forces_a_render_renders(pool):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        comm = await _connect("_TickView", "mode=force")
        try:
            frame = await _receive_until(comm, ("patch", "html_update"))
        finally:
            await comm.disconnect()
    assert frame.get("source") == "tick", frame
