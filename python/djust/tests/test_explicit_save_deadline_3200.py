"""#3200: an explicit turn's save deadline counts storage, not thread queueing.

``commit_explicit_turn`` used to wrap two-plus ``sync_to_async`` hops in one
150 ms ``asyncio.wait_for``. With the default single sync thread those hops
queue behind every other session's handlers and renders, so a busy process
timed out saves that storage would have finished in a few milliseconds, and
told the user to reload.

The load here is deterministic: worker coroutines keep the one Django sync
thread busy with fixed-length jobs, the way other sessions' handlers do. No
assertion races a wall clock against the save itself.

This module deliberately does NOT start with ``test_exposure_``: the conftest
raises the save bound to 30 s for those modules (#3130), and these tests need
the production bound.
"""

import asyncio
import json
import threading
import time

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import runtime as runtime_module
from djust._exposure_sessions import server_state_adapter
from djust.tests.test_exposure_runtime import RuntimeView, make_request, mount

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.fixture
def staged(monkeypatch):
    from djust import LiveView

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


async def _stored_count(key):
    fresh = await sync_to_async(make_request)(key)
    adapter = await sync_to_async(server_state_adapter)(RuntimeView(), fresh)
    stored = await adapter.aload()
    return None if stored is None else stored["count"]


async def test_a_busy_sync_thread_does_not_fail_the_save(staged):
    """Queueing behind other sessions' work is not storage time.

    Four workers each hold the Django thread for 100 ms at a time, so every
    ``sync_to_async`` hop waits roughly 300 ms in the queue. The old shape spent
    its whole 150 ms budget there. The save itself takes milliseconds.
    """
    assert runtime_module.EVENT_STATE_SAVE_TIMEOUT_S == 0.150
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    transport.sent.clear()

    stop = False

    async def other_session():
        while not stop:
            await sync_to_async(time.sleep)(0.1)

    workers = [asyncio.ensure_future(other_session()) for _ in range(4)]
    try:
        await asyncio.sleep(0.05)  # let the queue fill
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    finally:
        stop = True
        await asyncio.gather(*workers)

    assert not transport.errors, transport.errors
    assert any(frame.get("type") in {"patch", "html_update"} for frame in transport.sent)
    assert await _stored_count(request.session.session_key) == 6


async def _until(predicate, what):
    """Poll a loop-side condition; the hang guard only, never a timing assert."""
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


def _first_save_blocks_then_fails(original, release, calls):
    """A deterministic slow store: the FIRST save blocks the Django thread until
    the test releases it, then fails (so nothing from that turn lands). Every
    later save is the real one."""

    def save(self, *args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            release.wait(timeout=30)
            raise OSError("STORE_SENTINEL")
        return original(self, *args, **kwargs)

    return save


async def test_slow_storage_is_withheld_but_not_a_reload(staged, caplog):
    """A save that really outruns the deadline keeps the E3 guarantee.

    No success frame, the snapshot token is revoked and the next update is
    full HTML, as before. But the error is transient and does not tell the
    user to reload, and once storage answers a catch-up turn commits the kept
    value and sends full HTML without waiting for the user. The deadline
    comes from ``DJUST_EXPLICIT_STATE_SAVE_TIMEOUT``.
    """
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    view = runtime.view_instance
    transport.sent.clear()
    before = await _stored_count(request.session.session_key)
    release = threading.Event()
    original = SessionStore.save
    calls = []

    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05, DEBUG=False):
        SessionStore.save = _first_save_blocks_then_fails(original, release, calls)
        try:
            dispatch = asyncio.ensure_future(
                runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
            )
            try:
                await _until(lambda: transport.errors or dispatch.done(), "the error frame")
                # Deferred, not failed: the value is kept, nothing is acked yet.
                assert view.count == 6, "the handler ran and its value is kept"
                assert not [
                    f for f in transport.sent if f.get("type") in {"patch", "html_update", "noop"}
                ]
                [error] = transport.errors
                assert error["code"] == "state_error"
                assert error["transient"] is True
                assert "reload" not in error["error"].lower()
                assert error["state_snapshot_signed"] is None
                assert view._force_full_html is True
            finally:
                release.set()
            await asyncio.wait_for(dispatch, timeout=10)
            # The catch-up turn: after the blocked save settles it commits the
            # kept value and sends full HTML on its own.
            await asyncio.wait_for(runtime._explicit_catch_up, timeout=10)
        finally:
            SessionStore.save = original

    catch_up = [f for f in transport.sent if f.get("source") == "async"]
    assert [f["type"] for f in catch_up] == ["html_update"], transport.sent
    assert ">6<" in catch_up[0]["html"]
    assert len(transport.errors) == 1, transport.errors
    assert before != 6 and await _stored_count(request.session.session_key) == 6
    assert view._force_full_html is False
    assert "STORE_SENTINEL" not in caplog.text
    assert "STORE_SENTINEL" not in json.dumps(transport.sent)

    # And the next ordinary turn is acknowledged as usual.
    transport.sent.clear()
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert not transport.errors[1:], transport.errors
    assert any(f.get("type") in {"patch", "html_update"} for f in transport.sent)
    assert await _stored_count(request.session.session_key) == 7


