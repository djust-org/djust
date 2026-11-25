"""A connection's bounded outbound buffer (#3658).

One frame of a connection is with the transport at a time, and frames reach
it in the order they were sent. A sender whose frame finds the connection idle
transmits it itself: the frame is on the wire when ``send`` returns and an
error of the transport reaches the caller, as before the buffer existed. A
frame that finds a frame in flight or a backlog is handed off: ``send``
returns at once, and the connection's drain task sends it in turn. A sender
never sends frames of others, so neither a cancelled sender nor a stream of
frames from other tasks can strand the queue or hold a sender (and the locks it
holds) in a loop.

The backlog is bounded in bytes. The frame at the head of the queue (the one
the drain task takes next) is not counted, so one frame larger than the limit
is always accepted; the bound is on what waits behind it. A frame that would
take the backlog over the limit ends the connection instead: the buffer is
abandoned and the owner's ``on_overflow`` closes the socket (WebSocket: code
1013, decision D12), so the client reconnects and resynchronises through the
ordinary recovery path. Dropping or coalescing single frames would leave the
client on a state the server never had.
"""

from __future__ import annotations

import asyncio
import collections
import logging
from typing import Awaitable, Callable, Deque, Generic, List, Optional, Tuple, TypeVar

from .config import DEFAULT_MAX_OUTBOUND_BUFFER_BYTES

logger = logging.getLogger(__name__)

Frame = TypeVar("Frame")


def outbound_buffer_limit() -> int:
    """The configured per-connection outbound limit in bytes (``0`` = none)."""
    from .config import config

    value = config.get("max_outbound_buffer_bytes", DEFAULT_MAX_OUTBOUND_BUFFER_BYTES)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_MAX_OUTBOUND_BUFFER_BYTES
    return value


