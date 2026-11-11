"""Worker pool lanes (``LIVEVIEW_CONFIG["worker_pinning"] = False``).

Pinned (the default), each WebSocket connection keeps one pool thread, and a
slow hop of one connection holds up every connection pinned to the same thread
while other threads may be idle. A lane keeps the connection's own guarantee
(one hop at a time, in order) and runs each hop on any free thread.

Real ``WebsocketCommunicator`` round trips through ``LiveViewConsumer``:

* pinned, a connection that shares a thread with a slow one waits for it
  (today's behaviour, measured here so the contrast stays visible);
* with lanes, the same connection is answered while the slow hop runs;
* with lanes, one connection's events never overlap and are applied and
  answered in order, even when they are queued at once;
* the pool's stale-connection check (``db_check_due``, #3095) still runs before
  a lane's next hop;

and the lane executor on its own: order, no overlap, an exception reaches the
caller, a call cancelled before it starts is skipped, the asgiref entry does not
outlive the lane, and the pool's threads start from an empty context.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import threading
import time
import weakref

import pytest
from asgiref.sync import SyncToAsync, sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.decorators import event_handler

pytest.importorskip("channels")

_EVENTS: list = []
_EVENTS_LOCK = threading.Lock()


def _record(view, what):
    with _EVENTS_LOCK:
        _EVENTS.append((view.tag, what, threading.get_ident(), time.monotonic()))


class _LaneView(LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_worker_lanes._LaneView">{{ tag }}:{{ count }}</div>'
    )

    def mount(self, request, **kwargs):
        self.tag = request.GET.get("tag", "?") if request is not None else "?"
        self.count = 0

    @event_handler()
    def slow(self, **kwargs):
        _record(self, "start")
        time.sleep(0.8)
        self.count += 1
        _record(self, "end")

    @event_handler()
    def step(self, **kwargs):
        _record(self, "start")
        time.sleep(0.05)
        self.count += 1
        _record(self, f"end{self.count}")


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
    connected, _ = await communicator.connect(timeout=10)
    assert connected
    await communicator.receive_json_from()  # connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}._LaneView", "url": f"/lanes/?tag={tag}"}
    )
    frame = await _receive_until(communicator, "mount")
    assert frame.get("type") == "mount", frame
    return communicator


async def _receive_until(communicator, wanted, *, tries=8, timeout=10):
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
def pool(monkeypatch):
    """Set the pool's size and pinning on the live config for one test, with fresh pools."""
    from djust import worker_pool

    def _set(threads, pinning):
        monkeypatch.setitem(djust_config._config, "worker_threads", threads)
        monkeypatch.setitem(djust_config._config, "worker_pinning", pinning)

    monkeypatch.setattr(worker_pool, "_pool", [])
    lane_pools: dict = {}
    monkeypatch.setattr(worker_pool, "_lane_pools", lane_pools)
    _EVENTS.clear()
    yield _set
    _EVENTS.clear()
    for lane_pool_ in lane_pools.values():
        lane_pool_.close()


