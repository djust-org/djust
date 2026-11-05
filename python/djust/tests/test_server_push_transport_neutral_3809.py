"""#3809 slice 1: ``self.server_push()``, ``self.live_transport`` and ``tick_interval`` over SSE.

A page that follows a long-running job with a background ``create_task`` and
renders each step with a push froze over SSE: the push, the tick and the
``db_notify`` wiring were WebSocket-only. Real transports only: a real
``WebsocketCommunicator`` for the socket, the real SSE stream and message views
for SSE. Ordering is asserted by event order, never by a duration.

Slice 1 covers the view's OWN push and tick. ``push_to_view`` groups, push
scopes and ``db_notify`` stay WebSocket-only over SSE (they need the channel
layer); ``test_groups_stay_websocket_only_over_sse`` pins that, so a later
slice that changes it has to change this test.
"""

import asyncio
import json
import threading
import uuid

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView, event_handler
from djust.decorators import state
from djust.config import config
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.websocket import LiveViewConsumer

from ._ws_frames import receive_until, wait_until
from .test_exposure_sse_resilience_3200 import _fresh_key, _request

pytestmark = pytest.mark.django_db(transaction=True)

#: ``live_transport`` as ``mount()`` saw it, one entry per mount.
MOUNTS = []
#: The step the recording sync_turn_wrapper has open on this thread.
_OPEN = threading.local()
WRAPPED = []


class PushPage(LiveView):
    template = "<div dj-root><b id='n'>{{ n }}</b><i id='t'>{{ transport }}</i></div>"

    def mount(self, request, **kwargs):
        self.n = 0
        self.log = []
        self.transport = self.live_transport or "none"
        MOUNTS.append(self.live_transport)

    def get_context_data(self, **kwargs):
        return {"n": self.n, "transport": self.transport}

    def handle_set(self, n=0, **kwargs):
        self.log.append(n)
        self.n = n
        self.step_seen = getattr(_OPEN, "step", None)

    def handle_kick(self, **kwargs):
        """A push handler that queues background work: it must run after the push."""
        self.start_async(self._kicked)

    def _kicked(self):
        self.kicked = True

    def secret(self, **kwargs):  # neither handle_*, nor @event_handler, nor @push_handler
        self.log.append("SECRET")

    @event_handler()
    async def follow(self, count=3, **kwargs):
        """The issue's repro: a background task that pushes every step."""
        loop = asyncio.get_running_loop()
        self._follower = loop.create_task(self._follow(count))

    async def _follow(self, count):
        for i in range(1, count + 1):
            await self.server_push("handle_set", {"n": i})
            await asyncio.sleep(0.005)  # pacing only; nothing asserts on it

    @event_handler()
    async def set_then_push(self, **kwargs):
        """Pushes from inside an event turn: must defer, not wait on its own lock."""
        self.n = 100
        self.pushed = await self.server_push("handle_set", {"n": 200})

    @event_handler()
    def push_from_sync_http(self, **kwargs):
        self.n = 7 if async_to_sync(self.server_push)("handle_set", {"n": 99}) is False else 8


class TickPage(LiveView):
    template = "<div dj-root><b id='t'>{{ ticks }}</b></div>"
    tick_interval = 10

    def mount(self, request, **kwargs):
        self.ticks = 0

    def get_context_data(self, **kwargs):
        return {"ticks": self.ticks}

    def handle_tick(self):
        self.ticks += 1
        self.tick_step = getattr(_OPEN, "step", None)


class QuietTickPage(TickPage):
    """Overrides ``handle_tick`` without changing state."""

    def handle_tick(self):
        pass


class NoTickPage(LiveView):
    """Sets ``tick_interval`` but does not override ``handle_tick``."""

    template = "<div dj-root>x</div>"
    tick_interval = 10


class ExplicitPushPage(LiveView):
    """An explicit-exposure (ADR-038) root: its push turn is authorized and committed."""

    exposure_policy = "explicit"
    template = "<div dj-root><span id='c'>{{ count }}</span></div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    def handle_bump(self, by=1, **kwargs):
        self.count += by


