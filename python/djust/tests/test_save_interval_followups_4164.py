"""The work left before ``DJUST_STATE_SAVE_INTERVAL`` became the default (#4164).

* item 2: an HTTP fallback request that changes nothing does not take the tab
  from a socket that holds newer, unsaved changes; one that changes the state
  still does;
* item 3: the session write of an over-limit turn that asked for its save is
  ordered with the runtime's other saves and bounded by the save deadline;
* #4160: a handler that sends the page elsewhere with its own push event is
  saved before its reply when it says so (``save=True`` / ``save_state()``).

Timers are fired by hand, never waited for.
"""

from __future__ import annotations

from typing import Any, List

import pytest
from asgiref.sync import sync_to_async
from django.urls import path as _path

from djust import LiveView, runtime
from djust.decorators import event_handler
from djust.tests import _per_turn_harness as harness
from djust.tests import _save_frequency as sf

pytestmark = pytest.mark.django_db(transaction=True)

MODULE = __name__


class Counter(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def nothing(self, **kwargs):
        pass

    @event_handler(save=True)
    def checkout(self, **kwargs):
        """Sends the page to another URL with its own push event (#4160)."""
        self.count += 100
        self.push_event("go", {"url": "/thanks/"})

    @event_handler()
    def leave(self, **kwargs):
        self.count += 10
        self.save_state()
        self.push_event("go", {"url": "/thanks/"})

    @event_handler()
    def leave_unsaved(self, **kwargs):
        self.count += 10
        self.push_event("go", {"url": "/thanks/"})

    @event_handler()
    def grow(self, **kwargs):
        self.count += 1
        self.blob = "x" * 4000

    @event_handler(save=True)
    def keep_and_note(self, **kwargs):
        self.count += 1
        self.request.session["order"] = "placed"

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class Small(Counter):
    automatic_state_max_kb = 2


urlpatterns = [_path(sf.PATH.strip("/") + "/", Counter.as_view())]


@pytest.fixture(autouse=True)
def _each(monkeypatch):
    sf.one_tab(monkeypatch)


@pytest.fixture
def runtimes(monkeypatch):
    built: List[Any] = []
    real = runtime.ViewRuntime.__init__

    def recording(self, *args, **kwargs):
        real(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(runtime.ViewRuntime, "__init__", recording)
    return built


def _settings(**extra):
    return harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, **extra)


def _count(key: str) -> Any:
    return sf.only_envelope(key)["values"].get("count")


async def _stored(key: str) -> Any:
    return await sync_to_async(_count)(key)


def _fallback_post(key: str, event: str) -> Any:
    """A fallback POST through Django's ``SessionMiddleware``, which saves the
    session whenever the request modified it (as a deployed stack does)."""
    from django.contrib.sessions.middleware import SessionMiddleware

    view = Counter()
    request = sf.fallback_request(key, "POST", event)
    response = view.post(request)
    assert response.status_code == 200, response.content
    SessionMiddleware(lambda r: response).process_response(request, response)
    return view


async def _dirty(runtimes) -> tuple:
    key = await sync_to_async(sf.page_get)(Counter)
    comm, _ = await harness.mount_view(Counter, key)
    await harness.event(comm, "inc")
    rt = runtimes[-1]
    assert rt.state_unsaved
    return key, comm, rt


# ---------------------------------------------------------------------------
# item 2: a fallback request that saves nothing does not take the tab
# ---------------------------------------------------------------------------


async def test_a_fallback_request_that_changes_nothing_leaves_the_tab_to_the_socket(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key, comm, rt = await _dirty(runtimes)
        try:
            view = await sync_to_async(_fallback_post)(key, "nothing")
            assert view.count == 0  # it rendered the older stored state
            assert await rt.flush_state()
            assert not rt.view_instance.__dict__.get("_djust_auto_superseded")
        finally:
            await comm.disconnect()
    assert await _stored(key) == 1


async def test_a_fallback_request_that_changes_the_state_still_takes_the_tab(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key, comm, rt = await _dirty(runtimes)
        try:
            await sync_to_async(_fallback_post)(key, "nothing")
            await sync_to_async(_fallback_post)(key, "inc")  # 0 -> 1, claims
            await sync_to_async(_fallback_post)(key, "inc")  # 1 -> 2
            assert not await rt.flush_state()
            assert rt.view_instance.__dict__.get("_djust_auto_superseded") is True
        finally:
            await comm.disconnect()
    assert await _stored(key) == 2


# ---------------------------------------------------------------------------
# item 3: an over-limit turn's session write is ordered and bounded
# ---------------------------------------------------------------------------


def _order(key: str) -> Any:
    from django.contrib.sessions.backends.db import SessionStore

    return SessionStore(key).get("order")


async def _over_limit_socket(runtimes) -> tuple:
    key = await sync_to_async(sf.page_get)(Small)
    comm, _ = await harness.mount_view(Small, key)
    await harness.event(comm, "grow")  # deferred, over the limit
    return key, comm, runtimes[-1]


async def test_an_over_limit_turns_session_write_waits_for_the_save_still_running(runtimes):
    """The write goes through the runtime's one save queue: while an earlier
    save of this connection is still running it does not start, so it can
    never land before an older write."""
    import asyncio

    from djust.tests._ws_frames import has_frame, receive_until

    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key, comm, rt = await _over_limit_socket(runtimes)
        try:
            earlier: asyncio.Future = asyncio.get_running_loop().create_future()
            real_note = rt._note_over_limit

            async def note_then_start_another_save(*args, **kwargs):
                # Another save of this connection starts after the refused one
                # (as a flush or catch-up would) and is still running.
                await real_note(*args, **kwargs)
                rt._explicit_save_pending = earlier

            rt._note_over_limit = note_then_start_another_save
            await comm.send_json_to(
                {"type": "event", "event": "keep_and_note", "params": {}, "ref": 71}
            )
            await asyncio.sleep(0.3)
            assert await sync_to_async(_order)(key) is None  # still behind the earlier save
            earlier.set_result(None)
            frames = await receive_until(comm, has_frame(ref=71), what="the reply")
            assert frames[-1]["type"] in ("patch", "html_update"), frames
            assert await sync_to_async(_order)(key) == "placed"
        finally:
            await comm.disconnect()


async def test_an_over_limit_turns_session_write_is_bounded_by_the_save_deadline(
    runtimes, monkeypatch, caplog
):
    """A session write that outruns the deadline does not fail the turn: the
    reply is served, a WARNING says the write is still running, and it lands
    in order. (Over a socket the write holds the session's sync thread, so the
    reply itself still waits for that thread; what the deadline bounds is the
    turn's own wait on the save.)"""
    import logging
    import time

    from django.test import override_settings

    from djust.tests._ws_frames import has_frame, receive_until

    real = runtime._save_session_writes

    def slow(view):
        time.sleep(0.6)
        real(view)

    monkeypatch.setattr(runtime, "_save_session_writes", slow)
    with (
        _settings(DJUST_STATE_SAVE_INTERVAL=60),
        override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.2),
    ):
        key, comm, rt = await _over_limit_socket(runtimes)
        try:
            with caplog.at_level(logging.WARNING, logger="djust.runtime"):
                await comm.send_json_to(
                    {"type": "event", "event": "keep_and_note", "params": {}, "ref": 72}
                )
                frames = await receive_until(comm, has_frame(ref=72), what="the reply")
            assert frames[-1]["type"] in ("patch", "html_update"), frames
            assert "outran the save deadline" in caplog.text
            pending = rt._explicit_save_pending
            if pending is not None:
                await pending
            assert await sync_to_async(_order)(key) == "placed"
            assert rt.state_unsaved
        finally:
            await comm.disconnect()


# ---------------------------------------------------------------------------
# #4160: a full-page redirect sent through a push event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event, stored",
    [("checkout", 100), ("leave", 10), ("leave_unsaved", 0)],
    ids=["save=True", "save_state()", "neither"],
)
async def test_a_handler_that_sends_the_page_away_is_saved_before_its_reply_when_it_says_so(
    runtimes, event, stored
):
    """There is no server-side full-redirect call, so a handler that sends
    the browser to another page by pushing its own event is not seen as
    navigation. The documented means (``save=True``, ``self.save_state()``)
    store its state before the reply that carries the push, so the next page
    loads it; without either the turn is deferred like any other."""
    from djust.tests._ws_frames import has_type, receive_until

    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key = await sync_to_async(sf.page_get)(Counter)
        comm, _ = await harness.mount_view(Counter, key)
        try:
            frames = await harness.event_frames(comm, event)
            at_reply = await _stored(key)
            # The push that sends the page away follows the reply: by the time
            # the browser acts on it, the state must be stored.
            pushed = await receive_until(comm, has_type("push_event"), what="the push")
            at_push = await _stored(key)
        finally:
            await comm.disconnect()
    assert frames[-1]["type"] in ("patch", "html_update"), frames
    assert pushed[-1]["event"] == "go", pushed
    assert (at_reply, at_push) == (stored, stored)
