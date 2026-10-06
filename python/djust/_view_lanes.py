"""One serial turn queue per view on a connection (#3252).

A connection that hosts several views (:mod:`djust._view_slots`) used to run
every inbound frame to completion, in arrival order, inside its receive loop: a
slow event handler in one view delayed the frames of every other view, whatever
locks they took. A *lane* is the fix on the receive side. Frames addressed to a
view are queued on that view's lane and run one at a time, in order, by a task
of the lane's own; the receive loop only queues and returns, so another view's
frames start while the first view's handler is still running.

What a lane keeps: the order of one view's turns (an event, then the
``request_html`` that follows it, then the next event), and the order of a view's
turns relative to the frames that tear it down (``unmount``, a navigation, a
second mount, the disconnect wait for the view's lane before they release it).
What it does not do: run two turns of one view at once, or give a view its own
database thread (the connection's synchronous work, handlers declared with plain
``def``, still shares the connection's single sync thread; handlers declared
``async def``, and every ``await`` in a turn, overlap).

A page with one view never creates a lane: its frames run inline in the receive
loop, as they always have.
"""

import asyncio
import collections
import logging
from typing import Any, Awaitable, Callable, Deque, Optional, Tuple

logger = logging.getLogger(__name__)

#: Turns one view may have waiting behind the one that is running. A connection's
#: message rate limit (``LIVEVIEW_CONFIG["rate_limit"]``) already bounds how fast
#: a client can queue them; this bounds what a slow view can pile up, so a stuck
#: handler cannot grow memory without limit. A frame past it is refused.
MAX_QUEUED_TURNS = 64

#: What a queued turn is charged against a lane's byte budget besides its payload:
#: the frame object and its queue entry. It bounds the number of tiny frames (a
#: ``complete`` or ``cancel`` carries no data) the same way the payload bounds the
#: large ones.
TURN_OVERHEAD_BYTES = 256

#: A queued turn: the coroutine function that runs it, its argument, and the
#: bytes it is charged.
_Turn = Tuple[Callable[[Any], Awaitable[None]], Any, int]


class ViewLane:
    """A FIFO of turns for one view, run one at a time by a task that exists
    only while turns are queued (an idle lane holds no task)."""

    __slots__ = ("_turns", "_task", "_waiting", "_bytes")

    def __init__(self) -> None:
        self._turns: Deque[_Turn] = collections.deque()
        self._task: Optional["asyncio.Future[None]"] = None
        self._waiting = 0
        self._bytes = 0

    def queued_bytes(self) -> int:
        """What the queued turns are charged against the byte budget."""
        return self._bytes

    def queued(self) -> int:
        """How many turns are waiting behind the running one."""
        return len(self._turns)

    def waiting(self) -> int:
        """How many tasks are blocked in :meth:`quiesce` on this lane."""
        return self._waiting

    def busy(self) -> bool:
        """Whether a turn is queued or running."""
        return bool(self._turns) or (self._task is not None and not self._task.done())

    def submit(
        self,
        run: Callable[[Any], Awaitable[None]],
        item: Any,
        *,
        size: Optional[int] = None,
        budget: Optional[int] = None,
    ) -> bool:
        """Queue a turn; it runs after the ones already queued.

        False, with nothing queued, when it does not fit. An ordinary turn
        (``size`` None) is refused when ``MAX_QUEUED_TURNS`` are already waiting.
        A turn with a ``size`` (the frames of an upload, which must follow their
        ``upload_register`` and so cannot simply be refused) is charged
        ``size + TURN_OVERHEAD_BYTES`` against ``budget`` instead and refused only
        when that would exceed it.
        """
        if size is None:
            if len(self._turns) >= MAX_QUEUED_TURNS:
                return False
            charge = 0
        else:
            charge = size + TURN_OVERHEAD_BYTES
            if budget is not None and self._bytes + charge > budget:
                return False
        self._turns.append((run, item, charge))
        self._bytes += charge
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._work())
        return True

    async def _work(self) -> None:
        while self._turns:
            run, item, charge = self._turns.popleft()
            self._bytes -= charge
            try:
                await run(item)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a turn reports its own failures; the lane goes on
                logger.error("A view's queued turn raised")

    async def quiesce(self) -> None:
        """Wait until every queued turn has run and none is running.

        Returns at once from inside the lane's own task: a turn that tears its
        own view down (a refusal that ends it) must not wait for itself. Does
        not cancel the lane if the waiter is cancelled.
        """
        task = self._task
        if task is None or task.done() or task is asyncio.current_task():
            return
        self._waiting += 1
        try:
            await asyncio.wait({task})
        finally:
            self._waiting -= 1

    def drop_queued(self) -> None:
        """Forget the turns that have not started (the connection is gone)."""
        self._turns.clear()
        self._bytes = 0

    def cancel(self) -> None:
        """Stop now: forget what is queued and cancel the running turn."""
        self._turns.clear()
        self._bytes = 0
        task = self._task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
