"""#4098: what a slow explicit state save holds, drops, queues and escalates.

``test_rapid_events_persisted_4098.py`` pins the good case: a save that lands
within ``DJUST_EXPLICIT_STATE_SAVE_TIMEOUT`` (2 s by default) keeps its
event's own frame. These are characterization tests of everything around it,
so the cost of waiting is written down rather than discovered:

* what else is held while one save is in storage (other WebSocket sessions,
  ticks, ``db_notify``, pushes);
* what a save slower than the deadline does to a backlog of events (the
  deferral path, the escalation to the reload error, the one catch-up);
* the per-view lane cap on a socket with several views (``view_busy``);
* an SSE save that cannot get a pool thread.

Saves are held by ``SaveGate`` (a latch inside ``SessionStore.save``); the
only clocks are absence windows (``HOLD_S``) and deadlines the test itself
shortens so the wait is for a frame that WILL arrive.

This module deliberately does NOT start with ``test_exposure_`` (#3130).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, List

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler
from djust import runtime as runtime_module
from djust._exposure_sessions import MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S
from djust.decorators import state
from djust.tests import test_rapid_events_persisted_4098 as rapid
from djust.tests._ws_frames import drain_extra, has_frame, receive_until
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_rapid_events_persisted_4098 import (
    HOLD_S,
    RAN,
    SaveGate,
    _answers,
    _event,
    _mount_runtime,
    _shown,
    _stored,
)
from djust.websocket import LiveViewConsumer

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.pr_tier]

MODULE = __name__

#: Every consumer a test connected, in order.
CONSUMERS: List[Any] = []


class Live(LiveView):
    """A persisted counter that also ticks, takes pushes and hears NOTIFY."""

    exposure_policy = "explicit"
    template = '<div dj-root><span id="count">{{ count }}</span></div>'
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self._ticks = 0
        self._infos = 0

    @event_handler()
    def increment(self, **kwargs: Any) -> None:
        self.count += 1
        RAN.append(self.count)

    @event_handler()
    def pushed(self, **kwargs: Any) -> None:
        self.count += 10

    def handle_tick(self) -> None:
        self._ticks += 1

    def handle_info(self, message: Any) -> None:
        self._infos += 1


class Slot(LiveView):
    """A persisted counter mounted beside the page view on one socket."""

    exposure_policy = "explicit"
    template = '<div dj-view="' + MODULE + '.Slot"><span id="count">{{ count }}</span></div>'
    count = state(0, persist="server")

    @event_handler()
    def increment(self, **kwargs: Any) -> None:
        self.count += 1
        RAN.append(self.count)


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    RAN.clear()
    CONSUMERS.clear()
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[MODULE, rapid.MODULE, "djust"], DEBUG=False, DJUST_TENANTS=None
    ):
        yield


class _Recorded(LiveViewConsumer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


async def _open(view: str, key=None, target=None):
    request = await sync_to_async(make_request)(key)
    socket = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await _mount(socket, view, target)
    return socket, request.session.session_key


async def _mount(socket, view: str, target=None) -> None:
    frame = {"type": "mount", "view": view, "url": "/runtime-explicit/"}
    if target:
        frame["target_id"] = target
    await socket.send_json_to(frame)
    await receive_until(socket, has_frame("mount"), what="the mount frame")


# ------------------------------------------------------------------ #
# (a) What one slow save occupies
# ------------------------------------------------------------------ #


@override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S)
async def test_one_slow_websocket_save_holds_every_websocket_session_by_default():
    """Two sockets of two different sessions; the first one's save is in storage.

    A WebSocket save runs on the session's synchronous thread. With the default
    ``worker_threads = None`` that is ONE thread for the process, so the other
    session's handler cannot start until the write returns. This is so before
    and after #4098 and at any deadline (a sync write cannot be abandoned); it
    is pinned so the scaling guide's statement of it stays true. SSE saves use
    their own pool and do not hold other sessions:
    ``test_state_save_pool_3212.test_one_hung_store_write_does_not_stall_other_sessions``.
    """
    a, key_a = await _open(rapid.MODULE + ".Counter")
    b, key_b = await _open(rapid.MODULE + ".Counter")
    assert key_a != key_b
    try:
        with SaveGate(hold={1}) as gate:
            await a.send_json_to(_event(1))
            await gate.wait_entered()
            await b.send_json_to(_event(1))
            assert await b.receive_nothing(timeout=HOLD_S, interval=0.01)
            assert RAN == [1], "the other session's handler has not started"
            gate.release()
            a_frames = await receive_until(a, has_frame(ref=1), what="A's frame")
            b_frames = await receive_until(b, has_frame(ref=1), what="B's frame")
    finally:
        await a.disconnect()
        await b.disconnect()

    assert _answers(a_frames) == [("patch", 1, 1)], a_frames
    assert _answers(b_frames) == [("patch", 1, 1)], b_frames
    assert await _stored(key_a) == 1 and await _stored(key_b) == 1


# ------------------------------------------------------------------ #
# (b) Server-originated turns while a save is held
# ------------------------------------------------------------------ #


@override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S)
async def test_a_tick_and_a_notify_are_skipped_and_a_push_is_queued_while_a_save_is_held():
    socket, key = await _open(MODULE + ".Live")
    (consumer,) = CONSUMERS
    view = consumer.view_instance
    try:
        with SaveGate(hold={1}) as gate:
            await socket.send_json_to(_event(1))
            await gate.wait_entered()
            # The event turn is in its save: each of these returns at once.
            assert await consumer._tick_once() is False
            await consumer._db_notify_to_view({"channel": "c", "payload": {}})
            await consumer._server_push_to_view({"handler": "pushed", "payload": {}})
            assert (view._ticks, view._infos, view.count) == (0, 0, 1)
            assert len(consumer._deferred_pushes) == 1
            assert await socket.receive_nothing(timeout=HOLD_S, interval=0.01)
            gate.release()
            frames = await receive_until(
                socket, lambda fs: any(_shown(f) == 11 for f in fs), what="the queued push"
            )
            frames += await drain_extra(socket)
    finally:
        await socket.disconnect()

    # The event's own frame first, then the push that waited for it. The tick
    # and the notification were skipped for good: nothing replays them.
    shown = [(f.get("source"), _shown(f)) for f in frames if f.get("type") == "patch"]
    assert shown == [("event", 1), ("broadcast", 11)], frames
    assert (view._ticks, view._infos) == (0, 0)
    assert not [f for f in frames if f.get("type") == "error"], frames


# ------------------------------------------------------------------ #
# (c) A backlog behind a save slower than the deadline
# ------------------------------------------------------------------ #


async def test_over_a_websocket_a_backlog_behind_a_slow_save_waits_and_does_not_escalate():
    """Three events; the first one's save outruns a short deadline in storage.

    Event 1 is deferred (transient). The write holds the session's synchronous
    thread, and reading a frame needs that thread too (the connection cleanup
    before each dispatch), so events 2 and 3 are not even read until the write
    returns: over a WebSocket a backlog behind ONE slow write neither runs nor
    counts as further deferrals. Afterwards every event has run once, in
    order, the page ends on the newest state and storage has it.
    """
    socket, key = await _open(rapid.MODULE + ".Counter")
    try:
        with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05):
            with SaveGate(hold={1}) as gate:
                for ref in (1, 2, 3):
                    await socket.send_json_to(_event(ref))
                await gate.wait_entered()
                frames = await receive_until(socket, has_frame("error"), what="the deferral")
                assert await socket.receive_nothing(timeout=HOLD_S, interval=0.01)
                assert RAN == [1], "events behind the write have not been read"
                gate.release()
                frames += await receive_until(socket, has_frame(ref=3), what="event 3's frame")
                frames += await drain_extra(socket)
    finally:
        await socket.disconnect()

    errors = [f for f in frames if f.get("type") == "error"]
    assert [bool(e.get("transient")) for e in errors] == [True], errors
    assert RAN == [1, 2, 3]
    rendered = [f for f in frames if f.get("type") in {"patch", "html_update"}]
    assert [f.get("ref") for f in rendered if f.get("ref")] == [2, 3], frames
    assert [_shown(f) for f in rendered] == sorted(_shown(f) for f in rendered)
    assert _shown(rendered[-1]) == 3
    assert gate.landed == sorted(gate.landed) and gate.landed[-1] == 3
    assert await _stored(key) == 3


async def test_turns_on_other_threads_behind_a_stuck_save_escalate_to_the_reload_error():
    """The same backlog where each turn has a thread of its own (the SSE shape).

    Every event POST over SSE runs in its own thread-sensitive context, so a
    turn can start while an earlier save is still in storage. Every handler
    runs, in order. Event 1 is deferred (transient). Events 2 and 3 wait the
    deadline for that save, do not start their own, and are deferred as well;
    the third deferral in a row is the reload error. When storage answers,
    the one catch-up saves the newest state and sends it as full HTML: nothing
    is lost, and the older write does not land over the newer one.
    """
    from asgiref.sync import SyncToAsync

    assert runtime_module._EXPLICIT_MAX_DEFERRALS == 3
    runtime, transport, request = await _mount_runtime()
    var = SyncToAsync.thread_sensitive_context
    contexts = [ThreadSensitiveContext() for _ in range(3)]
    try:
        with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05):
            with SaveGate(hold={1}) as gate:
                for ref, context in zip((1, 2, 3), contexts):
                    token = var.set(context)
                    turn = asyncio.ensure_future(runtime.dispatch_event(_event(ref)))
                    var.reset(token)
                    await asyncio.wait({turn}, timeout=60)
                    assert turn.done(), "a deferred turn returns at its deadline"
                assert RAN == [1, 2, 3]
                assert gate.calls == 1, "a save behind a running one must not start"
                assert [bool(e.get("transient")) for e in transport.errors] == [True, True, False]
                assert "reload" in transport.errors[-1]["error"].lower()
                assert not _answers(transport.sent)[3:], transport.sent
                gate.release()
                await asyncio.wait({runtime._explicit_catch_up}, timeout=60)
                assert runtime._explicit_catch_up.done()
    finally:
        for context in contexts:
            await context.__aexit__(None, None, None)

    assert len(transport.errors) == 3, transport.errors
    catch_up = [f for f in transport.sent if f.get("source") == "async"]
    assert [(f["type"], _shown(f)) for f in catch_up] == [("html_update", 3)], transport.sent
    # The held write (1) lands first, the catch-up's (3) after it.
    assert gate.landed == [1, 3]
    assert await _stored(request.session.session_key) == 3


@override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S)
async def test_frames_for_a_view_beside_the_page_are_not_read_while_a_save_is_held():
    """Several views on one socket; a view's save is in storage.

    Each view queues at most ``MAX_QUEUED_TURNS`` turns and refuses further
    frames with ``view_busy``
    (``test_multi_view_render_locks_3252``, with a slow ``async`` handler).
    A held WRITE does not reach that cap: it holds the synchronous thread the
    receive loop needs before it dispatches any frame, so later frames wait
    unread (in the server's buffer) and are answered in order afterwards.
    """
    socket, _ = await _open(rapid.MODULE + ".Counter")
    await _mount(socket, MODULE + ".Slot", "w")
    try:
        with SaveGate(hold={1}) as gate:
            await socket.send_json_to({**_event(1), "target_id": "w"})
            await gate.wait_entered()
            for ref in (2, 3, 4):
                await socket.send_json_to({**_event(ref), "target_id": "w"})
            await socket.send_json_to(_event(5))  # the page view
            assert await socket.receive_nothing(timeout=HOLD_S, interval=0.01)
            assert RAN == [1]
            assert CONSUMERS[0]._slot_map()["w"].facade._lane.queued() == 0
            gate.release()
            frames = await receive_until(
                socket,
                lambda fs: {f.get("ref") for f in fs} >= {1, 2, 3, 4, 5},
                what="every event's frame",
            )
    finally:
        await socket.disconnect()

    assert not [f for f in frames if f.get("type") == "error"], frames
    slot = [f for f in frames if f.get("target_id") == "w" and f.get("type") == "patch"]
    assert [(f["ref"], _shown(f)) for f in slot] == [(1, 1), (2, 2), (3, 3), (4, 4)], frames


# ------------------------------------------------------------------ #
# SSE: a save that cannot get a pool thread
# ------------------------------------------------------------------ #


async def test_a_request_scoped_save_queued_past_the_start_bound_is_deferred_then_caught_up(
    monkeypatch,
):
    """Every save-pool thread is busy (hung writes of other sessions).

    A request-scoped save (the SSE event POST) waits ``_SAVE_START_TIMEOUT_S``
    for a thread, then its turn is answered with the transient error: no
    storage time was used, so it is not the reload error whatever the
    deadline. It still runs when a thread frees, and the catch-up then sends
    the state as full HTML. The longest an SSE event can be held is therefore
    the start bound plus the save deadline. The bound is shortened here; the
    deadline is the default.
    """
    assert runtime_module._SAVE_START_TIMEOUT_S == 5.0
    one = runtime_module._SaveExecutor(max_workers=1, thread_name_prefix="test-4098-pool")
    monkeypatch.setattr(runtime_module, "_save_executor", one)
    monkeypatch.setattr(runtime_module, "_SAVE_START_TIMEOUT_S", 0.05)
    runtime, transport, request = await _mount_runtime()
    busy = threading.Event()
    one.submit(busy.wait, 60)
    try:
        async with ThreadSensitiveContext():  # what Django's ASGI handler wraps
            await runtime.dispatch_event(_event(1))
        [error] = transport.errors
        assert error["code"] == "state_error" and error.get("transient") is True, error
        assert not _answers(transport.sent)[1:] and RAN == [1]
        assert await _stored(request.session.session_key) is None, "the save has not run yet"
        busy.set()
        await asyncio.wait({runtime._explicit_catch_up}, timeout=60)
        assert runtime._explicit_catch_up.done()
        await asyncio.wait({runtime._explicit_save_pending}, timeout=60)
    finally:
        busy.set()
        one.shutdown()

    assert len(transport.errors) == 1, transport.errors
    catch_up = [f for f in transport.sent if f.get("source") == "async"]
    assert [(f["type"], _shown(f)) for f in catch_up] == [("html_update", 1)], transport.sent
    assert await _stored(request.session.session_key) == 1
