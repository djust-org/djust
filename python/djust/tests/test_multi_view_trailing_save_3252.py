"""#3252 x #3248: a view mounted beside the page view saves through the same
trailing-save machinery as the page view.

Each view has its own runtime, and with it its own record of unsaved state, its
own trailing save and its own session-key guard. These tests drive the real
``LiveViewConsumer`` through a ``WebsocketCommunicator`` with a page view and a
``dj-lazy`` view that both opt in to state snapshots, and make the storage fail:

* a slot's failed save is retried and the latest state lands;
* a disconnect, a ``live_redirect`` and a page mount store a slot's pending
  state before the slot is released;
* a session logged out in another request, or whose key was rotated, gets no
  write from a slot's trailing save.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust import runtime as runtime_module
from djust.decorators import event_handler
from djust.tests.test_exposure_sse_resilience_3200 import _fresh_key

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
PATH = "/multi/"
PAGE_KEY = "liveview_" + PATH
SLOT_KEY = "liveview_slot:lazy:" + PATH
VIEWS: list = []


class _Counter(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True

    def mount(self, request, **kwargs):
        self.count = 0
        VIEWS.append(self)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class Page(_Counter):
    template = '<div dj-view="' + MOD + '.Page" dj-id="0">Page: {{ count }}</div>'


class Widget(_Counter):
    template = '<div dj-view="' + MOD + '.Widget" dj-id="0">Widget: {{ count }}</div>'


class Other(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-view="' + MOD + '.Other" dj-id="0">Other</div>'


class Store:
    """Wraps ``SessionStore.save`` for saves that carry ``watch``'s state."""

    def __init__(self, monkeypatch, watch, *, fail=False):
        self.watch = watch
        self.written: list = []
        self.attempts = 0
        self.fail = fail
        self.fail_next = 0
        self._lock = threading.Lock()
        original = SessionStore.save
        store = self

        def save(this, *args, **kwargs):
            if (args and args[0]) or kwargs.get("must_create"):
                return original(this, *args, **kwargs)
            state = (getattr(this, "_session_cache", None) or {}).get(store.watch)
            if state is None:
                return original(this, *args, **kwargs)
            with store._lock:
                store.attempts += 1
                failing = store.fail or store.fail_next > 0
                store.fail_next = max(0, store.fail_next - 1)
            if failing:
                raise RuntimeError("storage unavailable")
            with store._lock:
                store.written.append(state["count"])
            return original(this, *args, **kwargs)

        monkeypatch.setattr(SessionStore, "save", save)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(runtime_module, "_TRAILING_RETRY_DELAY_S", 0.05)
    VIEWS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield
    VIEWS.clear()


async def _stored(key, state_key):
    def load():
        return SessionStore(key).load().get(state_key)

    return await sync_to_async(load)()


async def _until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


class Socket:
    def __init__(self, session_key):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        class ScopeSession:
            def __init__(self, key):
                self.session_key = key

        self.ws = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        self.ws.scope["session"] = ScopeSession(session_key)

    async def open(self):
        ok, _ = await self.ws.connect()
        assert ok
        assert (await self.ws.receive_json_from(timeout=3))["type"] == "connect"
        await self.mount("Page")
        return await self.mount("Widget", target_id="lazy")

    async def mount(self, view, url=PATH, kind="mount", target_id=None):
        frame = {"type": kind, "view": f"{MOD}.{view}", "url": url}
        if target_id:
            frame["target_id"] = target_id
        await self.ws.send_json_to(frame)
        for _ in range(10):
            reply = await self.ws.receive_json_from(timeout=5)
            if reply.get("type") == "mount":
                return reply
        raise AssertionError("no mount frame")

    async def event(self, target_id=None, ref=1):
        frame = {"type": "event", "event": "increment", "params": {}, "ref": ref}
        if target_id:
            frame["target_id"] = target_id
        await self.ws.send_json_to(frame)
        out = [await self.ws.receive_json_from(timeout=5)]
        while not await self.ws.receive_nothing(timeout=0.05):
            out.append(await self.ws.receive_json_from(timeout=5))
        return out

    async def close(self):
        await self.ws.disconnect()


