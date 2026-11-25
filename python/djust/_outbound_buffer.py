"""A connection's bounded outbound buffer (#3658).

A socket has one frame in flight at a time. While one coroutine is handing a
frame to the ASGI server (and the server applies back-pressure because the
client reads slowly), every other sender of the same connection queues its
frame here instead of piling up as a blocked coroutine. The coroutine whose
send is in flight drains the queue after its own frame, in order, so frames
reach the wire in the order they were sent.

The queue is bounded in bytes. A frame that would take it over the limit, while
frames are already waiting, ends the connection instead: the buffer is
abandoned and the owner's ``on_overflow`` closes the socket (WebSocket: code
1013, decision D12), so the client reconnects and resynchronises through the
ordinary recovery path. Dropping or coalescing single frames would leave the
client on a state the server never had. A single frame larger than the limit
is still accepted when nothing is waiting: the bound is on the backlog, not on
how big one render may be.

With no contention a send is a plain awaited send: the caller's frame goes out
before the call returns and an error of the transport reaches the caller, as
before the buffer existed.
"""

from __future__ import annotations

import asyncio
import collections
from typing import Awaitable, Callable, Deque, Generic, List, Tuple, TypeVar

from .config import DEFAULT_MAX_OUTBOUND_BUFFER_BYTES

Frame = TypeVar("Frame")


def outbound_buffer_limit() -> int:
    """The configured per-connection outbound limit in bytes (``0`` = none)."""
    from .config import config

    value = config.get("max_outbound_buffer_bytes", DEFAULT_MAX_OUTBOUND_BUFFER_BYTES)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_MAX_OUTBOUND_BUFFER_BYTES
    return value


class OutboundBuffer(Generic[Frame]):
    """FIFO of the frames sent while another send of the connection is in flight.

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
        #: Bytes of the frames waiting in the queue (the in-flight frame is
        #: the transport's).
        self.queued_bytes = 0
        #: The largest backlog this connection reached, for tests and metrics.
        self.peak_bytes = 0
        self._sending = False
        #: Set once the buffer is abandoned (overflow, disconnect, a dead
        #: peer): nothing is queued or transmitted again.
        self.dead = False
        self._waiters: List["asyncio.Future[None]"] = []

    @property
    def queued_frames(self) -> int:
        return len(self._queue)

    async def send(self, frame: Frame) -> None:
        """Send *frame*, or queue it behind the send in flight."""
        if self.dead:
            return
        if self._sending:
            self._enqueue(frame)
            return
        self._sending = True
        try:
            if self._queue:
                # Frames a leader that raised or was cancelled left behind go
                # first: they were sent before this one.
                self._enqueue(frame)
            elif not await self._transmit(frame):
                self.abandon()
                return
            await self._drain()
        finally:
            self._sending = False
            if self.dead or not self._queue:
                self._wake()

    async def flush(self, timeout: float) -> bool:
        """Wait until every queued frame was handed to the transport.

        Used before a close, so a frame sent before the close is not overtaken
        by it. Returns ``False`` when the frames could not be delivered within
        ``timeout`` seconds; they are then abandoned.
        """
        if self.dead or not self._queue:
            return True
        if not self._sending:
            # Nobody is draining (the last leader raised or was cancelled).
            self._sending = True
            try:
                await self._drain()
            finally:
                self._sending = False
                self._wake()
            return True
        waiter: "asyncio.Future[None]" = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout)
        except asyncio.TimeoutError:
            self.abandon()
            return False
        return True

    def abandon(self) -> None:
        """Drop every queued frame and refuse later ones (the peer is gone)."""
        self.dead = True
        self._queue.clear()
        self.queued_bytes = 0
        self._wake()

    async def _drain(self) -> None:
        while self._queue and not self.dead:
            frame, size = self._queue.popleft()
            self.queued_bytes -= size
            if not await self._transmit(frame):
                self.abandon()

    def _enqueue(self, frame: Frame) -> None:
        size = self._size_of(frame)
        if self.limit and self._queue and self.queued_bytes + size > self.limit:
            pending = self.queued_bytes + size
            self.abandon()
            self._on_overflow(pending, self.limit)
            return
        self._queue.append((frame, size))
        self.queued_bytes += size
        if self.queued_bytes > self.peak_bytes:
            self.peak_bytes = self.queued_bytes

    def _wake(self) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)


class ByteCountingQueue(asyncio.Queue):  # type: ignore[type-arg]
    """An ``asyncio.Queue`` that knows the bytes of the frames waiting in it.

    The SSE session's outbound queue (#3658). A frame put with
    :meth:`put_sized` counts its size until the stream takes it; one put with
    ``put_nowait`` counts zero (the stream is about to take it, or it came
    from another event loop and was not measured).
    """

    def __init__(self) -> None:
        super().__init__()
        self._sizes: Deque[int] = collections.deque()
        self._next_size = 0
        self.queued_bytes = 0
        self.peak_bytes = 0

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
        if self.queued_bytes > self.peak_bytes:
            self.peak_bytes = self.queued_bytes

    def _get(self) -> object:
        item = super()._get()  # type: ignore[misc]
        self.queued_bytes -= self._sizes.popleft()
        return item
