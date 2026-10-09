"""Experimental process-local shared clocks (ADR-042, phases 1–3).

No session owns a clock. Publish a doorbell and read authoritative state in the
view. Multiple processes require room routing; Redis transport is not ownership.
"""

from __future__ import annotations

import asyncio
import contextvars
import heapq
import inspect
import itertools
import logging
import math
import re
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

from asgiref.sync import async_to_sync
from django.db import close_old_connections

from ._clock_loops import is_serving_loop
from ._exposure_diagnostics import log_failure
from ._exposure_diagnostics import diagnostic_scope, _details_allowed
from .presence import PresenceManager, tenant_scoped_presence_key
from .push import apush_to_view
from .tenants.middleware import tenant_context

logger = logging.getLogger(__name__)
__all__ = ["RoomClock", "ClockTick", "Publish", "Stop", "SharedPoll"]
_disabled = contextvars.ContextVar("djust_clocks_disabled", default=False)


@dataclass(frozen=True)
class ClockTick:
    """One step's timing; sequence is local to run_id, fence is reserved."""

    key: str
    scope: str
    seq: int
    run_id: str
    dt: float
    skipped: int
    scheduled_at: float
    started_at: float
    fence: None = None


@dataclass(frozen=True)
class Stop:
    """Return from a step to end the clock and flush its trailing doorbell."""

    reason: str = "step stopped"


@dataclass(frozen=True)
class Publish:
    """Scoped push destination and an optional beat-invariant payload factory."""

    view_path: str
    handler: str
    payload: Callable[[str], dict[str, Any]] | None = None