urlpatterns = [
    path("explicit/", ExplicitPushPage.as_view()),
    path("push/", PushPage.as_view()),
    path("tick/", TickPage.as_view()),
    path("quiet/", QuietTickPage.as_view()),
    path("notick/", NoTickPage.as_view()),
]
PUSH = "djust.tests.test_server_push_transport_neutral_3809.PushPage"
TICK = "djust.tests.test_server_push_transport_neutral_3809.TickPage"
QUIET = "djust.tests.test_server_push_transport_neutral_3809.QuietTickPage"
NOTICK = "djust.tests.test_server_push_transport_neutral_3809.NoTickPage"
EXPLICIT = "djust.tests.test_server_push_transport_neutral_3809.ExplicitPushPage"


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    MOUNTS.clear()
    WRAPPED.clear()
    with override_settings(
        ROOT_URLCONF=__name__,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
        DEBUG=False,
        DJUST_TENANTS=None,
    ):
        yield
    for session in list(_sse_sessions.values()):
        session.shutdown()
    _sse_sessions.clear()


@pytest.fixture
def wrapper():
    """A recording ``sync_turn_wrapper``: proves a push / tick turn's sync steps go through it."""
    import contextlib

    @contextlib.contextmanager
    def scope(view, step, turn):
        WRAPPED.append((type(view).__name__, step, getattr(turn, "kind", None)))
        _OPEN.step = step
        try:
            yield
        finally:
            _OPEN.step = None

    previous = config.get("sync_turn_wrapper")
    config.set("sync_turn_wrapper", scope)
    yield WRAPPED
    config.set("sync_turn_wrapper", previous)


# ---------------------------------------------------------------------------
# SSE and WebSocket harnesses
# ---------------------------------------------------------------------------


async def _sse(view_path=PUSH, url="/push/"):
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": view_path, "_djust_url": url}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]
    while not session.queue.empty():
        session.queue.get_nowait()
    return session, key


async def _sse_event(session, key, event, params=None):
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "event", "event": event, "params": params or {}},
        key,
    )
    return await DjustSSEMessageView().post(post, session_id=session.session_id)


async def _sse_frames(session, done, timeout=10.0):
    """Frames until ``done(frames)``; event-driven, so a slow machine only waits longer."""
    frames = []

    async def pump():
        while not done(frames):
            frame = await session.queue.get()
            if frame is None:
                return
            frames.append(frame)

    await asyncio.wait_for(pump(), timeout)
    return frames


def _broadcast(frames):
    return any(f.get("source") == "broadcast" for f in frames)


def _text(frame):
    return json.dumps(frame)


def _shows(n):
    """``done`` for a frame wait: a broadcast frame that carries the number ``n``."""
    return lambda frames: any(
        f.get("source") == "broadcast" and f'"text": "{n}"' in _text(f) for f in frames
    )


CONSUMERS = []


