"""Save frequency, step 3: the deferred save and its flush, behind the setting.

With ``DJUST_STATE_SAVE_INTERVAL`` unset (0, the default) every socket turn
saves before its frame, as before; the first group pins that. With it set,
a WebSocket or SSE turn ``_save_policy`` finds deferrable marks the view and
answers at once, and the view's state reaches the store

* when the one-shot timer fires (no later than the interval after the first
  unsaved change);
* when the socket closes, before ``disconnected()`` and the release;
* when the view is replaced on the socket (a navigation), before it goes;
* with any later immediate turn (it writes the whole view).

A URL change, a turn that queues navigation, a turn whose handler wrote the
session itself, a strict store and a view with a declared ``persist=`` field
stay immediate. Timers are fired by hand, never waited for.
"""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path as _path

from djust import LiveView, _automatic_state, _per_turn, _save_policy, runtime, state
from djust.decorators import event_handler
from djust.tests import _per_turn_harness as harness
from djust.tests import _save_frequency as sf

pytestmark = pytest.mark.django_db(transaction=True)

MODULE = __name__
INTERVAL = 5
DISCONNECTED: List[Any] = []


class Deferred(LiveView):
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

    @event_handler()
    def remember(self, **kwargs):
        self.count += 1
        self.request.session["cart"] = ["socks"]

    @event_handler()
    def note(self, **kwargs):
        """Writes the session only: an immediate turn that changes no state."""
        self.request.session["seen"] = True

    @event_handler()
    def patch_url(self, **kwargs):
        self.count += 1
        self.live_patch(params={"page": 2})

    def disconnected(self):
        key = self.__dict__.get("_djust_tab_session_key")
        if key is not None:
            DISCONNECTED.append(sf.stored_envelopes(key))

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class Declared(Deferred):
    kept = state(0, persist="server")


urlpatterns = [_path(sf.PATH.strip("/") + "/", Deferred.as_view())]


@pytest.fixture(autouse=True)
def _each(monkeypatch):
    sf.one_tab(monkeypatch)
    DISCONNECTED.clear()


@pytest.fixture
def runtimes(monkeypatch):
    """Every ViewRuntime built during the test, in order."""
    built: List[Any] = []
    real = runtime.ViewRuntime.__init__

    def recording(self, *args, **kwargs):
        real(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(runtime.ViewRuntime, "__init__", recording)
    return built


def _count(key: str) -> Any:
    found = sf.stored_envelopes(key)
    assert len(found) == 1, list(found)
    return next(iter(found.values()))["values"].get("count")


async def _stored(key: str) -> Any:
    return await sync_to_async(_count)(key)


def _settings(**extra):
    return harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, **extra)


async def _page() -> str:
    return await sync_to_async(sf.page_get)(Deferred)


async def _fire(rt: Any) -> bool:
    """Run the armed timer's callback now and wait for the flush it starts."""
    handle = rt._state_timer
    assert handle is not None, "no timer armed"
    handle.cancel()
    rt._state_timer_fired()
    await rt._state_flush_task
    return rt._state_dirty_view is None


# ---------------------------------------------------------------------------
# Default: nothing is deferred
# ---------------------------------------------------------------------------


async def test_by_default_every_websocket_turn_is_in_the_store_before_its_frame(runtimes):
    with _settings():
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        try:
            for expected in (1, 2):
                await harness.event(comm, "inc")
                assert await _stored(key) == expected
            assert runtimes[-1]._state_timer is None
        finally:
            await comm.disconnect()


async def test_an_invalid_interval_is_read_as_zero(caplog):
    _save_policy._WARNED.clear()
    for value in ("5", True, 0.1, 61, -1):
        with override_settings(DJUST_STATE_SAVE_INTERVAL=value):
            assert _save_policy.state_save_interval() == 0.0
    assert sum("DJUST_STATE_SAVE_INTERVAL" in r.getMessage() for r in caplog.records) == 1
    for value in (0, 0.5, 5, 60):
        with override_settings(DJUST_STATE_SAVE_INTERVAL=value):
            assert _save_policy.state_save_interval() == float(value)