async def test_a_late_save_never_overwrites_a_newer_acked_one(staged):
    """Review probe of #3206, as a regression: saves are ordered per runtime.

    Turn N's save blocks in storage past its deadline. Turn N+1 then runs in a
    DIFFERENT thread-sensitive context (as every SSE POST does), so nothing
    queues it behind N on the Django thread. Before, N+1 was acked with 7 and
    N's late write then left storage at 6: the browser showed a state storage
    no longer had. Now N+1 waits for N's save and is deferred while it runs;
    whatever the browser is shown is what storage ends with.
    """
    from asgiref.sync import SyncToAsync, ThreadSensitiveContext
    from django.contrib.sessions.models import Session

    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    transport.sent.clear()
    release = threading.Event()
    original = Session.save
    blocked = []

    def slow_row_write(self, *args, **kwargs):
        blocked.append(True)
        if len(blocked) == 1:
            release.wait(timeout=30)
        return original(self, *args, **kwargs)

    var = SyncToAsync.thread_sensitive_context
    ctx_a, ctx_b = ThreadSensitiveContext(), ThreadSensitiveContext()
    event = {"type": "event", "event": "increment", "params": {}}
    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05):
        Session.save = slow_row_write
        try:
            token = var.set(ctx_a)
            turn_n = asyncio.ensure_future(runtime.dispatch_event(dict(event)))
            var.reset(token)
            await _until(lambda: transport.errors or turn_n.done(), "turn N's deferral")
            await asyncio.wait_for(turn_n, 10)
            token = var.set(ctx_b)
            turn_n1 = asyncio.ensure_future(runtime.dispatch_event(dict(event)))
            var.reset(token)
            await asyncio.wait_for(turn_n1, 10)
            acked = [f for f in transport.sent if f.get("type") in {"patch", "html_update"}]
            assert not acked, "turn N+1 must not be acked while turn N's save is running"
            release.set()
            await asyncio.wait_for(runtime._explicit_save_pending, 10)
            catch_up = runtime._explicit_catch_up
            if catch_up is not None:
                await asyncio.wait_for(catch_up, 10)
        finally:
            release.set()
            Session.save = original
            await ctx_a.__aexit__(None, None, None)
            await ctx_b.__aexit__(None, None, None)

    shown = [f for f in transport.sent if f.get("type") in {"patch", "html_update"}]
    assert shown and ">7<" in json.dumps(shown[-1]), transport.sent
    assert await _stored_count(request.session.session_key) == 7


async def test_a_deferred_child_save_at_mount_is_terminal(staged, monkeypatch):
    """Review B1 of #3206: at mount there is no page to catch up.

    A slow mount-time child save used to answer "Child state unavailable.
    Please reload the page." and must still do so, not the transient message
    (which promised an update that no mount frame would ever deliver).
    """
    from djust.runtime import ViewRuntime
    from djust.tests.test_exposure_child_events import EventParent
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    request = await sync_to_async(make_request)()
    request.marker = "mount"
    request.child_allowed = True
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    release = threading.Event()
    original = SessionStore.save
    seen = []

    def slow(self, *args, **kwargs):
        # The mount's first save is the root's; the second is the child tree
        # save that goes through the explicit deadline.
        seen.append(True)
        if len(seen) == 2:
            release.wait(timeout=30)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 0.05)
    SessionStore.save = slow
    try:
        with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
            mounting = asyncio.ensure_future(
                runtime.dispatch_mount(
                    {
                        "type": "mount",
                        "view": EventParent.__module__ + "." + EventParent.__name__,
                        "url": request.path,
                    }
                )
            )
            try:
                await _until(lambda: transport.errors or mounting.done(), "the mount to answer")
            finally:
                release.set()
            await asyncio.wait_for(mounting, 10)
        await sync_to_async(lambda: None)()
    finally:
        release.set()
        SessionStore.save = original
    assert len(seen) >= 2, "the mount-time child save must have run"
    assert not any(f.get("type") == "mount" for f in transport.sent), transport.sent
    [error] = transport.errors
    assert error["error"] == "Child state unavailable. Please reload the page."
    assert not error.get("transient")
    assert runtime._explicit_catch_up is None


async def test_a_refused_save_is_still_terminal(staged):
    """Only a timeout is transient; an identity change is still a hard error."""
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    transport.sent.clear()
    view = runtime.view_instance
    # The turn's authorized request no longer matches the mount's binding.
    view._djust_event_request = request
    runtime._explicit_mount_binding = object()
    committed = await runtime.commit_explicit_turn(view, source="event")
    assert committed is False
    [error] = transport.errors
    assert error["code"] == "state_error"
    assert not error.get("transient")


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 0.150),
        (0.5, 0.5),
        (2, 2.0),
        (0, 0.150),
        (-1, 0.150),
        (11, 0.150),
        (True, 0.150),
        ("1", 0.150),
        (float("nan"), 0.150),
        (float("inf"), 0.150),
    ],
)
def test_explicit_save_timeout_setting(value, expected):
    kwargs = {} if value is None else {"DJUST_EXPLICIT_STATE_SAVE_TIMEOUT": value}
    with override_settings(**kwargs):
        assert runtime_module.explicit_state_save_timeout() == expected


@pytest.mark.parametrize(
    "value,flagged",
    [(0, True), (10.5, True), ("1", True), (True, True), (None, True), (0.5, False), (10, False)],
)
def test_explicit_save_timeout_system_check(value, flagged):
    from djust.checks.configuration import _check_explicit_state_save_timeout

    errors = []
    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=value):
        _check_explicit_state_save_timeout(errors)
    assert [e.id for e in errors] == (["djust.C024"] if flagged else [])


def test_explicit_save_timeout_system_check_is_silent_when_unset_or_suppressed():
    from djust.checks.configuration import _check_explicit_state_save_timeout

    errors = []
    _check_explicit_state_save_timeout(errors)
    with override_settings(
        DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0, DJUST_CONFIG={"suppress_checks": ["C024"]}
    ):
        _check_explicit_state_save_timeout(errors)
    assert errors == []