class RecordingConsumer(LiveViewConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


async def _ws(view_path=PUSH):
    CONSUMERS.clear()
    socket = WebsocketCommunicator(RecordingConsumer.as_asgi(), "/ws/")
    socket.scope.update(
        session=await sync_to_async(lambda: _saved_session())(), user=AnonymousUser(), tenant=None
    )
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": view_path, "url": "/push/"})
    assert (await socket.receive_json_from())["type"] == "mount"
    return socket, CONSUMERS[-1]


def _saved_session():
    session = SessionStore()
    session.save()
    return session


# ---------------------------------------------------------------------------
# (B) live_transport: answered inside mount() on every transport
# ---------------------------------------------------------------------------


def test_live_transport_is_none_on_the_http_render_and_a_fallback_post():
    client = Client()
    response = client.get("/push/")
    assert response.status_code == 200
    assert "<i id='t'>none</i>" in response.content.decode()
    assert MOUNTS == [None]

    response = client.post(
        "/push/",
        data=json.dumps({"event": "push_from_sync_http", "params": {}}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    # The fallback POST builds a fresh instance: nothing live is attached, and
    # the push it makes is a documented no-op that returns False (the handler
    # records 7 for False, 8 for a push that ran).
    body = response.json()
    assert '"text": "7"' in json.dumps(body) or "<b id='n'>7</b>" in json.dumps(body), body


async def test_live_transport_is_websocket_inside_mount():
    socket, consumer = await _ws()
    try:
        assert MOUNTS == ["websocket"]
        assert consumer.view_instance.live_transport == "websocket"
    finally:
        await socket.disconnect()


async def test_live_transport_is_sse_inside_mount():
    session, _key = await _sse()
    assert MOUNTS == ["sse"]
    assert session.view_instance.live_transport == "sse"


# ---------------------------------------------------------------------------
# (A) server_push: the issue's repro, on both transports
# ---------------------------------------------------------------------------


async def test_background_task_push_updates_the_page_over_sse():
    session, key = await _sse()
    view = session.view_instance
    response = await _sse_event(session, key, "follow", {"count": 3})
    assert response.status_code == 200
    frames = await _sse_frames(session, _shows(3))
    assert view.log[-1] == 3
    pushed = [f for f in frames if f.get("source") == "broadcast"]
    assert pushed and all(f.get("broadcast") is True for f in pushed), frames
    assert all(f["type"] in {"patch", "html_update"} for f in pushed)
    assert not [f for f in frames if f.get("type") == "error"], frames
    # The last frame carries the last state (earlier pushes may coalesce).
    assert "3" in _text(pushed[-1])
    assert view.n == 3


async def test_background_task_push_updates_the_page_over_a_websocket():
    socket, consumer = await _ws()
    try:
        await socket.send_json_to({"type": "event", "event": "follow", "params": {"count": 3}})
        view = consumer.view_instance
        frames = await receive_until(socket, _shows(3))
        pushed = [f for f in frames if f.get("source") == "broadcast"]
        assert pushed and all(f.get("broadcast") is True for f in pushed), frames
        assert "3" in _text(pushed[-1])
        assert view.n == 3
    finally:
        await socket.disconnect()


async def test_push_on_the_http_render_is_a_noop_not_an_exception():
    view = PushPage()
    assert view.live_transport is None
    assert await view.server_push("handle_set", {"n": 5}) is False
    assert not hasattr(view, "log") or view.log == []


async def test_push_issued_inside_an_event_defers_instead_of_deadlocking_over_sse():
    session, key = await _sse()
    view = session.view_instance
    response = await asyncio.wait_for(_sse_event(session, key, "set_then_push"), 10)
    assert response.status_code == 200
    # The event set 100; the push queued behind it and ran when the event's turn ended.
    frames = await _sse_frames(session, lambda fs: view.log == [200] and _broadcast(fs))
    assert view.pushed is True
    assert view.n == 200
    assert not [f for f in frames if f.get("type") == "error"], frames


async def test_push_issued_inside_an_event_defers_instead_of_deadlocking_over_a_websocket():
    socket, consumer = await _ws()
    try:
        await socket.send_json_to({"type": "event", "event": "set_then_push", "params": {}})
        view = consumer.view_instance
        await receive_until(socket, lambda fs: view.log == [200] and _broadcast(fs))
        assert view.pushed is True
    finally:
        await socket.disconnect()


# -- busy deferral: ordering by event order --------------------------------


async def test_sse_push_while_the_lock_is_held_waits_and_keeps_order():
    session, _key = await _sse()
    view, runtime = session.view_instance, session.runtime
    await session._render_lock.acquire()  # a slow event / background render holds the turn
    try:
        for n in (1, 2, 3):
            assert await view.server_push("handle_set", {"n": n}) is True
        assert len(runtime._deferred_pushes) == 3
        assert view.log == [], "nothing runs while the lock is held"
        assert session.queue.empty()
    finally:
        session._render_lock.release()
    # The lock is free but the drain has not run (no await since the release):
    # a push arriving now joins the queue behind the earlier ones, not ahead.
    assert await view.server_push("handle_set", {"n": 4}) is True
    assert len(runtime._deferred_pushes) == 4
    frames = await _sse_frames(session, lambda fs: view.log == [1, 2, 3, 4] and _broadcast(fs))
    assert view.log == [1, 2, 3, 4]
    broadcasts = [f for f in frames if f.get("source") == "broadcast"]
    assert len(broadcasts) == 1, "a backlog costs one render (#3001)"
    await wait_until(lambda: runtime._push_drain_task.done(), what="the drain to finish")


async def test_sse_identical_deferred_pushes_coalesce():
    session, _key = await _sse()
    view, runtime = session.view_instance, session.runtime
    await session._render_lock.acquire()
    try:
        for _ in range(5):
            await view.server_push("handle_set", {"n": 9})
        assert len(runtime._deferred_pushes) == 1
    finally:
        session._render_lock.release()
    await _sse_frames(session, lambda fs: view.log == [9] and _broadcast(fs))


async def test_sse_deferred_queue_is_bounded_and_drops_the_oldest():
    from djust.websocket import _MAX_DEFERRED_PUSHES

    session, _key = await _sse()
    view, runtime = session.view_instance, session.runtime
    await session._render_lock.acquire()
    try:
        for n in range(_MAX_DEFERRED_PUSHES + 5):
            await view.server_push("handle_set", {"n": n})
        assert len(runtime._deferred_pushes) == _MAX_DEFERRED_PUSHES
    finally:
        session._render_lock.release()
    last = _MAX_DEFERRED_PUSHES + 4
    await _sse_frames(session, lambda fs: view.log[-1:] == [last] and _broadcast(fs))
    assert view.log[0] == 5, "the five oldest were dropped"


# -- the chokepoints a normal turn goes through ----------------------------


async def test_sse_push_runs_inside_the_turn_chain(wrapper):
    session, _key = await _sse()
    view = session.view_instance
    assert await view.server_push("handle_set", {"n": 1}) is True
    await _sse_frames(session, lambda fs: _broadcast(fs))
    assert view.step_seen is not None, WRAPPED
    assert any(name == "PushPage" for name, _step, _kind in WRAPPED)


async def test_sse_push_is_refused_when_reauth_fails(monkeypatch):
    """The push turn runs the ``reauth`` built-in like any server turn."""
    from djust.runtime import SSESessionTransport

    session, _key = await _sse()
    view = session.view_instance
    view.login_required = True
    calls = []

    async def deny(self, v):
        calls.append(v)
        return False

    monkeypatch.setattr(SSESessionTransport, "recheck_event_auth", deny)
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        config.set("reauth_on_event", True)
        try:
            assert await view.server_push("handle_set", {"n": 1}) is True
            await wait_until(lambda: calls, what="the re-check")
        finally:
            config.set("reauth_on_event", False)
    assert calls == [view]
    assert view.log == [], "a refused turn applies nothing"


async def test_a_push_cannot_call_a_handler_the_websocket_would_refuse(caplog):
    session, _key = await _sse()
    view = session.view_instance
    for name in ("secret", "__init__", "mount", "render"):
        await view.server_push(name, {})
    await session._render_lock.acquire()  # let any (wrongly) running turn finish first
    session._render_lock.release()
    assert "SECRET" not in view.log
    assert "blocked handler" in caplog.text


async def test_groups_stay_websocket_only_over_sse():
    """Slice 1 does not wire ``push_to_view`` groups, push scopes or ``db_notify`` to SSE."""
    session, _key = await _sse()
    runtime = session.runtime
    assert not hasattr(runtime.transport, "join_view_group")
    assert getattr(session.view_instance, "_view_group", None) is None


# -- cleanup ----------------------------------------------------------------


async def test_sse_shutdown_drops_pending_pushes_and_a_late_push_is_dropped():
    session, _key = await _sse()
    view, runtime = session.view_instance, session.runtime
    await session._render_lock.acquire()
    try:
        await view.server_push("handle_set", {"n": 1})
        assert len(runtime._deferred_pushes) == 1
        session.shutdown()
        assert not runtime._deferred_pushes
    finally:
        session._render_lock.release()
    await wait_until(lambda: runtime._push_drain_task.done(), what="the drain to end")
    assert view.log == [], "a push queued before the close is never applied"
    # No exception, no task, no frame after the close.
    assert await view.server_push("handle_set", {"n": 2}) is False
    assert view.log == []
    assert runtime._push_drain_task.done()


async def test_a_push_from_a_view_a_navigation_replaced_is_dropped():
    session, key = await _sse()
    old_view = session.view_instance
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "live_redirect_mount", "url": "/push/", "params": {}},
        key,
    )
    assert (
        await DjustSSEMessageView().post(post, session_id=session.session_id)
    ).status_code == 200
    await wait_until(
        lambda: session.view_instance is not None and session.view_instance is not old_view,
        what="the replacement view",
    )
    new_view = session.view_instance
    # ``old_view._sse_session`` is the session, whose runtime now serves new_view.
    assert await old_view.server_push("handle_set", {"n": 1}) is False
    assert old_view.log == [] and new_view.log == []


async def test_websocket_push_after_disconnect_is_a_noop():
    socket, consumer = await _ws()
    view = consumer.view_instance
    await socket.disconnect()
    assert await view.server_push("handle_set", {"n": 1}) is False
    assert view.log == []


async def test_server_push_validates_its_arguments():
    view = PushPage()
    with pytest.raises(TypeError):
        await view.server_push("")
    with pytest.raises(TypeError):
        await view.server_push("handle_set", payload=[1])


# ---------------------------------------------------------------------------
# (C) tick_interval over SSE
# ---------------------------------------------------------------------------


async def test_tick_interval_ticks_the_page_over_sse(wrapper):
    session, _key = await _sse(TICK, "/tick/")
    view = session.view_instance
    frames = await _sse_frames(
        session, lambda fs: sum(1 for f in fs if f.get("source") == "tick") >= 2
    )
    ticks = [f for f in frames if f.get("source") == "tick"]
    assert all(f.get("event_name") == "tick" for f in ticks)
    assert view.ticks >= 2
    assert view.tick_step is not None, "handle_tick ran inside the sync-turn wrapper"
    task = session.runtime._tick_task
    assert task is not None and not task.done()
    session.shutdown()
    assert task.cancelling() > 0, (
        "closing the session cancels the tick itself, not at its next beat"
    )
    await wait_until(lambda: task.done(), what="the tick task to stop")
    assert session.runtime._tick_task is None


async def test_tick_once_skips_while_a_turn_holds_the_lock_and_runs_after():
    session, _key = await _sse(TICK, "/tick/")
    session.runtime._tick_task.cancel()  # drive the tick by hand: no timer in the assertion
    view, runtime = session.view_instance, session.runtime
    before = view.ticks
    await session._render_lock.acquire()
    try:
        assert await runtime._tick_once(view) is False
        assert view.ticks == before, "a tick yields to a turn in progress"
    finally:
        session._render_lock.release()
    assert await runtime._tick_once(view) is True
    assert view.ticks == before + 1


async def test_tick_that_changes_nothing_sends_no_frame():
    session, _key = await _sse(QUIET, "/quiet/")
    session.runtime._tick_task.cancel()
    view, runtime = session.view_instance, session.runtime
    assert await runtime._tick_once(view) is False
    assert session.queue.empty()


async def test_no_tick_task_without_a_handle_tick_override():
    session, _key = await _sse(NOTICK, "/notick/")
    assert session.runtime._tick_task is None


async def test_a_failed_mount_leaves_no_tick_task():
    from djust.runtime import SSESessionTransport

    session, _key = await _sse(TICK, "/tick/")
    runtime = session.runtime
    task = runtime._tick_task
    assert task is not None
    await SSESessionTransport(session).on_mount_failed(session.view_instance)
    assert task.cancelling() > 0
    await wait_until(lambda: task.done(), what="the tick task to stop")
    assert runtime._tick_task is None


async def test_a_replaced_page_stops_its_tick():
    session, key = await _sse(TICK, "/tick/")
    task = session.runtime._tick_task
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "live_redirect_mount", "url": "/push/", "params": {}},
        key,
    )
    assert (
        await DjustSSEMessageView().post(post, session_id=session.session_id)
    ).status_code == 200
    assert task.cancelling() > 0
    await wait_until(lambda: task.done(), what="the old page's tick to stop")
    assert session.runtime._tick_task is None, "the new page (PushPage) has no tick"


