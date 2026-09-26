"""Channel layers shipped with djust.

``djust.layers.InMemoryChannelLayer`` is Channels' in-process
``InMemoryChannelLayer`` with one change: its expiry sweep runs at most once
per ``clean_interval`` seconds instead of on every message (#3074).

Why: the stock layer calls ``_clean_expired()`` at the start of EVERY
``receive()`` and ``group_send()``. The sweep walks every channel queue and
every group membership, so each message costs O(channels) and a broadcast
round across N sessions costs O(N²), all on the asyncio event loop. The
#3074 snake profile at 224 sessions spent 11.9 % of the event-loop thread in
it. Messages expire after 60 s by default and group memberships after a day,
so sweeping more than about once a second buys nothing.

Use it where the in-memory layer is the right layer at all: ONE process,
which with free-threaded Python and ``LIVEVIEW_CONFIG["worker_threads"]`` can
be a process that uses several cores::

    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "djust.layers.InMemoryChannelLayer",
            # "CONFIG": {"clean_interval": 1.0},  # seconds; 0 = sweep on every message
        },
    }

Several processes need a shared layer (``channels_redis``), as before.

The only behaviour difference is timing: a message or a group membership is
removed up to ``clean_interval`` seconds after it expires rather than on the
next message, so until then an expired message may still be delivered, and a
queue full of expired messages keeps raising ``ChannelFull``. That only
touches a consumer that has not read for ``expiry`` (60 s). With
``clean_interval=0`` the layer behaves exactly like Channels' own.
"""

from __future__ import annotations

import asyncio
import collections
import threading
import time
from copy import deepcopy
from typing import Any, Deque, Dict, List, Optional, Tuple

from channels.exceptions import ChannelFull
from channels.layers import InMemoryChannelLayer as _ChannelsInMemoryChannelLayer

__all__ = ["InMemoryChannelLayer", "MultiLoopInMemoryChannelLayer"]


class InMemoryChannelLayer(_ChannelsInMemoryChannelLayer):
    """Channels' in-memory layer with a rate-limited expiry sweep.

    Accepts every argument of ``channels.layers.InMemoryChannelLayer`` plus
    ``clean_interval``: the minimum number of seconds between two expiry
    sweeps (default ``1.0``; ``0`` sweeps on every message, as Channels does).
    """

    def __init__(
        self,
        expiry: int = 60,
        group_expiry: int = 86400,
        capacity: int = 100,
        channel_capacity: Any = None,
        clean_interval: float = 1.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            expiry=expiry,
            group_expiry=group_expiry,
            capacity=capacity,
            channel_capacity=channel_capacity,
            **kwargs,
        )
        interval = float(clean_interval)
        if interval < 0:
            raise ValueError(f"clean_interval must be >= 0 seconds, got {clean_interval!r}")
        self.clean_interval = interval
        self._next_clean = 0.0

    def _clean_expired(self) -> None:
        now = time.monotonic()
        if now < self._next_clean:
            return
        self._next_clean = now + self.clean_interval
        super()._clean_expired()

    async def group_send(self, group: str, message: dict) -> None:
        """Channels' ``group_send`` without a task per member (#3095).

        Channels wraps each member's ``send()`` in ``asyncio.create_task`` and
        gathers them with ``as_completed``. ``send()`` on this layer never
        suspends (it puts on the member's queue or raises ``ChannelFull``),
        so awaiting each in turn delivers the same messages, in the same
        order, before this returns, without creating and scheduling a task
        per session on the event loop. A full channel is skipped, as before.
        Unlike Channels' version it does not yield to the loop between
        members, which only matters to a caller that group-sends in a loop
        with no other ``await``.
        """
        assert isinstance(message, dict), "Message is not a dict"
        require = getattr(self, "require_valid_group_name", None)
        if require is not None:
            require(group)
        else:  # Channels < 4.2
            assert self.valid_group_name(group), "Group name not valid"
        self._clean_expired()
        members = self.groups.get(group)
        if not members:
            return
        error: Exception | None = None
        for channel in list(members):
            try:
                await self.send(channel, message)
            except ChannelFull:
                # A full member drops this message, as Channels' own layers do;
                # it is not a delivery error for the group.
                pass
            except Exception as exc:  # noqa: BLE001 - raised after every member got it
                # Channels' tasks still deliver to the other members when one
                # send fails, and the first failure then propagates.
                if error is None:
                    error = exc
        if error is not None:
            raise error

    async def flush(self) -> None:
        await super().flush()
        self._next_clean = 0.0


def _require_channel(layer: Any, name: str) -> None:
    require = getattr(layer, "require_valid_channel_name", None)
    if require is not None:
        require(name)
    else:  # Channels < 4.2
        assert layer.valid_channel_name(name), "Channel name not valid"