async def _slow_neighbour_case():
    """A runs a slow event; C, the third connection on a 2-thread pool (pinned: A's
    thread), sends a quick one while it runs. Returns C's latency and A's frame."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect("A")
        b = await _connect("B")
        c = await _connect("C")
        try:
            await a.send_json_to({"type": "event", "event": "slow", "params": {}, "ref": 1})
            await asyncio.sleep(0.2)  # A's handler is running
            t0 = time.monotonic()
            await c.send_json_to({"type": "event", "event": "step", "params": {}, "ref": 1})
            fc = await _receive_until(c, "patch")
            c_latency = time.monotonic() - t0
            fa = await _receive_until(a, "patch")
        finally:
            for comm in (a, b, c):
                await comm.disconnect()
    return c_latency, fa, fc


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pinned_a_connection_on_a_slow_ones_thread_waits_for_it(pool):
    pool(2, True)
    c_latency, fa, fc = await _slow_neighbour_case()
    assert fa.get("type") == "patch" and fc.get("type") == "patch", (fa, fc)
    (a_end,) = _events("A", "end")
    (c_start,) = _events("C", "start")
    # Pinned: A and C share a thread, so C's handler started only after A's ended.
    assert a_end[2] == c_start[2]
    assert c_start[3] >= a_end[3]
    assert c_latency > 0.4, c_latency


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lanes_a_slow_connection_holds_up_only_itself(pool):
    pool(2, False)
    c_latency, fa, fc = await _slow_neighbour_case()
    assert fa.get("type") == "patch" and fc.get("type") == "patch", (fa, fc)
    (a_start,) = _events("A", "start")
    (a_end,) = _events("A", "end")
    (c_start,) = _events("C", "start")
    # C ran on the other thread, while A's handler was still running.
    assert a_start[3] < c_start[3] < a_end[3]
    assert c_start[2] != a_start[2]
    # No bound on ``c_latency`` (#3711): ``a_start < c_start < a_end`` above IS the claim
    # (C's handler began while A's was still running), as an ordering, and a stalled
    # runner stretches the latency without changing it.


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lanes_keep_one_connections_events_in_order_and_apart(pool):
    pool(4, False)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect("O")
        try:
            for ref in range(1, 7):  # all queued at once
                await a.send_json_to({"type": "event", "event": "step", "params": {}, "ref": ref})
            frames = [await _receive_until(a, "patch") for _ in range(6)]
        finally:
            await a.disconnect()
    assert [f.get("ref") for f in frames] == [1, 2, 3, 4, 5, 6]
    versions = [f.get("version") for f in frames]
    assert versions == sorted(versions) and len(set(versions)) == 6, versions
    ends = [e[1] for e in _events("O") if e[1] != "start"]
    assert ends == ["end1", "end2", "end3", "end4", "end5", "end6"]
    # Never two at once: each handler started after the previous one ended.
    starts = _events("O", "start")
    finished = [e for e in _events("O") if e[1].startswith("end")]
    for previous_end, next_start in zip(finished, starts[1:]):
        assert next_start[3] >= previous_end[3]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lanes_release_and_report_their_connections(pool):
    from djust import worker_pool

    pool(2, False)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect("R1")
        b = await _connect("R2")
        # One session's home on each thread, as pinned sessions spread.
        assert worker_pool.pool_stats() == [
            {"index": 0, "sessions": 1, "lanes": True},
            {"index": 1, "sessions": 1, "lanes": True},
        ]
        await a.disconnect()
        await b.disconnect()
        await asyncio.sleep(0.05)
    assert [s["sessions"] for s in worker_pool.pool_stats()] == [0, 0]


# -- the lane pool on its own -----------------------------------------------------


@pytest.fixture
def lane_pool():
    """``lane_pool(size)`` -> a fresh :class:`_LanePool`; closed after the test."""
    from djust import worker_pool

    made = []

    def _make(size):
        made.append(worker_pool._LanePool(size))
        return made[-1]

    yield _make
    for p in made:
        p.close()


def _lane_on(pool, home):
    """A lane with its home thread, and its executor. Keep the lane: the
    executor holds it weakly, as a bound connection's context holds it."""
    from djust import worker_pool

    lane = worker_pool._Lane(home)
    lane.pool = pool
    lane.checks_on_run = True
    return lane, worker_pool._LaneExecutor(lane, pool)


def test_lane_runs_calls_in_order_never_two_at_once(lane_pool):
    lane, executor = _lane_on(lane_pool(3), 0)
    log = []
    running = []

    def call(i):
        running.append(i)
        assert len(running) == 1, running
        time.sleep(0.01)
        log.append(i)
        running.remove(i)
        return i

    futures = [executor.submit(call, i) for i in range(20)]
    assert [f.result(timeout=10) for f in futures] == list(range(20))
    assert log == list(range(20))


def test_a_lane_runs_on_its_home_thread_while_that_thread_is_free(lane_pool):
    lane, executor = _lane_on(lane_pool(4), 2)
    names = [
        executor.submit(lambda: threading.current_thread().name).result(timeout=5)
        for _ in range(20)
    ]
    assert set(names) == {"djust-worker-2"}


def test_an_idle_thread_takes_a_lane_whose_home_is_busy(lane_pool):
    pool = lane_pool(2)
    slow_lane, slow = _lane_on(pool, 0)
    quick_lane, quick = _lane_on(pool, 0)  # the same home thread
    gate = threading.Event()
    started = threading.Event()

    def hold():
        started.set()
        return threading.current_thread().name, gate.wait(5)

    held = slow.submit(hold)
    assert started.wait(5)
    # The home thread is held: the quick lane's call runs on the other one,
    # and returns while the slow call is still running.
    assert (
        quick.submit(lambda: threading.current_thread().name).result(timeout=5) == "djust-worker-1"
    )
    assert not held.done()
    gate.set()
    assert held.result(timeout=5) == ("djust-worker-0", True)


def test_lanes_take_turns_on_a_thread(lane_pool):
    """Two lanes on a one-thread pool alternate: a lane with a long queue does not
    keep the thread until its queue is empty."""
    pool = lane_pool(1)
    order = []
    gate = threading.Event()
    one_lane, one = _lane_on(pool, 0)
    two_lane, two = _lane_on(pool, 0)
    blocker = one.submit(gate.wait, 5)  # holds the thread while both queues fill
    calls = [one.submit(order.append, "one") for _ in range(3)]
    calls += [two.submit(order.append, "two") for _ in range(3)]
    gate.set()
    blocker.result(timeout=5)
    for f in calls:
        f.result(timeout=5)
    assert order == ["two", "one", "two", "one", "two", "one"], order


def test_lane_hands_an_exception_to_its_caller_and_goes_on(lane_pool):
    lane, executor = _lane_on(lane_pool(2), 0)

    def boom():
        raise ValueError("handler failed")

    failed = executor.submit(boom)
    after = executor.submit(lambda: "next")
    with pytest.raises(ValueError, match="handler failed"):
        failed.result(timeout=5)
    assert after.result(timeout=5) == "next"