class OutboundBuffer(Generic[Frame]):
    """A connection's outbound FIFO and the task that drains it.

    ``transmit(frame)`` hands one frame to the transport and returns ``False``
    once the peer is gone (later frames are dropped). ``size_of(frame)`` is
    the frame's size in bytes, computed only for a frame that has to wait.
    ``on_overflow(pending, limit)`` is called once, synchronously, when the
    backlog would pass ``limit``; it must not await the transport.
    """

    def __init__(
        self,
        transmit: Callable[[Frame], Awaitable[bool]],
        size_of: Callable[[Frame], int],
        on_overflow: Callable[[int, int], None],
        limit: int,
    ) -> None:
        self._transmit = transmit
        self._size_of = size_of
        self._on_overflow = on_overflow
        self.limit = limit
        self._queue: Deque[Tuple[Frame, int]] = collections.deque()
        #: Bytes of every frame waiting in the queue, the head included.
        self.queued_bytes = 0
        #: The largest backlog (head excluded) this connection reached.
        self.peak_bytes = 0
        #: A sender is transmitting its own frame on an idle connection.
        self._inline = False
        #: A frame is with the transport (inline or the drain task's).
        self._in_flight = False
        #: Set once the buffer is abandoned (overflow, disconnect, a dead
        #: peer, a close that gave up): nothing is queued or transmitted again.
        self.dead = False
        self._task: Optional["asyncio.Task[None]"] = None
        self._waiters: List["asyncio.Future[None]"] = []

    @property
    def queued_frames(self) -> int:
        return len(self._queue)

    @property
    def backlog_bytes(self) -> int:
        """What the limit bounds: the queued bytes behind the head frame."""
        return self.queued_bytes - self._queue[0][1] if self._queue else 0

    @property
    def busy(self) -> bool:
        return self._in_flight or bool(self._queue)

    async def send(self, frame: Frame) -> None:
        """Send *frame* now on an idle connection, else hand it off.

        On an idle connection the caller transmits its own frame: it is on the
        wire when ``send`` returns, an error reaches the caller, and the
        interleaving with other tasks is that of a plain awaited send. Frames
        that arrive meanwhile are left to the drain task, never to the caller.
        """
        if self.dead:
            return
        if self.busy:
            self._enqueue(frame)
            self._ensure_drainer()
            return
        self._inline = self._in_flight = True
        try:
            alive = await self._transmit(frame)
        finally:
            self._inline = self._in_flight = False
            # Whatever queued behind this frame goes now, even when this
            # sender raised or was cancelled.
            self._ensure_drainer()
            if not self.busy:
                self._wake()
        if not alive:
            self.abandon()

    async def flush(self, timeout: float) -> bool:
        """Wait until every queued frame was handed to the transport.

        Used before a close, so a frame sent before the close is not overtaken
        by it. Returns ``False`` when the frames could not be delivered within
        ``timeout`` seconds; they are then abandoned.
        """
        if self.dead or not self.busy:
            return True
        self._ensure_drainer()
        waiter: "asyncio.Future[None]" = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout)
        except asyncio.TimeoutError:
            self.abandon()
            return False
        return True

    def abandon(self) -> None:
        """Drop every queued frame and refuse later ones (the peer is gone).

        A frame already in flight is the transport's: that one send finishes.
        """
        self.dead = True
        self._queue.clear()
        self.queued_bytes = 0
        self._wake()

    def _ensure_drainer(self) -> None:
        if (
            self._queue
            and not self.dead
            and not self._inline
            and (self._task is None or self._task.done())
        ):
            self._task = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        try:
            while self._queue and not self.dead:
                frame, size = self._queue.popleft()
                self.queued_bytes -= size
                self._in_flight = True
                try:
                    alive = await self._transmit(frame)
                except Exception:  # noqa: BLE001 — its sender has moved on
                    logger.warning("Outbound frame not sent: the transport raised; continuing")
                    continue
                finally:
                    self._in_flight = False
                if not alive:
                    self.abandon()
        finally:
            if self._task is asyncio.current_task():
                self._task = None
            if not self.busy:
                self._wake()

    def _enqueue(self, frame: Frame) -> None:
        size = self._size_of(frame)
        if self.limit and self._queue:
            pending = self.backlog_bytes + size
            if pending > self.limit:
                self.abandon()
                self._on_overflow(pending, self.limit)
                return
        self._queue.append((frame, size))
        self.queued_bytes += size
        if self.backlog_bytes > self.peak_bytes:
            self.peak_bytes = self.backlog_bytes

    def _wake(self) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)


class ByteCountingQueue(asyncio.Queue):  # type: ignore[type-arg]
    """An ``asyncio.Queue`` that knows the bytes of the frames waiting in it.

    The SSE session's outbound queue (#3658). A frame put with
    :meth:`put_sized` counts its size until the stream takes it; one put with
    ``put_nowait`` counts zero (the stream is about to take it). The head
    item (the stream's next write) is not counted against the limit.
    """

    def __init__(self) -> None:
        super().__init__()
        self._sizes: Deque[int] = collections.deque()
        self._next_size = 0
        self.queued_bytes = 0
        #: The largest backlog (head excluded) the queue reached.
        self.peak_bytes = 0

    @property
    def backlog_bytes(self) -> int:
        """What the limit bounds: the queued bytes behind the head frame."""
        return self.queued_bytes - self._sizes[0] if self._sizes else 0

    def put_sized(self, item: object, size: int) -> None:
        self._next_size = size
        try:
            self.put_nowait(item)
        finally:
            self._next_size = 0

    def clear(self) -> None:
        """Drop every waiting item (an overflow ends the stream)."""
        while True:
            try:
                self.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _put(self, item: object) -> None:
        super()._put(item)  # type: ignore[misc]
        self._sizes.append(self._next_size)
        self.queued_bytes += self._next_size
        if self.backlog_bytes > self.peak_bytes:
            self.peak_bytes = self.backlog_bytes

    def _get(self) -> object:
        item = super()._get()  # type: ignore[misc]
        self.queued_bytes -= self._sizes.popleft()
        return item