class _Time:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _Pool:
    """One lazy process pool. Due-order dispatch, capped per clock definition.

    Pending calls do not occupy threads. Even a clock with hung steps cannot
    take more than three quarters of the pool; completions release real slots.
    """

    def __init__(self, size: int) -> None:
        self.executor = ThreadPoolExecutor(size, thread_name_prefix="djust-clock")
        self.size = size
        self.cap = max(1, size * 3 // 4)
        self.lock = threading.RLock()
        self.pending = []
        self.serial = itertools.count()
        self.active = {}
        self.total = 0

    def submit(self, owner: Any, due: float, call: Callable[[], Any]) -> Future:
        result = Future()
        with self.lock:
            heapq.heappush(self.pending, (due, next(self.serial), owner, call, result))
            self._dispatch()
        return result

    def _dispatch(self) -> None:
        blocked = []
        while self.pending and self.total < self.size:
            item = heapq.heappop(self.pending)
            _, _, owner, call, result = item
            if self.active.get(owner, 0) >= self.cap:
                blocked.append(item)
                continue
            if not result.set_running_or_notify_cancel():
                continue
            self.total += 1
            self.active[owner] = self.active.get(owner, 0) + 1
            work = self.executor.submit(call)
            work.add_done_callback(lambda f, o=owner, r=result: self._finished(o, r, f))
        for item in blocked:
            heapq.heappush(self.pending, item)

    def _finished(self, owner: Any, result: Future, work: Future) -> None:
        try:
            result.set_result(work.result())
        except BaseException as exc:
            result.set_exception(exc)
        finally:
            with self.lock:
                self.total -= 1
                self.active[owner] -= 1
                if not self.active[owner]:
                    del self.active[owner]
                self._dispatch()


_pool = None
_pool_lock = threading.Lock()
_registry_lock = threading.RLock()
_registry = {}


def _worker_pool() -> _Pool:
    global _pool
    with _pool_lock:
        if _pool is None:
            from django.conf import settings

            size = (getattr(settings, "LIVEVIEW_CONFIG", {}) or {}).get("clock_workers", 10)
            if not isinstance(size, int) or isinstance(size, bool) or size < 2:
                raise ValueError("clock_workers must be an integer >= 2")
            _pool = _Pool(size)
        return _pool


@dataclass
class _Run:
    clock: Any
    key: str
    scope: str
    tenant: Any
    tenant_id: Any
    requested: float
    started: float
    interval: float
    payload: dict
    alive: Any
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task: Any = None
    wake: Any = field(default_factory=asyncio.Event)
    paused: bool = False
    stop_reason: str | None = None
    inactive: float | None = None
    seq: int = 0
    skipped: int = 0
    overruns: int = 0
    last_duration: float = 0
    max_duration: float = 0
    errors: int = 0
    stuck: bool = False
    trailing_at: float | None = None
    last_log: float = -math.inf
    in_flight: Future | None = None
    resumed_at: float | None = None
    stopping: bool = False
    done: Future = field(default_factory=Future)

    def living(self) -> bool:
        # A closed loop cannot cancel a worker thread. Do not overlap that work
        # during a restart on another loop.
        return bool(
            (self.in_flight is not None and not self.in_flight.done())
            or (
                self.task is not None
                and not self.task.done()
                and self.task.get_loop().is_running()
                and not self.task.get_loop().is_closed()
            )
        )


class RoomClock:
    """Run step once per interval per tenant-scoped key, on a serving loop.

    Sync callbacks use a separate shared pool. ``ensure`` is for sync view
    lifecycle code; async callers use ``aensure``. Keys come from authorized
    server code, never directly from client input. Without alive, ensure must
    heartbeat more frequently than idle_stop (with margin).
    """

    def __init__(
        self,
        *,
        name: str,
        interval: float,
        step: Callable[[ClockTick], Any],
        publish: Publish | None = None,
        alive: Callable[[str], Any] | None = None,
        idle_stop: float = 5.0,
        trailing: float = 1.0,
        on_overrun: str = "skip",
        max_catch_up: int = 3,
        max_clocks: int = 256,
        max_clocks_per_tenant: int = 64,
        max_consecutive_errors: int = 10,
        max_backoff: float = 30.0,
        step_timeout: float | None = None,
        validate_key: Callable[[str], bool] | None = None,
        on_stop: Callable[[str, str], Any] | None = None,
        log_details: bool = False,
        time_source: Any = None,
        executor: Any = None,
    ) -> None:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("clock name must be a nonempty slug")
        self._validate_interval(interval)
        if on_overrun not in ("skip", "catch_up"):
            raise ValueError("on_overrun must be skip or catch_up")
        for value in (max_catch_up, max_clocks, max_clocks_per_tenant, max_consecutive_errors):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("clock limits must be positive integers")
        for value in (idle_stop, trailing, max_backoff):
            if not math.isfinite(value) or value < 0:
                raise ValueError("clock durations must be finite and nonnegative")
        if step_timeout is not None and (not math.isfinite(step_timeout) or step_timeout <= 0):
            raise ValueError("step_timeout must be finite and positive")
        self.name, self.interval, self.step = name, interval, step
        self.publish, self.alive = publish, alive
        self.idle_stop, self.trailing = idle_stop, trailing
        self.on_overrun, self.max_catch_up = on_overrun, max_catch_up
        self.max_clocks, self.max_clocks_per_tenant = max_clocks, max_clocks_per_tenant
        self.max_consecutive_errors, self.max_backoff = max_consecutive_errors, max_backoff
        self.step_timeout, self.validate_key = step_timeout, validate_key
        self.on_stop, self.log_details = on_stop, log_details
        self._time = time_source or _Time()
        self._executor = executor
        self._history = OrderedDict()
        self._limit_logged = False

    @staticmethod
    def _validate_interval(seconds: float) -> None:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0.02:
            raise ValueError("clock interval must be finite and >= 0.02 seconds")

    def scope(self, view: Any, key: str) -> str:
        """Return the only publish/registry scope for this clock and raw key."""
        if not isinstance(key, str) or not key or len(key) > 512:
            raise ValueError("clock key must be a nonempty string of at most 512 characters")
        if self.validate_key is not None and not self.validate_key(key):
            raise ValueError("clock key failed application validation")
        return tenant_scoped_presence_key(view, f"{self.name}:{quote(key, safe='')}")

    def ensure(self, view: Any, key: str, *, presence_key: str | None = None) -> bool:
        """Ensure from sync view code bridged to its registered serving loop."""
        if _disabled.get():
            return False
        return async_to_sync(self.aensure)(view, key, presence_key=presence_key)

    async def aensure(self, view: Any, key: str, *, presence_key: str | None = None) -> bool:
        """Start idempotently, or refuse a limit; return whether running."""
        if _disabled.get():
            return False
        loop = asyncio.get_running_loop()
        if not is_serving_loop(loop):
            raise RuntimeError("RoomClock.ensure requires a registered djust serving loop")
        if not hasattr(view, "_websocket_session_id"):
            raise RuntimeError("RoomClock.ensure is only valid after WebSocket authorization")
        scope = self.scope(view, key)
        if presence_key is not None and (
            not callable(getattr(view, "get_presence_key", None))
            or presence_key != view.get_presence_key()
        ):
            raise ValueError("presence binding must use the configuring view's presence key")
        tenant = getattr(view, "_tenant", None)
        tenant_id = str(tenant.id) if tenant is not None else None
        if tenant is not None and not scope.startswith(f"tenant:{tenant_id}:"):
            raise ValueError("resolved clock tenants require TenantMixin scoping")
        now = self._time.now()
        while True:
            result = self._ensure_on_loop(
                loop, view, key, scope, tenant, tenant_id, now, presence_key
            )
            if not isinstance(result, Future):
                return result
            await asyncio.shield(asyncio.wrap_future(result))
            now = self._time.now()

    def _ensure_on_loop(self, loop, view, key, scope, tenant, tenant_id, now, presence_key):
        with _registry_lock:
            existing = _registry.get((self.name, scope))
            if existing is not None and existing.living():
                if existing.clock is not self:
                    raise ValueError("a different RoomClock already owns this name and scope")
                if existing.stopping:
                    return existing.done
                existing.requested = now
                if presence_key is not None:
                    existing.inactive = None
                # An idle stop and ensure use the same lock: a join wins before
                # stop is committed; after removal it starts a fresh run.
                if existing.stop_reason == "idle":
                    existing.stop_reason = None
                return True
            runs = [r for (name, _), r in _registry.items() if name == self.name and r.living()]
            if (
                len(runs) >= self.max_clocks
                or sum(r.tenant_id == tenant_id for r in runs) >= self.max_clocks_per_tenant
            ):
                if not self._limit_logged:
                    logger.warning("Clock %s capacity reached", self.name)
                    self._limit_logged = True
                return False
            self._limit_logged = False
            alive = self.alive
            if presence_key is not None:

                def alive(_: str) -> int:
                    return PresenceManager.presence_count(presence_key)

            payload = (
                self.publish.payload(key) if self.publish and self.publish.payload else {"key": key}
            )
            if not isinstance(payload, dict):
                raise ValueError("Publish.payload must return a dict")
            from copy import deepcopy

            payload = deepcopy(payload)
            run = _Run(self, key, scope, tenant, tenant_id, now, now, self.interval, payload, alive)
            _registry[(self.name, scope)] = run
            coro = self._run(run)
            try:
                run.task = contextvars.Context().run(loop.create_task, coro)
            except BaseException:
                del _registry[(self.name, scope)]
                coro.close()
                raise
            run.task.add_done_callback(lambda task: self._task_finished(run, task))
            logger.info("Clock %s started", self.name)
        return True

    def _retire(self, run: _Run) -> None:
        with _registry_lock:
            if run.done.done():
                return
            if self._lookup(run.scope) is run:
                del _registry[(self.name, run.scope)]
            self._history[run.scope] = self._snapshot(run)
            self._history.move_to_end(run.scope)
            while len(self._history) > self.max_clocks:
                self._history.popitem(last=False)
            run.done.set_result(None)

    def _task_finished(self, run: _Run, task: asyncio.Task) -> None:
        # A task cancelled before its first turn never enters the coroutine's
        # finally block. It still has to release its registry slot and joiners.
        if not run.done.done():
            run.stop_reason = "cancelled" if task.cancelled() else "scheduler failed"
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    self._failure(run, exc, "task")
            self._retire(run)

    def _lookup(self, scope: str) -> _Run | None:
        return _registry.get((self.name, scope))

    def running(self, scope: str) -> bool:
        """Whether the scoped run has a live owner or an unfinished sync step."""
        with _registry_lock:
            run = self._lookup(scope)
            return run is not None and run.living()

    def _change(self, scope: str, **changes: Any) -> bool:
        with _registry_lock:
            run = self._lookup(scope)
            if run is None or not run.living():
                return False
            if changes.get("paused") is False and run.paused:
                run.resumed_at = self._time.now() + run.interval
            for key, value in changes.items():
                setattr(run, key, value)
            loop = run.task.get_loop()
            if loop.is_running() and not loop.is_closed():
                loop.call_soon_threadsafe(run.wake.set)
            return True

    def stop(self, scope: str, reason: str = "requested") -> bool:
        """Request graceful stop from any thread; an in-flight step finishes."""
        return self._change(scope, stop_reason=reason)

    def _presence_empty(self, scope: str) -> None:
        # Presence already counted this leave. Record its timestamp without an
        # extra count read, so graceful stops need not wait for the coarse poll.
        with _registry_lock:
            run = self._lookup(scope)
            if run is not None and run.inactive is None:
                self._change(scope, inactive=self._time.now())

    def pause(self, scope: str) -> bool:
        """Pause steps while continuing membership checks."""
        return self._change(scope, paused=True)

    def resume(self, scope: str) -> bool:
        """Resume on the schedule, without catching up paused time."""
        return self._change(scope, paused=False)

    def set_interval(self, scope: str, seconds: float) -> bool:
        """Change the interval on the next slot, keeping the run identity."""
        self._validate_interval(seconds)
        return self._change(scope, interval=seconds)

    def _snapshot(self, run: _Run) -> dict[str, Any]:
        return dict(
            steps=run.seq,
            seq=run.seq,
            run_id=run.run_id,
            skipped=run.skipped,
            overruns=run.overruns,
            last_step_duration=run.last_duration,
            max_step_duration=run.max_duration,
            run_age=self._time.now() - run.started,
            stuck=run.stuck,
            consecutive_errors=run.errors,
            stop_reason=run.stop_reason,
            paused=run.paused,
        )

    def stats(self) -> dict[str, dict[str, Any]]:
        """Snapshot live stats and bounded recent stop records, keyed by scope."""
        with _registry_lock:
            result = dict(self._history)
            result.update(
                {
                    scope: self._snapshot(r)
                    for (name, scope), r in _registry.items()
                    if name == self.name and r.clock is self
                }
            )
            return result

    def _failure(self, run: _Run, exc: BaseException, operation: str) -> None:
        now = self._time.now()
        if now - run.last_log >= 1:
            run.last_log = now
            # Do not interpolate scope, exception text, application Stop reasons
            # or traceback by default, even when Django DEBUG is true.
            if self.log_details:
                # The traceback goes through the framework's diagnostics gate,
                # like every other exception-carrying log site.
                log_failure(
                    logger,
                    exc,
                    "Clock %s %s failed (%s)",
                    self.name,
                    operation,
                    type(exc).__name__,
                    traceback=True,
                )
            else:
                logger.error("Clock %s %s failed (%s)", self.name, operation, type(exc).__name__)

    async def _timeout(self, awaitable: Any, seconds: float) -> Any:
        # Same time source as the schedule, including deterministic timeouts.
        work = asyncio.ensure_future(awaitable)
        timer = asyncio.create_task(self._time.sleep(seconds))
        try:
            await asyncio.wait((work, timer), return_when=asyncio.FIRST_COMPLETED)
            if work.done():
                return work.result()
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            raise asyncio.TimeoutError
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    async def _call(self, run: _Run, fn: Callable, arg: Any, *, timed: bool = False) -> Any:
        if inspect.iscoroutinefunction(fn):
            with tenant_context(run.tenant):
                if timed and self.step_timeout:
                    return await self._timeout(fn(arg), self.step_timeout)
                return await fn(arg)

        ctx = contextvars.copy_context()

        def call():
            with tenant_context(run.tenant):
                close_old_connections()
                try:
                    return fn(arg)
                finally:
                    close_old_connections()

        due = arg.scheduled_at if isinstance(arg, ClockTick) else self._time.now()
        future = (self._executor or _worker_pool()).submit(self.name, due, lambda: ctx.run(call))
        run.in_flight = future
        wrapped = asyncio.wrap_future(future)
        try:
            if timed and self.step_timeout:
                try:
                    return await self._timeout(asyncio.shield(wrapped), self.step_timeout)
                except asyncio.TimeoutError:
                    run.stuck = True
                    logger.warning("Clock %s sync step timeout; waiting for worker", self.name)
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # Cancellation cannot reclaim a thread. Keep registry ownership
            # until its writes finish so another ensure cannot overlap it.
            while not wrapped.done():
                try:
                    await asyncio.shield(wrapped)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break  # cancellation wins after the worker actually ended
            if wrapped.done() and not wrapped.cancelled():
                exc = wrapped.exception()
                if exc is not None:
                    self._failure(run, exc, "cancelled worker")
            raise
        finally:
            run.in_flight = None
            run.stuck = False

    async def _send(self, run: _Run) -> None:
        if self.publish is None:
            return
        try:
            await apush_to_view(
                self.publish.view_path,
                handler=self.publish.handler,
                payload=run.payload,
                scope=run.scope,
            )
        except Exception as exc:
            self._failure(run, exc, "publish")

    async def _wait(self, run: _Run, deadline: float) -> None:
        sleep = asyncio.create_task(self._time.sleep(max(0, deadline - self._time.now())))
        wake = asyncio.create_task(run.wake.wait())
        try:
            await asyncio.wait((sleep, wake), return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleep.cancel()
            wake.cancel()
            await asyncio.gather(sleep, wake, return_exceptions=True)
            run.wake.clear()

    async def _run(self, run: _Run) -> None:
        next_at = run.started + run.interval
        check_at = run.started
        backoff_until = run.started
        cancelled = False
        # An explicit engine restriction, independent of Django DEBUG.
        try:
            with diagnostic_scope(), tenant_context(run.tenant):
                if not self.log_details:
                    _details_allowed.set(False)
                while True:
                    now = self._time.now()
                    if now >= check_at:
                        alive = None
                        if run.alive is not None:
                            try:
                                alive = await self._call(run, run.alive, run.scope)
                            except Exception as exc:
                                self._failure(run, exc, "alive")
                        now = self._time.now()
                        with _registry_lock:
                            if run.alive is None:
                                idle = now - run.requested + 1e-9 >= self.idle_stop
                            elif alive:
                                run.inactive = None
                                idle = False
                            else:
                                if run.inactive is None or run.requested > run.inactive:
                                    run.inactive = max(now, run.requested)
                                idle = now - run.inactive + 1e-9 >= self.idle_stop
                            if idle and run.stop_reason is None:
                                run.stop_reason = "idle"
                        check_at = now + 1.0
                    with _registry_lock:
                        inactive_at = run.inactive if run.alive is not None else run.requested
                        if (
                            inactive_at is not None
                            and now - inactive_at + 1e-9 >= self.idle_stop
                            and run.requested <= inactive_at
                            and run.stop_reason is None
                        ):
                            run.stop_reason = "idle"
                    if run.stop_reason is not None:
                        # Commit stop under the same lock as ensure. Removal is
                        # deferred until callbacks finish; ensure then starts a
                        # new run rather than reviving one already stopping.
                        with _registry_lock:
                            if run.stop_reason == "idle" and run.requested > (
                                run.inactive if run.inactive is not None else now - self.idle_stop
                            ):
                                run.stop_reason = None
                            else:
                                run.stopping = True
                                break
                    if run.trailing_at is not None and now >= run.trailing_at:
                        await self._send(run)
                        run.trailing_at = None
                    interval = run.interval
                    if run.resumed_at is not None:
                        next_at = max(next_at, run.resumed_at)
                        run.resumed_at = None
                    if now + 1e-9 >= next_at and now >= backoff_until:
                        due = max(1, int((now - next_at + 1e-9) / interval) + 1)
                        count = 1 if self.on_overrun == "skip" else min(due, self.max_catch_up)
                        skipped = due - count
                        if skipped and not run.paused:
                            run.skipped += skipped
                            run.overruns += 1
                            if now - run.last_log >= 1:
                                logger.warning("Clock %s missed beats", self.name)
                                run.last_log = now
                        if run.paused:
                            next_at += due * interval
                        else:
                            for index in range(count):
                                dropped = skipped if index == 0 else 0
                                scheduled = next_at + (skipped + index) * interval
                                tick = ClockTick(
                                    run.key,
                                    run.scope,
                                    run.seq + 1,
                                    run.run_id,
                                    interval * (dropped + 1)
                                    if self.on_overrun == "skip"
                                    else interval,
                                    dropped,
                                    scheduled,
                                    self._time.now(),
                                )
                                start = self._time.now()
                                run.seq += 1
                                try:
                                    result = await self._call(run, self.step, tick, timed=True)
                                    run.errors = 0
                                    backoff_until = self._time.now()
                                    if isinstance(result, Stop):
                                        run.stop_reason = result.reason
                                        break
                                    if result:
                                        await self._send(run)
                                        run.trailing_at = (
                                            self._time.now() + self.trailing
                                            if self.trailing
                                            else None
                                        )
                                except Exception as exc:
                                    run.errors += 1
                                    self._failure(run, exc, "step")
                                    if run.errors >= self.max_consecutive_errors:
                                        delay = min(
                                            self.max_backoff,
                                            interval
                                            * 2
                                            ** min(
                                                30, run.errors - self.max_consecutive_errors + 1
                                            ),
                                        )
                                        backoff_until = self._time.now() + delay
                                        break
                                finally:
                                    run.last_duration = self._time.now() - start
                                    run.max_duration = max(run.max_duration, run.last_duration)
                                    if run.last_duration > interval:
                                        run.overruns += 1
                                if run.stop_reason:
                                    break
                            next_at += due * interval
                    if run.stop_reason:
                        with _registry_lock:
                            run.stopping = True
                        break
                    deadline = min(
                        max(next_at, backoff_until),
                        check_at,
                        inactive_at + self.idle_stop if inactive_at is not None else math.inf,
                        run.trailing_at if run.trailing_at is not None else math.inf,
                    )
                    await self._wait(run, deadline)
        except asyncio.CancelledError:
            cancelled = True
            run.stop_reason = "cancelled"
            raise
        except Exception as exc:
            self._failure(run, exc, "scheduler")
            run.stop_reason = "scheduler failed"
        finally:
            with _registry_lock:
                run.stopping = True
            try:
                if not cancelled:
                    if run.trailing_at is not None:
                        await self._send(run)
                    if self.on_stop is not None:
                        try:
                            from functools import partial

                            await self._call(run, partial(self.on_stop, run.scope), run.stop_reason)
                        except Exception as exc:
                            self._failure(run, exc, "on_stop")
            except asyncio.CancelledError:
                cancelled = True
                run.stop_reason = "cancelled"
                raise
            finally:
                self._retire(run)
                logger.info(
                    "Clock %s stopped (%s)", self.name, "cancelled" if cancelled else "graceful"
                )


class SharedPoll(RoomClock):
    """One shared fetch per key, with a locked latest (version, result) store.

    Include role/object visibility in the key; never share per-user responses.
    ``latest`` accepts only an engine-produced scoped key.
    """

    def __init__(self, *, fetch: Callable[[ClockTick], Any], **kwargs: Any) -> None:
        self._results = OrderedDict()
        self._result_lock = threading.Lock()
        self.fetch = fetch
        if inspect.iscoroutinefunction(fetch):

            async def step(tick):
                self._store(tick.scope, await fetch(tick))
                return True
        else:

            def step(tick):
                self._store(tick.scope, fetch(tick))
                return True

        super().__init__(step=step, **kwargs)

    def _store(self, scope: str, value: Any) -> None:
        with self._result_lock:
            version = self._results.get(scope, (0, None))[0] + 1
            self._results[scope] = (version, value)
            self._results.move_to_end(scope)
            with _registry_lock:
                active = {
                    s for (name, s), r in _registry.items() if name == self.name and r.living()
                }
            while len(self._results) > self.max_clocks:
                expired = next((s for s in self._results if s not in active), None)
                if expired is None:
                    break
                del self._results[expired]

    def latest(self, scope: str) -> tuple[int, Any] | None:
        """Return the latest version and result for an authorized scoped key."""
        with self._result_lock:
            return self._results.get(scope)