def _widget():
    return next(v for v in VIEWS if type(v) is Widget)


async def test_a_slots_failed_save_is_retried_and_the_latest_state_lands(monkeypatch):
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, SLOT_KEY)
    sock = Socket(key)
    await sock.open()
    try:
        await sock.event("lazy", ref=1)
        store.fail_next = 1
        await sock.event("lazy", ref=2)  # the LAST event's save raises
        assert await _until(lambda: store.written and store.written[-1] == 2), (
            store.written,
            store.attempts,
        )
    finally:
        await sock.close()
    assert (await _stored(key, SLOT_KEY))["count"] == 2


async def test_each_view_keeps_its_own_trailing_save():
    """The page view's unsaved state is not the slot's: they are recorded, and
    written, by their own runtimes."""
    key = await sync_to_async(_fresh_key)()
    sock = Socket(key)
    await sock.open()
    try:
        page_rt = VIEWS[0]._ws_consumer._get_runtime()
        slot_rt = VIEWS[1]._ws_consumer._get_runtime()
        assert page_rt is not slot_rt
        assert page_rt._unsaved_state is not slot_rt._unsaved_state
    finally:
        await sock.close()


async def test_a_disconnect_stores_every_views_latest_state_first(monkeypatch):
    """Storage keeps failing while the user clicks both views; the disconnect
    stores the latest state of each, once it answers."""
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, SLOT_KEY, fail=True)
    sock = Socket(key)
    await sock.open()
    for i in range(3):
        await sock.event("lazy", ref=i)
    await sock.event(None, ref=9)
    assert await _until(lambda: _widget()._ws_consumer._get_runtime()._unsaved_state)
    assert store.written == []
    store.fail = False
    await sock.close()
    assert store.written == [3], store.written
    assert (await _stored(key, SLOT_KEY))["count"] == 3
    # The slot's last write did not take the page's saved state with it.
    page_state = await _stored(key, PAGE_KEY)
    assert page_state is not None and page_state["count"] == 1


@pytest.mark.parametrize("how", ["live_redirect_mount", "mount"])
async def test_a_navigation_stores_a_slots_pending_state_before_it_goes(monkeypatch, how):
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, SLOT_KEY, fail=True)
    sock = Socket(key)
    await sock.open()
    try:
        for i in range(3):
            await sock.event("lazy", ref=i)
        store.fail = False
        await sock.mount("Other", "/other/", kind=how)
        assert (await _stored(key, SLOT_KEY))["count"] == 3
    finally:
        await sock.close()


async def test_a_session_logged_out_elsewhere_gets_no_write_from_a_slots_trailing_save(monkeypatch):
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, SLOT_KEY, fail=True)
    sock = Socket(key)
    await sock.open()
    try:
        for i in range(2):
            await sock.event("lazy", ref=i)
        await sync_to_async(SessionStore(key).delete)()  # logout, in another request
        attempts = store.attempts
        store.fail = False
        await asyncio.sleep(0.4)
    finally:
        await sock.close()
    assert store.attempts == attempts, "a save was attempted into a logged-out session"
    assert store.written == []
    assert not await sync_to_async(SessionStore(key).exists)(key)


async def test_a_rotated_session_key_discards_a_slots_trailing_save(monkeypatch):
    key = await sync_to_async(_fresh_key)()
    store = Store(monkeypatch, SLOT_KEY, fail=True)
    sock = Socket(key)
    await sock.open()
    try:
        for i in range(2):
            await sock.event("lazy", ref=i)
        session = _widget()._djust_mount_request.session
        await sync_to_async(session.cycle_key)()
        assert session.session_key != key
        attempts = store.attempts
        store.fail = False
        await asyncio.sleep(0.4)
        assert store.attempts == attempts, "a trailing save wrote into the rotated session"
    finally:
        await sock.close()
    assert store.written == []
