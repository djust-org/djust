"""Opt-in deterministic helpers for the experimental clock API."""

import asyncio
from concurrent.futures import Future
from contextlib import contextmanager
from typing import Any, Callable, Iterator


class ManualClock:
    """Injected monotonic time; advance wakes due timers without real sleeps.

    ``settle`` drains a bounded number of event-loop turns. It deliberately
    does not wait for external I/O or real worker threads.
    """

    def __init__(self) -> None:
        self.value = 0.0
        self.sleepers = []

    def now(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        future = asyncio.get_running_loop().create_future()
        entry = (self.value + seconds, future)
        self.sleepers.append(entry)
        try:
            await future
        finally:
            self.sleepers.remove(entry)

    async def settle(self, turns: int = 100) -> None:
        for _ in range(turns):
            for deadline, future in tuple(self.sleepers):
                if deadline <= self.value + 1e-9 and not future.done():
                    future.set_result(None)
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        """Advance by seconds; a jump deliberately exercises the late policy."""
        if seconds < 0:
            raise ValueError("manual clock cannot go backwards")
        self.value += seconds
        await self.settle()


class DeterministicClockExecutor:
    """Run sync calls inline, for scheduling tests only (production uses threads)."""

    def submit(self, owner: Any, due: float, call: Callable[[], Any]) -> Future:
        future = Future()
        try:
            future.set_result(call())
        except BaseException as exc:
            future.set_exception(exc)
        return future


@contextmanager
def clocks_disabled() -> Iterator[None]:
    """Make ensure a no-op for synchronous LiveViewTestClient mounts."""
    from .clocks import _disabled

    token = _disabled.set(True)
    try:
        yield
    finally:
        _disabled.reset(token)
