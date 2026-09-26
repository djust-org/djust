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


async def test_slow_storage_is_withheld_but_not_a_reload(staged, caplog):
    """A save that really outruns the deadline keeps the E3 guarantee.

    No success frame, the snapshot token is revoked and the next update is
    full HTML, as before. But the error is transient and does not tell the
    user to reload: the value is still on the view, and the next turn saves
    it. The deadline comes from ``DJUST_EXPLICIT_STATE_SAVE_TIMEOUT``.
    """
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    view = runtime.view_instance
    transport.sent.clear()
    before = await _stored_count(request.session.session_key)
    release = threading.Event()
    original = SessionStore.save
    calls = []

    def slow_then_failing_store(self, *args, **kwargs):
        # A deterministic slow store: blocks the Django thread until the test
        # releases it, then fails, so nothing from this turn lands.
        calls.append(True)
        release.wait(timeout=30)
        raise OSError("STORE_SENTINEL")

    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05, DEBUG=False):
        SessionStore.save = slow_then_failing_store
        try:
            dispatch = asyncio.ensure_future(
                runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
            )
            try:
                for _ in range(1000):
                    if transport.errors or dispatch.done():
                        break
                    await asyncio.sleep(0.01)
            finally:
                release.set()
            await asyncio.wait_for(dispatch, timeout=10)
            # The abandoned save still runs; let the thread finish it (FIFO).
            await sync_to_async(lambda: None)()
        finally:
            SessionStore.save = original

        assert calls == [True]
        assert view.count == 6, "the handler ran and its value is kept"
        assert not [f for f in transport.sent if f.get("type") in {"patch", "html_update", "noop"}]
        [error] = transport.errors
        assert error["code"] == "state_error"
        assert error["transient"] is True
        assert "reload" not in error["error"].lower()
        assert error["state_snapshot_signed"] is None
        assert view._force_full_html is True
        assert await _stored_count(request.session.session_key) == before

        # The next turn saves the kept value along with its own, and answers
        # with full HTML: the client catches up without a reload.
        transport.sent.clear()
        transport.errors.clear()
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert not transport.errors, transport.errors
    assert any(
        frame.get("type") == "html_update" and ">7<" in frame.get("html", "")
        for frame in transport.sent
    ), transport.sent
    assert await _stored_count(request.session.session_key) == 7
    assert "STORE_SENTINEL" not in caplog.text
    assert "STORE_SENTINEL" not in json.dumps(transport.sent)


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
