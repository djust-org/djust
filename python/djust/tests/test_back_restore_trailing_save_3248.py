"""#3248: Back restores the LATEST rendered state, not an older session copy.

A legacy ``enable_state_snapshot`` view persists its state to the Django
session after every event, bounded by ``EVENT_STATE_SAVE_TIMEOUT_S``. A save
that misses the bound is not waited for, and a save that cannot start because
the previous one is still running (an SSE or request-pool turn, where saves
run on a pool while the next event proceeds) writes nothing. A save that
FAILS writes nothing either. When the burst ends the session then holds an
older state than the last render, and Back restores the session copy in
preference to the client's token.

The fix is a coalesced trailing save. State a save did not store is recorded
(latest wins), and one task writes it once the pending save settles, strictly
after it. Teardown (disconnect, ``live_redirect``, an SSE close or navigation)
waits for it. It is dropped, never written, when the session was logged out or
its key changed since the state was rendered.

* Over WebSocket a save runs on the session's own sync thread, so a save that
  hangs also holds that session's renders; the reachable losses are a failed
  save and a teardown. Those tests drive the real ``LiveViewConsumer`` through
  a ``WebsocketCommunicator``.
* Over SSE a save runs on a pool, so a later event overtakes a slow one: the
  burst, ordering and logout tests drive the real SSE views.

The restored state is HISTORICAL view state: a handler's permission check still
runs against the current request.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust import runtime as runtime_module
from djust.decorators import event_handler, permission_required
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.tests.test_exposure_sse_resilience_3200 import _fresh_key, _request

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

PATH = "/trailing/"
KEY = "liveview_" + PATH
MOD = __name__

VIEWS: list = []


class Counter(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = '<div dj-view="' + MOD + '.Counter" dj-id="0">Counter: {{ count }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0
        VIEWS.append(self)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def log_out(self, **kwargs):
        self.count += 1
        self._djust_mount_request.session.flush()  # an in-handler logout()

    @event_handler()
    @permission_required("trailing.delete_everything")
    def wipe(self, **kwargs):
        self.count = -1


class Other(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-view="' + MOD + '.Other" dj-id="0">Other page</div>'

    def mount(self, request, **kwargs):
        pass


urlpatterns = [path("trailing/", Counter.as_view()), path("other/", Other.as_view())]


class Store:
    """Wraps ``SessionStore.save`` for saves of the view's key.

    ``written`` holds the counter at each save the store accepted, in order.
    ``block_first`` makes the first such save wait for ``release`` (a hung
    write); ``fail`` raises for every save while it is truthy.
    """

    def __init__(self, monkeypatch, *, block_first=False, fail=False, engine=None):
        self.release = threading.Event()
        if not block_first:
            self.release.set()
        self.written: list = []
        self.attempts = 0
        self.fail = fail
        self.fail_next = 0  # fail only the next N saves
        self._lock = threading.Lock()
        engine = engine or SessionStore
        original = engine.save
        store = self

        def save(this, *args, **kwargs):
            if (args and args[0]) or kwargs.get("must_create"):
                return original(this, *args, **kwargs)  # create(): cycle_key, login
            cache = getattr(this, "_session_cache", None) or {}
            state = cache.get(KEY)
            if state is None:
                return original(this, *args, **kwargs)
            with store._lock:
                store.attempts += 1
                first = store.attempts == 1
            if first:
                store.release.wait(timeout=15)
            with store._lock:
                failing = store.fail or store.fail_next > 0
                store.fail_next = max(0, store.fail_next - 1)
            if failing:
                raise RuntimeError("storage unavailable")
            with store._lock:
                store.written.append(state["count"])
            return original(this, *args, **kwargs)

        monkeypatch.setattr(engine, "save", save)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(runtime_module, "_TRAILING_RETRY_DELAY_S", 0.05)
    VIEWS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], ROOT_URLCONF=MOD, DEBUG=False):
        yield
    VIEWS.clear()
    _sse_sessions.clear()


async def _stored(key):
    def load():
        return SessionStore(key).load().get(KEY)

    return await sync_to_async(load)()


async def _until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


class Socket:
    def __init__(self, session_key):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        class ScopeSession:
            def __init__(self, key):
                self.session_key = key

        self.key = session_key
        self.ws = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        self.ws.scope["session"] = ScopeSession(session_key)

    async def open(self, view="Counter", url=PATH):
        ok, _ = await self.ws.connect()
        assert ok
        assert (await self.ws.receive_json_from(timeout=3))["type"] == "connect"
        return await self.mount(view, url)

    async def mount(self, view="Counter", url=PATH, kind="mount"):
        await self.ws.send_json_to({"type": kind, "view": f"{MOD}.{view}", "url": url})
        for _ in range(10):
            frame = await self.ws.receive_json_from(timeout=5)
            if frame.get("type") == "mount":
                return frame
        raise AssertionError("no mount frame")

    async def event(self, name="increment", ref=1):
        await self.ws.send_json_to({"type": "event", "event": name, "params": {}, "ref": ref})
        frames = [await self.ws.receive_json_from(timeout=5)]
        while not await self.ws.receive_nothing(timeout=0.05):
            frames.append(await self.ws.receive_json_from(timeout=5))
        return frames

    async def close(self):
        await self.ws.disconnect()


async def test_a_failed_save_is_retried_and_the_latest_state_lands(monkeypatch, caplog):
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch)
    sock = Socket(key)
    await sock.open()
    try:
        with caplog.at_level(logging.WARNING, logger="djust"):
            await sock.event(ref=1)
            store.fail_next = 1
            await sock.event(ref=2)  # the LAST event's save raises; no further event arrives
            assert await _until(lambda: store.written and store.written[-1] == 2), (
                store.written,
                store.attempts,
            )
    finally:
        await sock.close()
    assert (await _stored(key))["count"] == 2


async def test_back_after_a_disconnect_restores_the_latest_state(monkeypatch, caplog):
    """Storage keeps failing while the user clicks; the disconnect still stores the
    latest state once it answers, so the reconnect (Back, bfcache, a dropped
    socket) mounts the page the user left."""
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, fail=True)
    sock = Socket(key)
    with caplog.at_level(logging.WARNING, logger="djust"):
        await sock.open()
        for i in range(4):
            await sock.event(ref=i)
        assert await _until(
            lambda: any("could not be saved" in r.getMessage() for r in caplog.records)
        ), [r.getMessage() for r in caplog.records]
        assert store.written == []  # nothing stored yet: the pending state is only kept
        store.fail = False
        await sock.close()  # the disconnect flush
    assert store.written == [4], store.written  # ONE write, the latest

    again = Socket(key)
    frame = await again.open()
    try:
        assert "Counter: 4" in frame["html"], frame["html"]
    finally:
        await again.close()


async def test_back_after_navigation_restores_the_latest_state(monkeypatch):
    """``live_redirect`` away, then Back: the redirect stores the pending state
    BEFORE the new view mounts."""
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, fail=True)
    sock = Socket(key)
    await sock.open()
    try:
        for i in range(3):
            await sock.event(ref=i)
        store.fail = False
        await sock.mount("Other", "/other/", kind="live_redirect_mount")
        assert (await _stored(key))["count"] == 3
        frame = await sock.mount("Counter", PATH, kind="live_redirect_mount")
        assert "Counter: 3" in frame["html"], frame["html"]
    finally:
        await sock.close()


async def test_a_session_logged_out_elsewhere_gets_no_trailing_write_over_websocket(monkeypatch):
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, fail=True)
    sock = Socket(key)
    await sock.open()
    try:
        for i in range(2):
            await sock.event(ref=i)
        await sync_to_async(SessionStore(key).delete)()  # logout, in another request
        attempts = store.attempts
        store.fail = False
        await asyncio.sleep(0.4)
    finally:
        await sock.close()
    assert store.attempts == attempts, "a save was attempted into a logged-out session"
    assert store.written == []
    assert not await sync_to_async(SessionStore(key).exists)(key)


async def test_a_rotated_session_key_discards_the_trailing_save(monkeypatch):
    """The session object's key changed (login/cycle_key, or an in-object logout)
    after the state was rendered: nothing may be written into the new session."""
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, fail=True)
    sock = Socket(key)
    await sock.open()
    try:
        for i in range(2):
            await sock.event(ref=i)
        session = VIEWS[0]._djust_mount_request.session
        await sync_to_async(session.cycle_key)()
        new_key = session.session_key
        assert new_key != key
        attempts = store.attempts
        store.fail = False
        await asyncio.sleep(0.4)
        assert store.attempts == attempts, "a trailing save wrote into the rotated session"
    finally:
        await sock.close()
    assert store.written == []


async def test_restored_state_does_not_bypass_handler_permissions(monkeypatch):
    """The restored state is historical view state, not authority: a handler
    that needs a permission still checks the CURRENT request."""
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch)
    first = Socket(key)
    await first.open()
    await first.event(ref=1)
    await first.event(ref=2)
    await first.close()
    assert await _until(lambda: store.written and store.written[-1] == 2)

    again = Socket(key)
    frame = await again.open()
    try:
        assert "Counter: 2" in frame["html"]  # restored
        await again.ws.send_json_to({"type": "event", "event": "wipe", "params": {}, "ref": 9})
        reply = await again.ws.receive_json_from(timeout=5)
        assert reply["type"] == "error", reply
        assert VIEWS[-1].count == 2  # the handler did not run
    finally:
        await again.close()


# --------------------------------------------------------------------------- #
# SSE (saves run on a pool, so later events overtake a slow one)
# --------------------------------------------------------------------------- #


async def _sse_start(key):
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": f"{MOD}.Counter", "_djust_url": PATH}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]
    while not session.queue.empty():
        session.queue.get_nowait()
    return session


async def _sse_event(session, key, body=None):
    request = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        body or {"type": "event", "event": "increment", "params": {}},
        key,
    )
    async with ThreadSensitiveContext():  # what Django's ASGIHandler wraps
        response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200


async def _settled(session):
    """Wait for the trailing task and the last save; a dropped save raises, ignored."""
    for waited in (session.runtime._trailing_save, session.runtime._explicit_save_pending):
        if waited is not None:
            await asyncio.wait({waited}, timeout=10)


async def test_a_burst_behind_a_slow_save_ends_in_one_trailing_save_of_the_latest(monkeypatch):
    key = await sync_to_async(_fresh_key)()
    session = await _sse_start(key)
    store = Store(monkeypatch, block_first=True)
    try:
        for _ in range(5):
            await _sse_event(session, key)
        assert store.written == []  # the first save is still inside storage
        store.release.set()
        assert await _until(lambda: len(store.written) >= 2), store.written
        await _settled(session)
        await asyncio.sleep(0.3)  # a second trailing write would show up here
    finally:
        store.release.set()
        await _settled(session)
    # The save that was already running wrote what it read when it started;
    # ONE trailing write brought storage to the latest state.
    assert store.written[-1] == 5
    assert len(store.written) == 2, store.written
    assert (await _stored(key))["count"] == 5


async def test_a_newer_state_is_never_overwritten_by_an_older_write(monkeypatch):
    """The slow write completes AFTER newer events were queued: the store only
    ever sees the counter go up, and ends on the latest."""
    key = await sync_to_async(_fresh_key)()
    session = await _sse_start(key)
    store = Store(monkeypatch, block_first=True)
    try:
        for _ in range(3):
            await _sse_event(session, key)
        store.release.set()
        assert await _until(lambda: store.written and store.written[-1] == 3), store.written
        await _settled(session)
    finally:
        store.release.set()
        await _settled(session)
    assert store.written == sorted(store.written), store.written
    assert (await _stored(key))["count"] == 3


async def test_an_sse_close_stores_the_latest_state_first(monkeypatch):
    """The stream closes (the user navigated away) while the first save is
    still inside storage: the close waits for the trailing save."""
    from djust.sse import _shutdown_closed_session

    key = await sync_to_async(_fresh_key)()
    session = await _sse_start(key)
    store = Store(monkeypatch, block_first=True)
    try:
        for _ in range(3):
            await _sse_event(session, key)
        threading.Timer(0.3, store.release.set).start()
        await _shutdown_closed_session(session)
    finally:
        store.release.set()
    assert store.written[-1] == 3, store.written
    assert (await _stored(key))["count"] == 3


async def test_a_session_logged_out_elsewhere_gets_no_trailing_write_over_sse(monkeypatch):
    key = await sync_to_async(_fresh_key)()
    session = await _sse_start(key)
    store = Store(monkeypatch, block_first=True)
    try:
        for _ in range(3):
            await _sse_event(session, key)
        await sync_to_async(SessionStore(key).delete)()  # logout, in another request
        store.release.set()
        await _settled(session)
        await asyncio.sleep(0.3)
    finally:
        store.release.set()
    # Only the save already inside storage attempted a write; the trailing save
    # found its session gone.
    assert store.attempts == 1, store.attempts
    assert not await sync_to_async(SessionStore(key).exists)(key)


# --------------------------------------------------------------------------- #
# Hung storage must not hold a navigation (#3212), review I1
# --------------------------------------------------------------------------- #


@pytest.fixture
def stuck_soon(monkeypatch):
    monkeypatch.setattr(runtime_module, "_TRAILING_STUCK_AFTER_S", 0.2)


async def _hang(monkeypatch, session, key):
    """Hung storage: the first save never answers, so every later event's save
    never starts and its state is recorded as unsaved."""
    store = Store(monkeypatch, block_first=True)
    for _ in range(3):
        await _sse_event(session, key)
    await asyncio.sleep(0.4)  # the first save has now been running past the threshold
    assert session.runtime._unsaved_state, "vacuous: nothing was left unsaved"
    return store


async def test_hung_storage_does_not_hold_an_sse_close(monkeypatch, stuck_soon):
    from djust.sse import _shutdown_closed_session

    key = await sync_to_async(_fresh_key)()
    session = await _sse_start(key)
    store = await _hang(monkeypatch, session, key)
    try:
        started = time.monotonic()
        await _shutdown_closed_session(session)
        assert time.monotonic() - started < 1.5
    finally:
        store.release.set()


async def test_hung_storage_does_not_hold_an_sse_navigation(monkeypatch, stuck_soon):
    key = await sync_to_async(_fresh_key)()
    session = await _sse_start(key)
    store = await _hang(monkeypatch, session, key)
    try:
        started = time.monotonic()
        await _sse_event(session, key, {"type": "live_redirect_mount", "url": "/other/"})
        assert time.monotonic() - started < 1.5
    finally:
        store.release.set()


async def test_an_sse_navigation_stores_the_latest_state_before_the_new_page(monkeypatch):
    """``_replace_view`` waits for the trailing save, as the WebSocket redirect does."""
    key = await sync_to_async(_fresh_key)()
    session = await _sse_start(key)
    store = Store(monkeypatch, fail=True)
    for _ in range(3):
        await _sse_event(session, key)
    # The trailing save has given up: only the teardown's last attempt is left.
    assert await _until(lambda: session.runtime._trailing_save.done())
    assert store.written == []
    store.fail = False
    await _sse_event(session, key, {"type": "live_redirect_mount", "url": "/other/"})
    assert store.written == [3], store.written
    assert (await _stored(key))["count"] == 3


# --------------------------------------------------------------------------- #
# Logout, review M2/M3; teardown flag, M4; other session backends, M5
# --------------------------------------------------------------------------- #


async def test_an_in_handler_logout_gets_no_trailing_write(monkeypatch):
    """``session.flush()`` in a handler (``logout()``) leaves the object with no
    key. The trailing save must not create a new cookie-less session row."""
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, fail=True)
    sock = Socket(key)
    await sock.open()
    try:
        await sock.event("log_out", ref=1)  # the flush, then its own save raises
        assert store.attempts == 1
        store.fail = False
        await asyncio.sleep(0.4)
    finally:
        await sock.close()
    assert store.attempts == 1, "a trailing save wrote after the session was flushed"
    assert store.written == []


def _bare_runtime(view, session=None):
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    runtime = ViewRuntime(MockTransport())
    runtime.view_instance = view
    view._djust_mount_request = type("R", (), {"session": session, "path": PATH})()
    return runtime


class _FakeSession:
    def __init__(self, key):
        self.session_key = key


def test_the_save_guard_follows_the_session_key():
    view = Counter()
    # A persisted session: guarded by its key.
    assert _bare_runtime(view, _FakeSession("k1"))._legacy_save_guard(view, sticky=False) == ("k1",)
    # No key now, one at mount: flushed by an in-handler logout.
    view._django_session_key = "k1"
    runtime = _bare_runtime(view, _FakeSession(None))
    assert runtime._legacy_save_guard(view, sticky=False) == (runtime_module._SESSION_FLUSHED,)
    # Never persisted (anonymous): an earlier queued save may create the key.
    view._django_session_key = None
    assert runtime._legacy_save_guard(view, sticky=False) is None


class Unhashable(Counter):
    """A view that defines ``__eq__`` is unhashable (a dataclass, for one)."""

    def __eq__(self, other):
        return self is other

    __hash__ = None


async def test_no_save_is_recorded_for_a_released_view_even_if_unhashable():
    view = Unhashable()
    runtime = _bare_runtime(view, _FakeSession("k1"))
    runtime._settle_legacy_save("root", view, "e", runtime_module._UNSAVED, sticky=False)
    assert "root" in runtime._unsaved_state  # recorded before teardown
    runtime._trailing_save.cancel()

    await runtime.finish_state_saves()  # must not raise on an unhashable view
    assert runtime._unsaved_state == {}

    runtime._settle_legacy_save("root", view, "e", runtime_module._UNSAVED, sticky=False)
    assert runtime._unsaved_state == {}, "a save was recorded after teardown"
    assert runtime._trailing_save is None


@pytest.mark.parametrize(
    "engine",
    ["django.contrib.sessions.backends.cached_db", "django.contrib.sessions.backends.cache"],
)
async def test_logout_elsewhere_gets_no_trailing_write_on_other_session_backends(
    monkeypatch, engine
):
    from importlib import import_module

    store_cls = import_module(engine).SessionStore
    with override_settings(SESSION_ENGINE=engine):

        def create():
            session = store_cls()
            session.create()
            return session.session_key

        key = await sync_to_async(create)()
        store = Store(monkeypatch, fail=True, engine=store_cls)
        sock = Socket(key)
        await sock.open()
        try:
            for i in range(2):
                await sock.event(ref=i)
            await sync_to_async(store_cls(key).delete)()  # logout, in another request
            attempts = store.attempts
            assert attempts >= 1, "vacuous: no save reached the store"
            store.fail = False
            await asyncio.sleep(0.4)
        finally:
            await sock.close()
        assert store.attempts == attempts, "a save was attempted into a logged-out session"
        assert store.written == []
        assert not await sync_to_async(store_cls(key).exists)(key)


# --------------------------------------------------------------------------- #
# Sticky children (the parallel path)
# --------------------------------------------------------------------------- #


class StickyChild(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    sticky = True
    sticky_id = "side"
    template = "<div>{{ clicks }}</div>"

    def get_context_data(self, **kwargs):
        return {"clicks": self.clicks}


async def test_a_sticky_childs_failed_save_gets_a_trailing_save(monkeypatch):
    from djust.mixins.sticky import sticky_child_session_key

    key = await sync_to_async(_fresh_key)()
    request = await sync_to_async(_request)("GET", PATH, {}, key)
    request.path = PATH
    parent = Counter()
    parent.count = 0
    parent._djust_mount_request = request
    runtime = _bare_runtime(parent, request.session)
    child = StickyChild()
    child.clicks = 3
    parent._register_child("side", child)

    original = SessionStore.save
    calls = []

    def flaky(this, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("storage unavailable")
        return original(this, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", flaky)
    await runtime._persist_sticky_child_after_event(child, "click")
    assert runtime._unsaved_state, "the failed sticky save was not recorded"
    await asyncio.wait_for(runtime._trailing_save, 10)

    def load():
        return SessionStore(key).load().get(sticky_child_session_key(PATH, "side"))

    assert await sync_to_async(load)() == {"clicks": 3}
    assert runtime._unsaved_state == {}
