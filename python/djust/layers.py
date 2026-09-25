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

import time
from typing import Any

from channels.exceptions import ChannelFull
from channels.layers import InMemoryChannelLayer as _ChannelsInMemoryChannelLayer

__all__ = ["InMemoryChannelLayer"]


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
        for channel in list(members):
            try:
                await self.send(channel, message)
            except ChannelFull:
                pass

    async def flush(self) -> None:
        await super().flush()
        self._next_clean = 0.0
