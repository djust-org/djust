"""Opt-in deterministic helpers for the experimental clock API."""

import asyncio
from concurrent.futures import Future
from contextlib import contextmanager
from typing import Any, Callable, Coroutine, Iterator


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

    def sleep(self, seconds: float) -> Coroutine[Any, Any, None]:
        """Sleep ``seconds`` of manual time, measured from THIS call (#3451).

        The deadline is fixed here, not when the returned coroutine first runs.
        A caller turns ``deadline - now()`` into ``seconds`` and then hands the
        coroutine to ``asyncio.create_task``, whose first step runs later in the
        same loop iteration or after it. If ``advance`` runs in between, a
        deadline computed at the first step would be ``seconds`` past a clock that
        has already moved: the sleeper would register beyond the time it was
        asked to wake at, and nothing would ever wake it.
        """
        return self._sleep_until(seconds, self.value + seconds)

    async def _sleep_until(self, seconds: float, deadline: float) -> None:
        if seconds <= 0:
            return
        if deadline <= self.value + 1e-9:
            # Time reached the deadline before this coroutine first ran, after the
            # last ``settle`` looked for sleepers. Nobody is left to wake it.
            return
        future = asyncio.get_running_loop().create_future()
        entry = (deadline, future)
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
            # Match Future's exception transfer, including cancellation.
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
