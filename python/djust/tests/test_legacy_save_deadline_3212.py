"""#3212 item 3: a legacy best-effort save's 150 ms counts storage, not queueing.

``_persist_state_after_event`` and ``_persist_sticky_child_after_event`` wrapped
several ``sync_to_async`` hops in one 150 ms ``asyncio.wait_for``. On the
default single sync thread those hops queue behind other sessions' handlers
and renders, so a busy process dropped saves storage would have finished in
milliseconds (a warning, nothing stored). #3200 fixed the explicit saves; the
legacy ones now use the same shape: one Django-thread hop, with the deadline
starting when that hop starts running.

Legacy semantics are otherwise unchanged: a save that really outruns the
deadline, or fails, is logged and the event goes on. Such a save may still
land later, so saves of one runtime are ordered and a late one can never
overwrite a newer one.

The load is deterministic, as in ``test_explicit_save_deadline_3200``: worker
coroutines keep the one sync thread busy with fixed-length jobs. This module
deliberately does not start with ``test_exposure_``, so the production bound
applies.
"""

import asyncio
import logging
import threading
import time

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, event_handler
from djust import runtime as runtime_module
from djust.mixins.sticky import sticky_child_session_key
from djust.runtime import ViewRuntime
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

PATH = "/legacy-3212/"


class Counter(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class StickyChild(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    sticky = True
    sticky_id = "side"
    template = "<div>{{ clicks }}</div>"

    def get_context_data(self, **kwargs):
        return {"clicks": self.clicks}


def _request():
    request = RequestFactory().get(PATH)
    request.session = SessionStore()
    request.session.create()
    return request


async def _setup(count=5):
    request = await sync_to_async(_request)()
    view = Counter()
    view.count = count
    view._djust_mount_request = request
    runtime = ViewRuntime(MockTransport())
    runtime.view_instance = view
    return runtime, view, request.session.session_key


async def _stored(key, name):
    def load():
        return SessionStore(key).load().get(name)

    return await sync_to_async(load)()


class _Busy:
    """Four workers each holding the sync thread for 100 ms at a time, so every
    ``sync_to_async`` hop waits ~300 ms in the queue (the #3200 load)."""

    async def __aenter__(self):
        self.stop = False

        async def other_session():
            while not self.stop:
                await sync_to_async(time.sleep)(0.1)

        self.workers = [asyncio.ensure_future(other_session()) for _ in range(4)]
        await asyncio.sleep(0.05)  # let the queue fill
        return self

    async def __aexit__(self, *exc):
        self.stop = True
        await asyncio.gather(*self.workers)


def _timeouts(caplog):
    return [r for r in caplog.records if "exceeded 150ms" in r.getMessage()]


async def test_a_busy_sync_thread_does_not_drop_the_root_save(caplog):
    assert runtime_module.EVENT_STATE_SAVE_TIMEOUT_S == 0.150
    runtime, view, key = await _setup()
    with caplog.at_level(logging.WARNING, logger="djust.runtime"):
        async with _Busy():
            await runtime._persist_state_after_event(view, "increment")
    assert not _timeouts(caplog), [r.getMessage() for r in caplog.records]
    assert await _stored(key, "liveview_" + PATH) == {"count": 5}


async def test_a_busy_sync_thread_does_not_drop_the_sticky_child_save(caplog):
    runtime, parent, key = await _setup()
    child = StickyChild()
    child.clicks = 3
    parent._register_child("side", child)
    with caplog.at_level(logging.WARNING, logger="djust.runtime"):
        async with _Busy():
            await runtime._persist_sticky_child_after_event(child, "click")
    assert not _timeouts(caplog), [r.getMessage() for r in caplog.records]
    assert await _stored(key, sticky_child_session_key(PATH, "side")) == {"clicks": 3}


async def test_a_save_that_really_outruns_the_bound_only_warns(caplog, monkeypatch):
    """Storage itself is slow: the save is not waited for, the event goes on
    with a warning (never an exception), and the save still lands."""
    runtime, view, key = await _setup()
    release = threading.Event()
    original = SessionStore.save

    def slow_save(self, *args, **kwargs):
        release.wait(timeout=10)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", slow_save)
    try:
        with caplog.at_level(logging.WARNING, logger="djust.runtime"):
            await runtime._persist_state_after_event(view, "increment")
        # Returned while the store was still blocked: the bound held.
        assert not release.is_set()
        assert len(_timeouts(caplog)) == 1
    finally:
        release.set()
    await asyncio.wait_for(runtime._explicit_save_pending, 10)
    assert await _stored(key, "liveview_" + PATH) == {"count": 5}


async def test_a_late_save_never_overwrites_a_newer_one(monkeypatch):
    """Turn N's save outruns its deadline and keeps running. Turn N+1 runs in
    another thread-sensitive context (as SSE requests do). N+1 waits for N, so
    the order the store sees is N then N+1, never the reverse.

    Deterministic (the #3229 review's version): up to the save spawn, N+1
    makes no thread hop, so a bounded number of loop yields reaches a definite
    state, ordered (N+1 parked on N's save, which is still the pending one) or
    unordered (N+1's save already spawned). Only the FIRST store call blocks,
    so an unordered N+1 always lands first."""
    runtime, view, key = await _setup(count=1)
    release = threading.Event()
    written = []
    calls = []
    original = SessionStore.save

    def recording_save(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            release.wait(timeout=10)
        written.append(dict(self._session))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", recording_save)
    monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 0.1)
    try:
        await runtime._persist_state_after_event(view, "first")  # times out, still running
        first = runtime._explicit_save_pending
        view.count = 2
        monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 5.0)

        async def second_turn():
            async with ThreadSensitiveContext():
                await runtime._persist_state_after_event(view, "second")

        second = asyncio.ensure_future(second_turn())
        for _ in range(50):
            await asyncio.sleep(0)
        if runtime._explicit_save_pending is not first:
            await second  # unordered: its save does not block, so it lands first
    finally:
        release.set()
    await second
    assert [w["liveview_" + PATH]["count"] for w in written] == [1, 2]
    assert await _stored(key, "liveview_" + PATH) == {"count": 2}