# ---------------------------------------------------------------------------
# More paths: explicit roots, a view beside the page view, a stale queue
# ---------------------------------------------------------------------------


async def test_push_to_an_explicit_root_is_authorized_committed_and_rendered():
    session, _key = await _sse(EXPLICIT, "/explicit/")
    view = session.view_instance
    assert await view.server_push("handle_bump", {"by": 5}) is True
    frames = await _sse_frames(session, _shows(5))
    assert view.count == 5
    assert not [f for f in frames if f.get("type") == "error"], frames


async def test_a_push_before_the_mount_frame_is_dropped_and_a_tick_waits():
    session, _key = await _sse(TICK, "/tick/")
    view, runtime = session.view_instance, session.runtime
    runtime.stop_server_work()
    runtime._mount_sent_for = None  # as inside mount(): the client has nothing to patch yet
    assert await runtime.server_push(view, {"handler": "handle_tick", "payload": {}}) is False
    assert not runtime._deferred_pushes
    assert session.queue.empty()


async def test_a_queued_push_is_dropped_when_another_view_took_the_session():
    session, _key = await _sse()
    view, runtime = session.view_instance, session.runtime
    await session._render_lock.acquire()
    try:
        await view.server_push("handle_set", {"n": 1})
        assert len(runtime._deferred_pushes) == 1
        other = PushPage()  # a different view now owns the runtime
        other.log = []
        runtime.view_instance = other
    finally:
        session._render_lock.release()
    await wait_until(lambda: runtime._push_drain_task.done(), what="the drain to finish")
    assert view.log == [] and other.log == [], "the push was addressed to the view it left"
    assert not runtime._deferred_pushes
    assert session.queue.empty()