def test_lane_skips_a_call_cancelled_before_it_starts(lane_pool):
    lane, executor = _lane_on(lane_pool(2), 0)
    gate = threading.Event()
    ran = []
    first = executor.submit(gate.wait, 5)
    second = executor.submit(ran.append, "second")
    third = executor.submit(ran.append, "third")
    assert second.cancel()
    gate.set()
    first.result(timeout=5)
    third.result(timeout=5)
    assert ran == ["third"]


def test_lane_runs_the_due_connection_check_before_its_next_call(monkeypatch, lane_pool):
    import django.db

    checks = []
    monkeypatch.setattr(
        django.db, "close_old_connections", lambda: checks.append(threading.get_ident())
    )
    lane, executor = _lane_on(lane_pool(2), 1)
    lane.db_check_due = True
    ident = executor.submit(threading.get_ident).result(timeout=5)
    assert checks == [ident]
    assert lane.db_check_due is False
    executor.submit(lambda: None).result(timeout=5)
    assert len(checks) == 1  # only when due


def test_a_closed_pool_fails_queued_and_later_calls_and_stops_its_threads(lane_pool):
    pool = lane_pool(1)
    lane, executor = _lane_on(pool, 0)
    gate = threading.Event()
    running = executor.submit(gate.wait, 5)
    queued = executor.submit(lambda: "never")
    time.sleep(0.05)
    pool.close()
    gate.set()
    assert running.result(timeout=5) is True  # the running call finishes
    with pytest.raises(RuntimeError, match="shut down"):
        queued.result(timeout=5)
    with pytest.raises(RuntimeError, match="shut down"):
        executor.submit(lambda: "late").result(timeout=5)
    for thread in pool._threads:
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_mark_db_check_due_marks_a_bound_lane(monkeypatch, pool):
    from djust import worker_pool

    pool(2, False)
    monkeypatch.setattr(worker_pool, "_CHANNELS_CHECKS_PER_MESSAGE", True)
    binding = worker_pool.bind_session()
    try:
        assert isinstance(binding.slot, worker_pool._Lane)
        assert worker_pool.offload_enabled()
        assert worker_pool.mark_db_check_due() is True
        assert binding.slot.db_check_due is True
    finally:
        binding.release()


def test_the_asgiref_entry_does_not_outlive_the_lane(pool):
    from djust import worker_pool

    pool(2, False)

    def bind_and_release():
        binding = worker_pool.bind_session()
        lane = binding.slot
        assert lane in SyncToAsync.context_to_thread_executor
        binding.release()
        return weakref.ref(lane)

    lane_ref = contextvars.Context().run(bind_and_release)
    gc.collect()
    assert lane_ref() is None


def test_the_pool_threads_start_from_an_empty_context(pool):
    """Pool threads live as long as the process; one started lazily from a
    session's call would keep that session's context values alive (#3114)."""
    from djust import worker_pool

    marker = contextvars.ContextVar("lane_marker")
    pool(3, False)

    def bind():
        marker.set("a session's value")
        return worker_pool.bind_session()

    binding = contextvars.Context().run(bind)
    try:
        executor = SyncToAsync.context_to_thread_executor[binding.slot]
        assert len(executor._pool._threads) == 3  # all started at creation
        # A call submitted straight to the executor runs in its thread's own
        # context (asgiref's calls carry theirs): none holds the value.
        seen = [executor.submit(marker.get, None) for _ in range(9)]
        assert [f.result(timeout=5) for f in seen] == [None] * 9
    finally:
        binding.release()


def test_pinning_must_be_a_bool(pool, caplog):
    from djust.checks.configuration import _check_worker_threads
    from djust.worker_pool import configured_pinning

    pool(2, "no")
    with caplog.at_level("WARNING", logger="djust.worker_pool"):
        assert configured_pinning() is True
    assert "worker_pinning" in caplog.text
    errors: list = []
    _check_worker_threads(errors)
    assert [e.id for e in errors] == ["djust.C021"]
    assert "worker_pinning" in errors[0].msg

    errors.clear()
    pool(2, False)
    _check_worker_threads(errors)
    assert errors == []


def test_many_lanes_from_many_threads_keep_their_order_and_never_overlap(lane_pool):
    """A stress run on free-threaded CPython: 24 lanes on 4 threads, calls
    submitted from 6 threads at once. Each lane's calls run in submission
    order, never two at once, and every call runs exactly once."""
    pool = lane_pool(4)
    lanes = [_lane_on(pool, i % 4) for i in range(24)]
    active = [0] * len(lanes)
    seen: list = [[] for _ in lanes]
    guard = threading.Lock()

    def call(n, k):
        with guard:
            active[n] += 1
            overlapped = active[n] != 1
        seen[n].append(-1 if overlapped else k)
        time.sleep(0.0005)
        with guard:
            active[n] -= 1

    futures = []
    futures_lock = threading.Lock()

    def submitter(mine):
        for k in range(40):
            for n in mine:
                future = lanes[n][1].submit(call, n, k)
                with futures_lock:
                    futures.append(future)

    threads = [threading.Thread(target=submitter, args=(range(i, 24, 6),)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for future in futures:
        future.result(timeout=30)
    assert len(futures) == 24 * 40
    assert all(order == list(range(40)) for order in seen), seen