# ---------------------------------------------------------------------------
# Deferred: the turn answers, the flush writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deferral", ["on", "off"])
async def test_a_deferred_turn_answers_and_the_timer_writes_it(runtimes, monkeypatch, deferral):
    off = ("deferred_save",) if deferral == "off" else ()
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL), harness.shortcuts(monkeypatch, *off):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        rt = runtimes[-1]
        try:
            reply = await harness.event(comm, "inc")
            assert reply["type"] in ("patch", "html_update"), reply
            if deferral == "off":
                assert await _stored(key) == 1 and rt._state_timer is None
                return
            assert await _stored(key) == 0  # answered; not yet stored
            assert rt.state_unsaved
            first = rt._state_timer.when()
            delay = first - asyncio.get_running_loop().time()
            assert 0.75 * INTERVAL - 0.5 <= delay <= INTERVAL
            await harness.event(comm, "inc")
            assert rt._state_timer.when() == first  # a later change does not push it back
            assert await _fire(rt)
            assert await _stored(key) == 2
            assert rt._state_timer is None and not rt.state_unsaved
        finally:
            await comm.disconnect()


async def test_a_turn_that_changes_nothing_arms_nothing(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        rt = runtimes[-1]
        try:
            await harness.event(comm, "nothing")
            assert rt._state_timer is None and not rt.state_unsaved
        finally:
            await comm.disconnect()


async def test_a_stored_state_past_half_its_lifetime_is_rewritten_by_an_unchanged_turn(
    runtimes, monkeypatch
):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        rt = runtimes[-1]
        try:
            monkeypatch.setattr(_automatic_state, "_still_fresh", lambda basis, now: False)
            await harness.event(comm, "nothing")
            assert rt.state_unsaved
        finally:
            await comm.disconnect()


async def test_closing_the_socket_writes_before_disconnected_and_release(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        runtimes[-1].view_instance._djust_tab_session_key = key
        await harness.event(comm, "inc")
        await harness.event(comm, "inc")
        assert await _stored(key) == 0
        await comm.disconnect()
    assert DISCONNECTED and next(iter(DISCONNECTED[0].values()))["values"]["count"] == 2
    assert await _stored(key) == 2


async def test_a_view_replaced_on_the_socket_is_written_before_it_goes(runtimes):
    """A page ``mount`` on the same socket replaces the view (as a navigation
    does); mounting the same tab again restores what it was flushed with."""
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        try:
            await harness.event(comm, "inc")
            await harness.event(comm, "inc")
            assert await _stored(key) == 0
            await comm.send_json_to(
                {"type": "mount", "view": f"{MODULE}.Deferred", "url": sf.PATH, "tab": sf.TAB}
            )
            frame = await comm.receive_json_from()
            assert frame["type"] == "mount", frame
            assert ">2</pre>" in frame["html"], frame["html"][:300]
        finally:
            await comm.disconnect()


async def test_an_immediate_turn_writes_the_earlier_deferred_changes_too(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        rt = runtimes[-1]
        try:
            await harness.event(comm, "inc")
            await harness.event(comm, "inc")
            assert rt.state_unsaved
            await harness.event(comm, "remember")  # writes the session: immediate
            assert await _stored(key) == 3
            assert rt._state_timer is None and not rt.state_unsaved
        finally:
            await comm.disconnect()


async def test_an_immediate_turn_that_changes_nothing_still_writes_the_unsaved_ones(runtimes):
    """Its own change hint says "nothing changed"; the save must not trust
    it, since earlier deferred turns' changes are in memory only."""
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        rt = runtimes[-1]
        try:
            await harness.event(comm, "inc")
            await harness.event(comm, "note")  # immediate (session write)
            assert await _stored(key) == 1
            assert not rt.state_unsaved and rt._state_timer is None
        finally:
            await comm.disconnect()


async def test_an_unsaved_change_means_the_store_is_behind(runtimes):
    """The over-limit refusal rolls back to the store only when it is not
    behind the view (#4095); an unflushed change makes it behind."""
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        rt = runtimes[-1]
        try:
            assert not rt._explicit_store_behind()
            await harness.event(comm, "inc")
            assert rt._explicit_store_behind()
            assert await _fire(rt)
            assert not rt._explicit_store_behind()
        finally:
            await comm.disconnect()


# ---------------------------------------------------------------------------
# What stays immediate
# ---------------------------------------------------------------------------


async def test_a_handler_that_writes_the_session_is_saved_before_its_frame(runtimes):
    from django.contrib.sessions.backends.db import SessionStore

    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        try:
            await harness.event(comm, "remember")
            stored = await sync_to_async(lambda: dict(SessionStore(key).items()))()
            assert stored["cart"] == ["socks"]
            assert await _stored(key) == 1
        finally:
            await comm.disconnect()


async def test_a_turn_that_queues_navigation_is_saved_before_its_frame(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key = await _page()
        comm, _ = await harness.mount_view(Deferred, key)
        try:
            await harness.event(comm, "patch_url")
            assert await _stored(key) == 1
            assert runtimes[-1]._state_timer is None
        finally:
            await comm.disconnect()


async def test_strict_store_and_declared_persistence_stay_immediate(settings):
    view = Deferred()
    declared = Declared()
    transport = runtime.WSConsumerTransport(object())
    with override_settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        assert _save_policy.save_mode(view, transport, source="event") == "deferred"
        assert _save_policy.save_mode(view, transport, source="url_change") == "immediate"
        assert _save_policy.save_mode(declared, transport, source="event") == "immediate"
        assert _save_policy.save_mode(view, object(), source="event") == "immediate"
        with override_settings(DJUST_STATE_STORE_STRICT=True):
            assert _save_policy.save_mode(view, transport, source="event") == "immediate"
    assert _save_policy.save_mode(view, transport, source="event") == "immediate"


# ---------------------------------------------------------------------------
# The flush itself
# ---------------------------------------------------------------------------


async def _dirty_counter(runtimes) -> tuple:
    key = await _page()
    comm, _ = await harness.mount_view(Deferred, key)
    await harness.event(comm, "inc")
    rt = runtimes[-1]
    assert rt.state_unsaved
    return key, comm, rt


async def test_a_flush_that_is_not_authorized_writes_nothing(runtimes, monkeypatch):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key, comm, rt = await _dirty_counter(runtimes)
        try:

            async def refused(view):
                raise PermissionError("logged out")

            monkeypatch.setattr(rt, "authorize_explicit_turn", refused)
            assert not await rt.flush_state()
            assert await _stored(key) == 0
            assert not rt.state_unsaved and rt._state_timer is None
        finally:
            monkeypatch.undo()
            await comm.disconnect()


async def test_a_failed_flush_keeps_the_change_and_retries(runtimes, monkeypatch, caplog):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key, comm, rt = await _dirty_counter(runtimes)
        try:
            real = rt._save_explicit_root
            calls = []

            def failing(view, request):
                calls.append(1)
                if len(calls) <= 3:
                    raise OSError("storage down")
                return real(view, request)

            monkeypatch.setattr(rt, "_save_explicit_root", failing)
            for attempt in (1, 2, 3):
                assert not await _fire(rt)
                assert rt.state_unsaved and rt._state_timer is not None
                assert rt._state_flush_failures == attempt
            assert any("3 times in a row" in r.getMessage() for r in caplog.records)
            assert await _fire(rt)
            assert await _stored(key) == 1
        finally:
            monkeypatch.undo()
            await comm.disconnect()


async def test_a_busy_connection_is_flushed_a_moment_later(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key, comm, rt = await _dirty_counter(runtimes)
        try:
            await rt._explicit_event_lock.acquire()
            try:
                assert not await rt.flush_state()
            finally:
                rt._explicit_event_lock.release()
            assert rt.state_unsaved
            assert rt._state_timer.when() - asyncio.get_running_loop().time() <= 0.5
            assert await _fire(rt)
            assert await _stored(key) == 1
        finally:
            await comm.disconnect()


async def test_a_stale_holder_cannot_overwrite(runtimes):
    """A holds an unflushed change; B (the fallback, another process) restores
    the tab and saves; A's flush fires: the store still holds B's state."""
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key, comm, rt = await _dirty_counter(runtimes)
        try:
            await sync_to_async(sf.fallback_post)(Deferred, key, "inc")  # B: 0 -> 1
            await sync_to_async(sf.fallback_post)(Deferred, key, "inc")  # B: 1 -> 2
            assert not await rt.flush_state()
            assert rt.view_instance.__dict__.get("_djust_auto_superseded") is True
            assert not rt.state_unsaved
        finally:
            await comm.disconnect()
    assert await _stored(key) == 2


async def test_sse_turns_defer_the_same_way(monkeypatch):
    seen = []
    real = _save_policy.save_mode

    def recording(view, transport, *, source):
        answer = real(view, transport, source=source)
        seen.append((type(transport).__name__, answer))
        return answer

    monkeypatch.setattr(_save_policy, "save_mode", recording)
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key = await _page()
        await sf.sse_run(Deferred, key, [("inc", None)], MODULE)
    assert seen == [("SSESessionTransport", "deferred")]
    assert await _stored(key) == 0  # its close flush is the SSE step's


def test_the_switch_is_on_by_default():
    assert _per_turn.on("deferred_save")