async def test_a_view_mounted_beside_the_page_view_pushes_and_ticks_on_its_own_runtime():
    session, key = await _sse()
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "mount", "view": TICK, "target_id": "lazy-1", "url": "/tick/"},
        key,
    )
    assert (
        await DjustSSEMessageView().post(post, session_id=session.session_id)
    ).status_code == 200
    slot = session._slots["lazy-1"]
    slot_view = slot.view
    assert slot_view is not None and slot_view is not session.view_instance
    # ``view._sse_session.runtime`` is the slot's own runtime, not the page's.
    assert slot_view._sse_session.runtime is slot.runtime
    assert slot.runtime is not session.runtime
    frames = await _sse_frames(
        session,
        lambda fs: any(f.get("source") == "tick" and f.get("target_id") == "lazy-1" for f in fs),
    )
    assert slot_view.ticks >= 1
    # The page view's frames never carry the slot's address.
    assert all(f.get("target_id") in (None, "lazy-1") for f in frames)
    task = slot.runtime._tick_task
    session.shutdown()
    assert task.cancelling() > 0, "closing the session stops the slot's tick"


async def test_start_async_queued_by_a_push_handler_runs_after_the_push():
    session, _key = await _sse()
    view = session.view_instance
    assert await view.server_push("handle_kick") is True
    await wait_until(lambda: getattr(view, "kicked", False), what="the queued start_async work")


async def test_push_to_an_explicit_root_whose_authority_is_revoked_runs_nothing(monkeypatch):
    from djust.runtime import ViewRuntime

    session, _key = await _sse(EXPLICIT, "/explicit/")
    view = session.view_instance

    async def revoked(self, v):
        raise PermissionError("revoked")

    monkeypatch.setattr(ViewRuntime, "authorize_explicit_turn", revoked)
    assert await view.server_push("handle_bump", {"by": 5}) is True
    frames = await _sse_frames(session, lambda fs: any(f.get("type") == "error" for f in fs))
    assert any(f.get("code") == "permission_denied" for f in frames), frames
    assert view.count == 0, "a turn whose authority was revoked applies nothing"
    assert session.runtime.view_instance is None, "the revoked view is dropped"