def _require_group(layer: Any, name: str) -> None:
    require = getattr(layer, "require_valid_group_name", None)
    if require is not None:
        require(name)
    else:  # Channels < 4.2
        assert layer.valid_group_name(name), "Group name not valid"


def _wake_pending(futures: List["asyncio.Future[None]"]) -> None:
    """Wake parked receivers. Runs on the receivers' own loop."""
    for fut in futures:
        if not fut.done():
            fut.set_result(None)


class MultiLoopInMemoryChannelLayer(InMemoryChannelLayer):
    """The in-memory layer for a process with several event loops (#3128).

    ``djust serve --loops N`` runs N asyncio event loops in one process, one per
    thread. A session's consumer receives on the loop that accepted its
    connection, while pushes to it come from sessions, room clocks and
    ``push_to_view`` calls on any loop. Channels' layer (and
    :class:`InMemoryChannelLayer`) keep each channel in an ``asyncio.Queue``,
    which only its own loop may touch, so a send from another loop can wake the
    receiver from the wrong thread or not at all.

    Here a channel is a plain ``collections.deque`` of ``(expires, message)``
    guarded by one ``threading.Lock``, and belongs to no loop:

    * ``send`` checks the channel's capacity and appends under the lock
      (``ChannelFull`` is raised before any hand-off), then wakes one parked
      receiver: directly when the receiver is on the sender's loop, through
      ``loop.call_soon_threadsafe`` otherwise;
    * ``receive`` takes the oldest message under the lock or parks a future of
      its own loop until a sender wakes it;
    * ``group_send`` appends to every member under one acquisition of the lock,
      so two group sends reach every member in the same order, and wakes each
      receiving loop once, not once per member.

    Messages on one channel arrive in the order they were appended, whichever
    loops sent them. Everything else is :class:`InMemoryChannelLayer`: the
    same arguments, ``expiry``, ``group_expiry``, ``capacity``, per-channel
    capacities, the rate-limited expiry sweep (``clean_interval``), and a
    group send that skips full members.

    Configure it where the in-memory layer is right at all, one process::

        CHANNEL_LAYERS = {
            "default": {"BACKEND": "djust.layers.MultiLoopInMemoryChannelLayer"},
        }
    """

    #: Checked by :func:`djust.multiloop.serve` before it starts several loops.
    multi_loop_safe = True

    def __init__(
        self,
        expiry: int = 60,
        group_expiry: int = 86400,
        capacity: int = 100,
        channel_capacity: Any = None,
        clean_interval: float = 1.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            expiry=expiry,
            group_expiry=group_expiry,
            capacity=capacity,
            channel_capacity=channel_capacity,
            clean_interval=clean_interval,
            **kwargs,
        )
        # Channels' BaseChannelLayer stores ``channel_capacity`` uncompiled,
        # so its get_capacity() fails on the dict; compile the globs here.
        if isinstance(self.channel_capacity, dict):
            self.channel_capacity = self.compile_capacities(self.channel_capacity)
        self._lock = threading.Lock()
        # channel -> messages; replaces Channels' asyncio.Queue per channel.
        self.channels: Dict[str, Deque[Tuple[float, dict]]] = {}  # type: ignore[assignment]
        # channel -> parked receivers, oldest first.
        self._waiters: Dict[str, Deque["asyncio.Future[None]"]] = {}

    # -- delivery ---------------------------------------------------------

    def _append(self, channel: str, item: Tuple[float, dict]) -> Optional["asyncio.Future[None]"]:
        """Append under ``self._lock``; return the receiver to wake, if any."""
        queue = self.channels.get(channel)
        if queue is None:
            queue = self.channels[channel] = collections.deque()
        capacity = self.get_capacity(channel)
        if capacity > 0 and len(queue) >= capacity:  # <= 0: unbounded, as asyncio.Queue
            raise ChannelFull(channel)
        queue.append(item)
        return self._pop_waiter(channel)

    def _pop_waiter(self, channel: str) -> Optional["asyncio.Future[None]"]:
        """The oldest receiver still waiting on ``channel`` (under the lock)."""
        waiters = self._waiters.get(channel)
        while waiters:
            fut = waiters.popleft()
            if not fut.done():
                if not waiters:
                    del self._waiters[channel]
                return fut
        if waiters is not None:
            del self._waiters[channel]
        return None

    @staticmethod
    def _wake(woken: List["asyncio.Future[None]"]) -> None:
        """Wake receivers, one hand-off per receiving loop."""
        if not woken:
            return
        try:
            here: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        by_loop: Dict[asyncio.AbstractEventLoop, List["asyncio.Future[None]"]] = {}
        for fut in woken:
            by_loop.setdefault(fut.get_loop(), []).append(fut)
        for loop, futures in by_loop.items():
            if loop is here:
                _wake_pending(futures)
                continue
            try:
                loop.call_soon_threadsafe(_wake_pending, futures)
            except RuntimeError:
                # The receiver's loop is closed; its receive() is gone and the
                # message stays queued for the next receiver of the channel.
                pass

    async def send(self, channel: str, message: dict) -> None:
        """Queue ``message`` on ``channel``; raise ``ChannelFull`` when full."""
        assert isinstance(message, dict), "message is not a dict"
        _require_channel(self, channel)
        assert "__asgi_channel__" not in message
        item = (time.time() + self.expiry, deepcopy(message))
        with self._lock:
            waiter = self._append(channel, item)
        if waiter is not None:
            self._wake([waiter])

    async def receive(self, channel: str) -> dict:
        """The oldest message on ``channel``, waiting on this loop if none."""
        _require_channel(self, channel)
        self._clean_expired()
        loop = asyncio.get_running_loop()
        while True:
            with self._lock:
                queue = self.channels.get(channel)
                if queue:
                    _, message = queue.popleft()
                    if not queue and channel not in self._waiters:
                        del self.channels[channel]
                    return message
                fut: "asyncio.Future[None]" = loop.create_future()
                self._waiters.setdefault(channel, collections.deque()).append(fut)
            try:
                await fut
            except BaseException:
                self._abandon(channel, fut)
                raise
            # Woken: take the message, or park again if another receiver on
            # the channel took it first.

    def _abandon(self, channel: str, fut: "asyncio.Future[None]") -> None:
        """A parked receive() was cancelled: forget it, and if a sender had
        already picked it to wake, pass that wake-up to the next receiver."""
        nxt = None
        with self._lock:
            waiters = self._waiters.get(channel)
            picked = True
            if waiters is not None:
                try:
                    waiters.remove(fut)
                    picked = False
                except ValueError:
                    pass
                if not waiters:
                    del self._waiters[channel]
            queue = self.channels.get(channel)
            if picked and queue:
                nxt = self._pop_waiter(channel)
            if queue is not None and not queue and channel not in self._waiters:
                del self.channels[channel]
        if nxt is not None:
            self._wake([nxt])

    # -- groups -----------------------------------------------------------

    async def group_add(self, group: str, channel: str) -> None:
        _require_group(self, group)
        _require_channel(self, channel)
        with self._lock:
            self.groups.setdefault(group, {})[channel] = time.time()

    async def group_discard(self, group: str, channel: str) -> None:
        _require_channel(self, channel)
        _require_group(self, group)
        with self._lock:
            members = self.groups.get(group)
            if members:
                members.pop(channel, None)
                if not members:
                    self.groups.pop(group, None)

    async def group_send(self, group: str, message: dict) -> None:
        """Deliver to every member; a full member drops the message.

        All members get the message under one acquisition of the lock, and
        each receiving loop is woken once.
        """
        assert isinstance(message, dict), "Message is not a dict"
        _require_group(self, group)
        self._clean_expired()
        with self._lock:
            members = list(self.groups.get(group, ()))
        if not members:
            return
        expires = time.time() + self.expiry
        items = [(channel, (expires, deepcopy(message))) for channel in members]
        woken: List["asyncio.Future[None]"] = []
        with self._lock:
            for channel, item in items:
                try:
                    waiter = self._append(channel, item)
                except ChannelFull:
                    continue
                if waiter is not None:
                    woken.append(waiter)
        self._wake(woken)

    # -- expiry and flush -------------------------------------------------

    def _clean_expired(self) -> None:
        """Drop expired messages and memberships, at most once per interval.

        Loop-agnostic: the queues are deques, so the sweep touches no loop's
        state and can run from whichever loop calls it.
        """
        if time.monotonic() < self._next_clean:  # unlocked pre-check: the common case
            return
        with self._lock:
            now_mono = time.monotonic()
            if now_mono < self._next_clean:
                return
            self._next_clean = now_mono + self.clean_interval
            now = time.time()
            for channel, queue in list(self.channels.items()):
                expired = False
                while queue and queue[0][0] < now:
                    queue.popleft()
                    expired = True
                if expired:
                    # As Channels does: an expired message means nobody is
                    # reading the channel, so it leaves every group.
                    for members in self.groups.values():
                        members.pop(channel, None)
                    if not queue and channel not in self._waiters:
                        del self.channels[channel]
            timeout = int(now) - self.group_expiry
            for members in self.groups.values():
                for name, joined in list(members.items()):
                    if joined and joined < timeout:
                        members.pop(name, None)

    def _remove_from_groups(self, channel: str) -> None:  # noqa: dead-method-allowed
        # Overrides Channels' hook so a caller outside the sweep (the sweep
        # inlines it under the lock it already holds) stays lock-safe.
        with self._lock:
            for members in self.groups.values():
                members.pop(channel, None)

    async def flush(self) -> None:
        """Drop every message and group; parked receivers keep waiting."""
        with self._lock:
            self.channels = {}
            self.groups = {}
            self._next_clean = 0.0
