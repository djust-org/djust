"""Opt-in pinned session worker pool (``LIVEVIEW_CONFIG["worker_threads"]``, #3074).

Real ``WebsocketCommunicator`` round trips through ``LiveViewConsumer``:

* with the pool on, two sessions' sync handlers run at the same time on two
  different threads, each session stays on ONE thread for mount and every
  event, and each session sees only its own state;
* one session's events are applied and answered in order;
* with the setting absent (the default), every session shares asgiref's one
  thread and handlers never overlap — today's behaviour, unchanged;
* disconnecting releases the session's slot, and the least-loaded slot is
  picked next;
* the setting's spellings, and the ``djust.C021`` system check for a bad one.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.decorators import event_handler

pytest.importorskip("channels")

_EVENTS: list = []
_EVENTS_LOCK = threading.Lock()


def _record(view, what):
    with _EVENTS_LOCK:
        _EVENTS.append((view.session_tag, what, threading.get_ident(), time.monotonic()))


class _PoolView(LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_worker_pool_3074._PoolView">'
        "{{ session_tag }}:{{ count }}</div>"
    )

    def mount(self, request, **kwargs):
        self.session_tag = request.GET.get("tag", "?") if request is not None else "?"
        self.count = 0
        _record(self, "mount")

    @event_handler()
    def slow(self, **kwargs):
        _record(self, "start")
        time.sleep(0.3)
        self.count += 1
        _record(self, "end")

    @event_handler()
    def incr(self, **kwargs):
        self.count += 1
        _record(self, f"incr{self.count}")


async def _connect(tag):
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
    await communicator.receive_json_from(timeout=3)  # connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}._PoolView", "url": f"/pool/?tag={tag}"}
    )
    frame = await _receive_until(communicator, "mount")
    assert frame.get("type") == "mount", frame
    assert f"{tag}:0" in frame.get("html", ""), frame
    return communicator


async def _receive_until(communicator, wanted, *, tries=8, timeout=5):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted:
            return last
    return last


def _events(tag, what=None):
    with _EVENTS_LOCK:
        return [e for e in _EVENTS if e[0] == tag and (what is None or e[1] == what)]


@pytest.fixture
def worker_threads(monkeypatch):
    """Set ``worker_threads`` on the live config for one test, and reset the pool."""
    from djust import worker_pool

    def _set(value):
        monkeypatch.setitem(djust_config._config, "worker_threads", value)

    monkeypatch.setattr(worker_pool, "_pool", [])
    _EVENTS.clear()
    yield _set
    _EVENTS.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pool_runs_two_sessions_concurrently_each_pinned_to_one_thread(worker_threads):
    worker_threads(2)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect("A")
        b = await _connect("B")
        try:
            for ref in (1, 2):
                await a.send_json_to({"type": "event", "event": "slow", "params": {}, "ref": ref})
                await b.send_json_to({"type": "event", "event": "slow", "params": {}, "ref": ref})
                fa, fb = await asyncio.gather(
                    _receive_until(a, "patch"), _receive_until(b, "patch")
                )
                assert fa.get("type") == "patch" and fb.get("type") == "patch", (fa, fb)
        finally:
            await a.disconnect()
            await b.disconnect()

    # Pinned: every hop of a session (mount + both events) ran on ONE thread.
    threads_a = {e[2] for e in _events("A")}
    threads_b = {e[2] for e in _events("B")}
    assert len(threads_a) == 1, _events("A")
    assert len(threads_b) == 1, _events("B")
    # Spread: the second session took the other (least-loaded) thread.
    assert threads_a != threads_b
    # Concurrent: in each round, B's handler started before A's finished.
    for (a_start, a_end), (b_start, b_end) in zip(
        zip(_events("A", "start"), _events("A", "end")),
        zip(_events("B", "start"), _events("B", "end")),
    ):
        assert b_start[3] < a_end[3] and a_start[3] < b_end[3], "handlers did not overlap"
    # Isolated: each session counted only its own events.
    assert [e[1] for e in _events("A")] == ["mount", "start", "end", "start", "end"]
    assert [e[1] for e in _events("B")] == ["mount", "start", "end", "start", "end"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pool_keeps_one_sessions_events_in_order(worker_threads):
    worker_threads(3)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect("O")
        try:
            for ref in range(1, 6):
                await a.send_json_to({"type": "event", "event": "incr", "params": {}, "ref": ref})
            frames = [await _receive_until(a, "patch") for _ in range(5)]
        finally:
            await a.disconnect()
    assert [f.get("ref") for f in frames] == [1, 2, 3, 4, 5]
    versions = [f.get("version") for f in frames]
    assert versions == sorted(versions) and len(set(versions)) == 5, versions
    assert [e[1] for e in _events("O")][1:] == ["incr1", "incr2", "incr3", "incr4", "incr5"]
    assert len({e[2] for e in _events("O")}) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_default_keeps_every_session_on_one_shared_thread(worker_threads):
    worker_threads(None)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect("SA")
        b = await _connect("SB")
        try:
            await a.send_json_to({"type": "event", "event": "slow", "params": {}, "ref": 1})
            await b.send_json_to({"type": "event", "event": "slow", "params": {}, "ref": 1})
            await asyncio.gather(_receive_until(a, "patch"), _receive_until(b, "patch"))
        finally:
            await a.disconnect()
            await b.disconnect()
    from djust import worker_pool

    assert worker_pool.pool_stats() == []  # no pool was created
    assert {e[2] for e in _events("SA")} == {e[2] for e in _events("SB")}
    (a_start,), (a_end,) = _events("SA", "start"), _events("SA", "end")
    (b_start,), (b_end,) = _events("SB", "start"), _events("SB", "end")
    # Serial on the shared thread: one handler ended before the other began.
    assert a_end[3] <= b_start[3] or b_end[3] <= a_start[3]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_disconnect_releases_the_slot(worker_threads):
    from djust import worker_pool

    worker_threads(2)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect("R1")
        b = await _connect("R2")
        assert sorted(s["sessions"] for s in worker_pool.pool_stats()) == [1, 1]
        await a.disconnect()
        await asyncio.sleep(0.05)
        assert sorted(s["sessions"] for s in worker_pool.pool_stats()) == [0, 1]
        c = await _connect("R3")  # takes the freed slot, not the busy one
        assert sorted(s["sessions"] for s in worker_pool.pool_stats()) == [1, 1]
        await b.disconnect()
        await c.disconnect()
        await asyncio.sleep(0.05)
    assert [s["sessions"] for s in worker_pool.pool_stats()] == [0, 0]
    assert {e[2] for e in _events("R3")} == {e[2] for e in _events("R1")}


@pytest.mark.parametrize(
    "value, expected",
    [(None, 0), (False, 0), (0, 0), (1, 1), (7, 7)],
)
def test_resolve_pool_size(value, expected):
    from djust.worker_pool import resolve_pool_size

    assert resolve_pool_size(value) == expected


def test_resolve_pool_size_auto_is_the_cpu_count_capped(monkeypatch):
    from djust import worker_pool

    monkeypatch.setattr(worker_pool, "_available_cpus", lambda: 12)
    assert worker_pool.resolve_pool_size(True) == 12
    assert worker_pool.resolve_pool_size("auto") == 12
    monkeypatch.setattr(worker_pool, "_available_cpus", lambda: 256)
    assert worker_pool.resolve_pool_size(True) == worker_pool.AUTO_MAX_THREADS


@pytest.mark.parametrize("value", [-1, 2.5, "8", "yes", [4]])
def test_resolve_pool_size_rejects_other_values(value):
    from djust.worker_pool import resolve_pool_size

    with pytest.raises(ValueError, match="worker_threads"):
        resolve_pool_size(value)


def test_invalid_value_is_logged_and_means_stock(worker_threads, caplog):
    from djust.worker_pool import configured_pool_size

    worker_threads("eight")
    with caplog.at_level("WARNING", logger="djust.worker_pool"):
        assert configured_pool_size() == 0
    assert "worker_threads" in caplog.text


def test_system_check_c021_reports_an_invalid_value(worker_threads):
    from djust.checks.configuration import _check_worker_threads

    errors: list = []
    worker_threads("eight")
    _check_worker_threads(errors)
    assert [e.id for e in errors] == ["djust.C021"]

    errors.clear()
    worker_threads(4)
    _check_worker_threads(errors)
    assert errors == []
