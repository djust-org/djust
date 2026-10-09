"""ADR-042 process-local scheduling, lifecycle, safety and phase-3 contracts."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from djust._clock_loops import register_serving_loop
from djust.clocks import RoomClock, Publish, Stop, SharedPoll, _registry, _registry_lock
from djust.push import origin_channel
from djust.tenants.mixin import TenantMixin
from djust.tenants.middleware import get_current_tenant
from djust.tenants.resolvers import TenantInfo
from djust.testing import ManualClock, DeterministicClockExecutor, clocks_disabled


class TenantView(TenantMixin):
    def __init__(self, tenant):
        self._tenant = TenantInfo(tenant)
        self._websocket_session_id = "test"


def view():
    return SimpleNamespace(_websocket_session_id="test")


def clock(time, step, **kwargs):
    return RoomClock(
        name="test",
        interval=0.1,
        step=step,
        time_source=time,
        executor=DeterministicClockExecutor(),
        idle_stop=100,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def empty_registry():
    assert not _registry
    yield
    assert not _registry


async def cleanup(c, time):
    for scope in c.stats():
        c.stop(scope)
    await time.settle()


@pytest.mark.asyncio
@pytest.mark.parametrize("members", [1, 4, 50])
async def test_one_step_per_slot_independent_of_members(members):
    register_serving_loop()
    time, ticks = ManualClock(), []
    c = clock(time, ticks.append)
    try:
        for _ in range(members):
            assert await c.aensure(view(), "room")
        await time.settle()
        for _ in range(50):
            await time.advance(0.1)
        assert len(ticks) == 50
        assert [t.seq for t in ticks] == list(range(1, 51))
        assert [t.scheduled_at for t in ticks] == pytest.approx([0.1 * i for i in range(1, 51)])
        assert {t.dt for t in ticks} == {0.1}
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,count,skipped,dt", [("skip", 1, 4, 0.5), ("catch_up", 3, 2, 0.1)])
async def test_late_beats_are_bounded_and_resume_grid(policy, count, skipped, dt):
    register_serving_loop()
    time, ticks = ManualClock(), []
    c = clock(time, ticks.append, on_overrun=policy)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        await time.advance(0.55)
        assert len(ticks) == count
        assert sum(t.skipped for t in ticks) == skipped
        assert all(t.dt == pytest.approx(dt) for t in ticks)
        assert c.stats()["test:room"]["skipped"] == skipped
        await time.advance(0.05)
        assert ticks[-1].scheduled_at == pytest.approx(0.6)
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_fixed_rate_not_sleep_after_work():
    register_serving_loop()
    time, ticks = ManualClock(), []

    def step(tick):
        ticks.append(tick)
        time.value += 0.02

    c = clock(time, step)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        for i in range(1, 601):
            await time.advance(i * 0.1 - time.now())
        assert len(ticks) == 600
        assert ticks[-1].scheduled_at == pytest.approx(60)
        assert c.stats()["test:room"]["max_step_duration"] == pytest.approx(0.02)
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["alive", "unreadable", "heartbeat"])
async def test_idle_stop_restart_and_bounded_history(source):
    register_serving_loop()
    time = ManualClock()

    def unreadable(_):
        raise ValueError("secret")

    alive = (
        None if source == "heartbeat" else unreadable if source == "unreadable" else lambda _: False
    )
    c = RoomClock(
        name="test",
        interval=0.1,
        step=lambda _: None,
        idle_stop=0.2,
        alive=alive,
        time_source=time,
        executor=DeterministicClockExecutor(),
    )
    await c.aensure(view(), "room")
    await time.settle()
    run_id = c.stats()["test:room"]["run_id"]
    await time.advance(0.2)
    assert not c.running("test:room")
    assert c.stats()["test:room"]["stop_reason"] == "idle"
    await c.aensure(view(), "room")
    await time.settle()
    assert c.stats()["test:room"]["run_id"] != run_id
    await cleanup(c, time)


@pytest.mark.asyncio
async def test_ensure_during_alive_read_keeps_one_clock():
    register_serving_loop()
    time, entered, release = ManualClock(), asyncio.Event(), asyncio.Event()

    async def alive(_):
        entered.set()
        await release.wait()
        return False

    c = clock(time, lambda _: None, alive=alive)
    try:
        await c.aensure(view(), "room")
        await entered.wait()
        time.value = 1
        await c.aensure(view(), "room")
        release.set()
        await time.settle()
        assert c.running("test:room")
        assert len(_registry) == 1
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_errors_are_value_free_backoff_and_success_resets(caplog):
    register_serving_loop()
    time, ticks = ManualClock(), []
    fail = True

    def step(t):
        ticks.append(t)
        if fail:
            raise ValueError("private room state")

    c = clock(time, step, max_consecutive_errors=2)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        await time.advance(0.1)
        await time.advance(0.1)
        assert c.stats()["test:room"]["consecutive_errors"] == 2
        await time.advance(0.1)
        assert len(ticks) == 2
        fail = False
        await time.advance(0.1)
        assert len(ticks) == 3
        assert c.stats()["test:room"]["consecutive_errors"] == 0
        assert "private room state" not in caplog.text
        assert any("ValueError" in r.message for r in caplog.records)
        assert all(r.exc_info is None for r in caplog.records)
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_details_opt_in(caplog):
    register_serving_loop()
    time = ManualClock()

    def step(_):
        raise ValueError("private room state")

    c = clock(time, step, log_details=True)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        await time.advance(0.1)
        assert "private room state" in caplog.text
        assert any(r.exc_info for r in caplog.records)
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_caps_are_per_tenant_and_process(caplog):
    register_serving_loop()
    time = ManualClock()
    c = clock(time, lambda _: None, max_clocks=3, max_clocks_per_tenant=1)
    try:
        assert await c.aensure(TenantView("a"), "same")
        assert not await c.aensure(TenantView("a"), "other")
        assert not await c.aensure(TenantView("a"), "other")
        assert await c.aensure(TenantView("b"), "same")
        assert await c.aensure(TenantView("c"), "same")
        assert not await c.aensure(TenantView("d"), "same")
        assert len(_registry) == 3
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_kind", ["ignores", "rejects"])
async def test_clean_context_tenant_and_task_factory(factory_kind):
    register_serving_loop()
    time, seen = ManualClock(), []

    def step(t):
        seen.append((t.scope, get_current_tenant().id, origin_channel.get()))

    def alive(scope):
        assert scope.startswith("tenant:" + get_current_tenant().id + ":")
        assert origin_channel.get() is None
        return True

    loop = asyncio.get_running_loop()
    old = loop.get_task_factory()
    if factory_kind == "ignores":

        def factory(loop, coro, **kwargs):
            return asyncio.Task(coro, loop=loop)
    else:

        def factory(loop, coro):
            return asyncio.Task(coro, loop=loop)

    c = clock(time, step, alive=alive)
    token = origin_channel.set("starter")
    loop.set_task_factory(factory)
    try:
        await c.aensure(TenantView("a"), "same")
        await c.aensure(TenantView("b"), "same")
        await time.settle()
        await time.advance(0.1)
        assert set(seen) == {("tenant:a:test:same", "a", None), ("tenant:b:test:same", "b", None)}
    finally:
        await cleanup(c, time)
        origin_channel.reset(token)
        loop.set_task_factory(old)


@pytest.mark.asyncio
async def test_trailing_stop_flush_and_cancellation():
    register_serving_loop()
    time, sent = ManualClock(), []
    result = True

    def step(_):
        return result

    c = clock(time, step, publish=Publish("app.views.Room", "handle_refresh"), trailing=0.3)

    async def send(*args, **kwargs):
        sent.append(kwargs)

    with patch("djust.clocks.apush_to_view", send):
        try:
            await c.aensure(view(), "room")
            await time.settle()
            await time.advance(0.1)
            result = False
            await time.advance(0.3)
            assert len(sent) == 2
            await time.advance(0.2)
            assert len(sent) == 2
            result = True
            await time.advance(0.1)
            result = Stop("finished")
            await time.advance(0.1)
            assert len(sent) == 4
            assert all(event == sent[0] for event in sent)
            assert c.stats()["test:room"]["stop_reason"] == "finished"
            result = True
            await c.aensure(view(), "room")
            await time.settle()
            await time.advance(0.1)
            before = len(sent)
            task = _registry[("test", "test:room")].task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert len(sent) == before
        finally:
            await cleanup(c, time)


@pytest.mark.asyncio
async def test_pause_interval_and_shared_poll():
    register_serving_loop()
    time, calls = ManualClock(), []

    def fetch(t):
        calls.append(t)
        return {"scope": t.scope, "tenant": get_current_tenant().id}

    c = SharedPoll(
        name="poll",
        interval=0.1,
        fetch=fetch,
        idle_stop=100,
        time_source=time,
        executor=DeterministicClockExecutor(),
    )
    try:
        for _ in range(4):
            await c.aensure(TenantView("a"), "same")
        await c.aensure(TenantView("b"), "same")
        await time.settle()
        await time.advance(0.1)
        a, b = c.scope(TenantView("a"), "same"), c.scope(TenantView("b"), "same")
        assert len(calls) == 2
        assert c.latest(a) == (1, {"scope": a, "tenant": "a"})
        assert c.latest(b) == (1, {"scope": b, "tenant": "b"})
        assert c.latest("same") is None
        run = c.stats()[a]["run_id"]
        c.pause(a)
        await time.settle()
        await time.advance(0.1)
        assert c.latest(a)[0] == 1
        c.resume(a)
        c.set_interval(a, 0.2)
        await time.settle()
        await time.advance(0.1)
        assert c.latest(a)[0] == 2
        await time.advance(0.1)
        assert c.latest(a)[0] == 2
        await time.advance(0.1)
        assert c.latest(a)[0] == 3
        assert c.stats()[a]["run_id"] == run
    finally:
        await cleanup(c, time)


def test_plain_thread_refused_and_noop_helper():
    c = RoomClock(name="plain", interval=0.1, step=lambda _: None)
    with pytest.raises(RuntimeError, match="serving loop"):
        c.ensure(view(), "room")
    with clocks_disabled():
        assert c.ensure(view(), "room") is False
    assert not _registry


@pytest.mark.parametrize("bad", ["", None, True, 1, "x" * 513])
def test_key_validation(bad):
    c = RoomClock(name="validate", interval=0.1, step=lambda _: None)
    with pytest.raises(ValueError):
        c.scope(view(), bad)


def test_application_validation_and_interval_floor():
    c = RoomClock(
        name="validate", interval=0.1, step=lambda _: None, validate_key=lambda k: k == "ok"
    )
    with pytest.raises(ValueError):
        c.scope(view(), "bad")
    for interval in (0.01, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            c.set_interval("scope", interval)


@pytest.mark.asyncio
async def test_real_sync_worker_single_flight_and_db_cleanup():
    register_serving_loop()
    time = ManualClock()
    entered, release = threading.Event(), threading.Event()
    thread_ids = []

    def step(_):
        thread_ids.append(threading.get_ident())
        entered.set()
        assert release.wait(5)

    c = RoomClock(name="thread", interval=0.1, step=step, idle_stop=100, time_source=time)
    with patch("djust.clocks.close_old_connections") as close:
        try:
            await c.aensure(view(), "room")
            await time.settle()
            await time.advance(0.1)
            assert await asyncio.to_thread(entered.wait, 5)
            await time.advance(1)
            assert len(thread_ids) == 1
            assert thread_ids[0] != threading.get_ident()
            c.stop("thread:room")
            release.set()
            task = _registry[("thread", "thread:room")].task
            await asyncio.wait_for(task, 5)
            assert close.call_count == 2
        finally:
            release.set()
            await cleanup(c, time)


@pytest.mark.asyncio
async def test_sync_timeout_marks_stuck_until_worker_returns_without_overlap():
    register_serving_loop()
    time = ManualClock()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def step(t):
        calls.append(t)
        entered.set()
        assert release.wait(5)

    c = RoomClock(
        name="stuck", interval=0.1, step=step, step_timeout=0.05, idle_stop=100, time_source=time
    )
    try:
        await c.aensure(view(), "room")
        await time.settle()
        await time.advance(0.1)
        assert await asyncio.to_thread(entered.wait, 5)
        await time.advance(0.05)
        assert c.stats()["stuck:room"]["stuck"]
        await time.advance(1)
        assert len(calls) == 1
        c.stop("stuck:room")
        task = _registry[("stuck", "stuck:room")].task
        release.set()
        await asyncio.wait_for(task, 5)
        assert not c.running("stuck:room")
        assert not c.stats()["stuck:room"]["stuck"]
    finally:
        release.set()
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_async_timeout_is_cancelled_and_clock_continues():
    register_serving_loop()
    time, entered, cancelled = ManualClock(), asyncio.Event(), asyncio.Event()
    calls = []

    async def step(t):
        calls.append(t)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    c = clock(time, step, step_timeout=0.05)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        await time.advance(0.1)
        assert entered.is_set()
        await time.advance(0.05)
        assert cancelled.is_set()
        assert c.stats()["test:room"]["consecutive_errors"] == 1
        await time.advance(0.05)
        assert len(calls) == 2
    finally:
        c.stop("test:room")
        await time.advance(0.05)
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_sync_ensure_racing_from_two_worker_threads():
    from asgiref.sync import sync_to_async

    register_serving_loop()
    time = ManualClock()
    c = clock(time, lambda _: None)
    try:
        results = await asyncio.gather(
            *[sync_to_async(c.ensure, thread_sensitive=False)(view(), "room") for _ in range(2)]
        )
        assert results == [True, True]
        assert len(_registry) == 1
    finally:
        await cleanup(c, time)


def test_two_real_loops_racing_start_one_task_and_foreign_stop():
    c = RoomClock(name="loops", interval=0.1, step=lambda _: None, alive=lambda _: True)
    barrier = threading.Barrier(3)
    release = threading.Event()
    errors, owners = [], []

    def worker():
        async def run():
            register_serving_loop()
            await c.aensure(view(), "room")
            with _registry_lock:
                task = _registry[("loops", "loops:room")].task
            await asyncio.to_thread(barrier.wait, 5)
            await asyncio.to_thread(release.wait, 5)
            if task.get_loop() is asyncio.get_running_loop():
                owners.append(True)
                await asyncio.wait_for(task, 5)

        try:
            asyncio.run(run())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(5)
        assert len(_registry) == 1
        assert c.running("loops:room")
        c.stop("loops:room")  # ordinary foreign thread, no cached loop
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert len(owners) == 1


@pytest.mark.asyncio
async def test_coarse_alive_reads_pause_keeps_liveness_and_idle_flush():
    register_serving_loop()
    time, reads, sent = ManualClock(), [], []
    alive = True

    def check(scope):
        reads.append(time.now())
        return alive

    c = RoomClock(
        name="coarse",
        interval=0.02,
        step=lambda _: True,
        alive=check,
        idle_stop=0.2,
        time_source=time,
        executor=DeterministicClockExecutor(),
        publish=Publish("app.views.Room", "handle_refresh"),
    )

    async def send(*args, **kwargs):
        sent.append(kwargs)

    with patch("djust.clocks.apush_to_view", send):
        try:
            await c.aensure(view(), "room")
            await time.settle()
            for _ in range(50):
                await time.advance(0.02)
            assert reads == pytest.approx([0, 1])
            c.pause("coarse:room")
            await time.settle()
            before = c.stats()["coarse:room"]["steps"]
            alive = False
            await time.advance(1)
            assert c.stats()["coarse:room"]["steps"] == before
            before_sent = len(sent)
            await time.advance(0.2)
            assert not c.running("coarse:room")
            # A trailing doorbell may have become due before the idle stop;
            # it is never flushed twice.
            assert len(sent) <= before_sent + 1
        finally:
            await cleanup(c, time)


@pytest.mark.asyncio
async def test_thousand_create_stop_cycles_leave_no_registry_or_tasks():
    register_serving_loop()
    time = ManualClock()
    c = clock(time, lambda _: Stop("done"), max_clocks=4)
    baseline = len(asyncio.all_tasks())
    for i in range(1000):
        await c.aensure(view(), str(i))
        await time.settle(10)
        await time.advance(0.1)
        assert not _registry
    assert len(c.stats()) == 4
    assert len(asyncio.all_tasks()) == baseline


@pytest.mark.asyncio
async def test_join_during_stop_finalization_waits_then_restarts():
    register_serving_loop()
    time = ManualClock()
    stopping, release = asyncio.Event(), asyncio.Event()

    async def on_stop(scope, reason):
        stopping.set()
        await release.wait()

    c = clock(time, lambda _: Stop("end"), on_stop=on_stop)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        old_id = c.stats()["test:room"]["run_id"]
        await time.advance(0.1)
        await stopping.wait()
        join = asyncio.create_task(c.aensure(view(), "room"))
        await time.settle()
        assert not join.done()
        release.set()
        assert await join
        await time.settle()
        assert c.running("test:room")
        assert c.stats()["test:room"]["run_id"] != old_id
    finally:
        release.set()
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_resume_drops_paused_backlog_even_before_scheduler_observes_pause():
    register_serving_loop()
    time, ticks = ManualClock(), []
    c = clock(time, ticks.append, on_overrun="catch_up")
    try:
        await c.aensure(view(), "room")
        await time.settle()
        c.pause("test:room")
        time.value += 10  # pause and resume without giving the scheduler a turn
        c.resume("test:room")
        await time.settle()
        assert ticks == []
        await time.advance(0.1)
        assert len(ticks) == 1
        assert ticks[0].skipped == 0
        assert ticks[0].dt == 0.1
    finally:
        await cleanup(c, time)


def test_shared_pool_limits_one_namespace_and_orders_eligible_due_calls():
    from djust.clocks import _Pool

    pool = _Pool(4)
    release = threading.Event()
    entered = threading.Barrier(4)
    order = []

    def hold():
        entered.wait(5)
        assert release.wait(5)

    first = [pool.submit("a", 0, hold) for _ in range(3)]
    try:
        entered.wait(5)
        blocked = pool.submit("a", 1, lambda: order.append("a"))
        other = pool.submit("b", 2, lambda: order.append("b"))
        other.result(5)
        assert order == ["b"]
        assert not blocked.done()
        release.set()
        for future in first:
            future.result(5)
        blocked.result(5)
        assert order == ["b", "a"]
    finally:
        release.set()
        pool.executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_uncancellable_worker_ownership():
    register_serving_loop()
    time = ManualClock()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def step(t):
        calls.append(t)
        entered.set()
        assert release.wait(5)

    c = RoomClock(name="cancel-worker", interval=0.1, step=step, time_source=time, idle_stop=100)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        await time.advance(0.1)
        assert await asyncio.to_thread(entered.wait, 5)
        task = _registry[("cancel-worker", "cancel-worker:room")].task
        task.cancel()
        await time.settle()
        task.cancel()
        await time.settle()
        assert c.running("cancel-worker:room")
        assert await c.aensure(view(), "room")
        assert len(calls) == 1
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not c.running("cancel-worker:room")
    finally:
        release.set()
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_poll_retains_paused_active_result_when_idle_entries_are_evicted():
    register_serving_loop()
    time = ManualClock()
    c = SharedPoll(
        name="retain",
        interval=0.1,
        fetch=lambda t: t.key,
        max_clocks=2,
        time_source=time,
        executor=DeterministicClockExecutor(),
        idle_stop=100,
    )
    try:
        await c.aensure(view(), "active")
        await time.settle()
        await time.advance(0.1)
        c.pause("retain:active")
        for key in ("first", "second", "third"):
            await c.aensure(view(), key)
            await time.settle()
            await time.advance(0.1)
            c.stop("retain:" + key)
            await time.settle()
        assert c.latest("retain:active") == (1, "active")
        assert c.latest("retain:first") is None
        assert len(c._results) == 2
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_cancellation_during_stop_callback_releases_waiting_join():
    register_serving_loop()
    time, entered = ManualClock(), asyncio.Event()

    async def on_stop(scope, reason):
        entered.set()
        await asyncio.Event().wait()

    c = clock(time, lambda _: Stop("end"), on_stop=on_stop)
    await c.aensure(view(), "room")
    await time.settle()
    await time.advance(0.1)
    await entered.wait()
    task = _registry[("test", "test:room")].task
    join = asyncio.create_task(c.aensure(view(), "room"))
    await time.settle()
    assert not join.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert await join
    await time.settle()
    # Cancel the fresh run rather than entering another stop callback.
    restarted = _registry[("test", "test:room")].task
    restarted.cancel()
    await asyncio.gather(restarted, return_exceptions=True)
    assert not _registry


@pytest.mark.asyncio
async def test_stopped_owner_loop_is_replaced_and_old_cleanup_cannot_remove_new_run():
    loop = asyncio.new_event_loop()
    c = RoomClock(name="deadloop", interval=10, step=lambda _: None, alive=lambda _: True)

    async def start():
        register_serving_loop()
        await c.aensure(view(), "room")

    await asyncio.to_thread(loop.run_until_complete, start())
    old = _registry[("deadloop", "deadloop:room")]
    old_id = old.run_id
    if old.in_flight is not None:
        old.in_flight.result(5)
    assert not loop.is_running()

    async def restart():
        register_serving_loop()
        await c.aensure(view(), "room")
        new = _registry[("deadloop", "deadloop:room")]
        assert new.run_id != old_id

        async def clean_old():
            tasks = asyncio.all_tasks(loop) - {asyncio.current_task()}
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        await asyncio.to_thread(loop.run_until_complete, clean_old())
        assert _registry[("deadloop", "deadloop:room")] is new
        c.stop("deadloop:room")
        await new.task

    try:
        await restart()
    finally:
        loop.close()


@pytest.mark.django_db
def test_liveview_testclient_can_mount_with_clocks_disabled_and_http_is_noop():
    from djust import LiveView, PresenceMixin
    from djust.testing import LiveViewTestClient

    c = RoomClock(name="testclient", interval=0.1, step=lambda _: None)

    class TestView(PresenceMixin, LiveView):
        template = "<div dj-root>{{ value }}</div>"
        room_clock = c
        presence_key = "testclient-room"

        def mount(self, request, **kwargs):
            self.value = 1
            self.track_presence({})

    with clocks_disabled():
        client = LiveViewTestClient(TestView).mount()
        assert client.view_instance._presence_tracked
        assert "_room_clock_scope" not in client.view_instance._user_private_keys
        assert "room_clock" not in client.view_instance.get_state()
        assert not _registry
        client.view_instance.untrack_presence()
    with patch.object(c, "ensure", side_effect=AssertionError("HTTP starts no clock")):
        client = LiveViewTestClient(TestView).mount(via_websocket=False)
        assert not client.view_instance._presence_tracked


@pytest.mark.asyncio
async def test_colon_bearing_tenant_ids_cannot_alias_clock_or_publish_scope():
    register_serving_loop()
    time, seen, sent = ManualClock(), [], []
    c = clock(
        time,
        lambda t: seen.append((t.scope, get_current_tenant().id)) or True,
        publish=Publish("app.views.Room", "handle_refresh"),
    )

    async def send(*args, **kwargs):
        sent.append(kwargs["scope"])

    with patch("djust.clocks.apush_to_view", send):
        try:
            a, b = TenantView("a"), TenantView("a:test")
            assert c.scope(a, "test:same") != c.scope(b, "same")
            await c.aensure(a, "test:same")
            await c.aensure(b, "same")
            await time.settle()
            await time.advance(0.1)
            assert len(_registry) == 2
            assert len(set(sent)) == 2
            assert {tenant for _, tenant in seen} == {"a", "a:test"}
        finally:
            await cleanup(c, time)


@pytest.mark.asyncio
async def test_cancellation_before_first_task_turn_releases_registry_slot():
    register_serving_loop()
    time = ManualClock()
    c = clock(time, lambda _: None)
    await c.aensure(view(), "room")
    task = _registry[("test", "test:room")].task
    task.cancel()  # no settle: the coroutine has never entered its finally
    await asyncio.gather(task, return_exceptions=True)
    assert not _registry
    assert c.stats()["test:room"]["stop_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_worker_failure_racing_cancellation_is_consumed_and_value_free(caplog):
    from concurrent.futures import Future

    register_serving_loop()
    time = ManualClock()
    future = Future()

    class Executor:
        def submit(self, owner, due, call):
            return future

    c = RoomClock(
        name="cancel-error",
        interval=0.1,
        step=lambda _: None,
        executor=Executor(),
        time_source=time,
        idle_stop=100,
    )
    await c.aensure(view(), "room")
    await time.settle()
    await time.advance(0.1)
    task = _registry[("cancel-error", "cancel-error:room")].task
    future.set_exception(ValueError("private worker state"))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not _registry
    assert "private worker state" not in caplog.text
    assert any("ValueError" in record.message for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_untenanted_rooms_use_global_cap():
    register_serving_loop()
    time = ManualClock()
    c = clock(time, lambda _: None)
    try:
        for number in range(70):
            assert await c.aensure(view(), str(number))
        assert len(_registry) == 70
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["requested", "idle", ""])
async def test_stop_then_immediate_ensure_restarts(reason):
    register_serving_loop()
    time = ManualClock()
    c = clock(time, lambda _: None)
    try:
        await c.aensure(view(), "room")
        old = _registry[("test", "test:room")]
        c.stop("test:room", reason)
        assert await c.aensure(view(), "room")
        await time.settle()
        assert c.running("test:room")
        assert _registry[("test", "test:room")].run_id != old.run_id
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_capacity_warning_is_time_limited_across_churn(caplog):
    register_serving_loop()
    time = ManualClock()
    c = clock(time, lambda _: None, max_clocks=1)
    try:
        for _ in range(4):
            assert await c.aensure(view(), "room")
            assert not await c.aensure(view(), "excess")
            await cleanup(c, time)
        assert sum("capacity reached" in r.message for r in caplog.records) == 1
        await time.advance(1)
        assert await c.aensure(view(), "room")
        assert not await c.aensure(view(), "excess")
        assert sum("capacity reached" in r.message for r in caplog.records) == 2
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["skip", "catch_up"])
async def test_breaker_suppression_is_not_overload_or_simulation_time(policy, caplog):
    register_serving_loop()
    time, ticks = ManualClock(), []

    def step(tick):
        ticks.append(tick)
        if len(ticks) == 1:
            raise ValueError("fail")

    c = clock(time, step, on_overrun=policy, max_consecutive_errors=1)
    try:
        await c.aensure(view(), "room")
        await time.settle()
        await time.advance(0.1)
        await time.advance(0.2)
        assert len(ticks) == 2
        assert ticks[-1].dt == pytest.approx(0.1)
        assert ticks[-1].skipped == 0
        assert c.stats()["test:room"]["overruns"] == 0
        assert "missed beats" not in caplog.text
    finally:
        await cleanup(c, time)


@pytest.mark.asyncio
async def test_payload_factory_is_single_flight_off_loop_and_registry_lock():
    register_serving_loop()
    entered, release = threading.Event(), threading.Event()
    calls = []
    loop_thread = threading.get_ident()

    def payload(key):
        assert threading.get_ident() != loop_thread
        assert not _registry_lock._is_owned()
        calls.append(key)
        entered.set()
        assert release.wait(5)
        return {"key": key}

    c = RoomClock(
        name="payload",
        interval=10,
        step=lambda _: None,
        publish=Publish("app.Room", "handle_refresh", payload),
    )
    tasks = [asyncio.create_task(c.aensure(view(), "room")) for _ in range(2)]
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        # Both the serving loop and a foreign registry reader remain usable.
        assert await asyncio.to_thread(c.running, "payload:room")
        assert calls == ["room"]
        release.set()
        assert await asyncio.gather(*tasks) == [True, True]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        c.stop("payload:room")
        if _registry:
            await _registry[("payload", "payload:room")].task


@pytest.mark.asyncio
async def test_step_stop_commits_before_foreign_loop_ensure():
    register_serving_loop()
    time = ManualClock()
    checked = threading.Event()
    results = []
    c = clock(time, lambda _: Stop("end"))
    original_call = c._call

    async def call(run, fn, arg, **kwargs):
        if fn is c.step:
            # Return Stop without yielding, then the engine must commit it
            # before _send yields to a foreign serving loop.
            return Stop("end")
        return await original_call(run, fn, arg, **kwargs)

    async def send(run):
        def foreign():
            async def ensure():
                register_serving_loop()
                task = asyncio.create_task(c.aensure(view(), "room"))
                await asyncio.sleep(0)
                results.append(task.done())
                checked.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            asyncio.run(ensure())

        await asyncio.to_thread(foreign)

    # Force a trailing send exactly in the window after the Stop result.
    c._call = call
    c._send = send
    try:
        await c.aensure(view(), "room")
        await time.settle()
        _registry[("test", "test:room")].trailing_at = 10
        await time.advance(0.1)
        assert await asyncio.to_thread(checked.wait, 5)
        assert results == [False]  # joining waits for retirement, never reports success
        await time.settle()
    finally:
        await cleanup(c, time)
