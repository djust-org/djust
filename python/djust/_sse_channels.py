"""An SSE view's member of the channel layer (#3885).

``push_to_view`` / ``apush_to_view`` (view-wide and ``scope=`` pushes) and
``db_notify`` reach a view through the channel layer: the publisher does a
``group_send`` to ``djust_view_<path>``, ``djust_scope_<digest>`` or
``djust_db_notify_<channel>``, and every channel that joined the group receives
the message. A WebSocket consumer has a channel; an SSE session does not, so
before #3885 nothing joined a group on its behalf and an SSE page never saw
these turns.

:class:`SSEChannelMember` gives each SSE-mounted view a channel of its own
(``channel_layer.new_channel()``) and a receive loop on the session's event
loop. It joins and leaves groups through the SAME functions the WebSocket
transport uses (``runtime.join_view_push_group``,
``runtime.join_listen_channels``, ``push.sync_push_scope_groups``,
``runtime.leave_consumer_view_groups``), so the group names, the scope and
tenant rules and the cleanup are one implementation. Delivery applies the
WebSocket's receive-side checks (``push.push_tenant_matches``, the #1677
own-echo skip) and then runs the turn through the view's
:class:`~djust.runtime.ViewRuntime` (``server_push`` / ``db_notify``), whose
push body is the WebSocket's ``_prepare_server_push`` (state through
``safe_setattr``, the handler allowlist).

Because delivery is the channel layer's, it works across processes with a
shared layer (``channels_redis``) exactly as the WebSocket does, and not across
processes with the in-memory layer, exactly as the WebSocket does not. With no
channel layer configured the member is inert, as a WebSocket without a layer
is.

One channel per view: the page view and every view mounted beside it (an SSE
slot, #3252) have their own transport, hence their own member, so a message
needs no routing between views of one session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

#: Leave tasks scheduled by a release, held until they finish so one is not
#: garbage-collected half way.
_PENDING_LEAVES: Set["asyncio.Task[Any]"] = set()

#: Pause after a channel-layer ``receive`` error before the next try (a Redis
#: outage must not spin the loop).
_RECEIVE_RETRY_S = 1.0


class _RecordingLayer:
    """The channel layer, as one member uses it: it records every group the
    member's channel was actually added to (and removes the ones it left), so a
    release can discard a join that landed after the release's own leave ran
    (a networked layer yields inside ``group_add``)."""

    def __init__(self, layer: Any, member: "SSEChannelMember") -> None:
        self._layer = layer
        self._member = member

    async def group_add(self, group: str, channel: str) -> None:
        await self._layer.group_add(group, channel)
        if channel == self._member.channel_name:
            self._member._joined.add(group)

    async def group_discard(self, group: str, channel: str) -> None:
        await self._layer.group_discard(group, channel)
        if channel == self._member.channel_name:
            self._member._joined.discard(group)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._layer, name)


class SSEChannelMember:
    """The channel, and the groups it joined, of one SSE-mounted view.

    Carries the attributes the shared group helpers read and write on a
    WebSocket consumer (``channel_layer``, ``channel_name``, ``_view_path``,
    ``_view_group``, ``_db_notify_channels``, ``_push_scope_groups``,
    ``_presence_scope_group``), so those helpers run unchanged on it.
    """

    def __init__(self, transport: Any) -> None:
        from channels.layers import get_channel_layer

        self._transport = transport
        layer = get_channel_layer()
        #: Every group this member's channel is in right now, as added (see
        #: :class:`_RecordingLayer`); what a release discards in the end.
        self._joined: Set[str] = set()
        self.channel_layer: Any = _RecordingLayer(layer, self) if layer is not None else None
        self.channel_name: Optional[str] = None
        self._view_path = ""
        self._view_group: Optional[str] = None
        self._db_notify_channels: Set[str] = set()
        self._push_scope_groups: Dict[str, str] = {}
        self._presence_scope_group: Optional[str] = None
        self._receive_task: Optional["asyncio.Task[Any]"] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._released = False

    # ------------------------------------------------------------------ #
    # Joining
    # ------------------------------------------------------------------ #

    @property
    def active(self) -> bool:
        """Whether this member holds a channel and has not been released."""
        return self.channel_name is not None and not self._released

    async def attach(self, view: Any) -> None:
        """Take a channel, start receiving, and join the view's push group and
        the NOTIFY groups of its class-level ``_listen_channels``.

        Called from the SSE transport's ``on_view_mounted``: after the mount
        was admitted (a refused mount joins nothing), before ``mount()``, at
        the point the WebSocket transport joins the same groups.
        """
        from .runtime import join_listen_channels, join_view_push_group

        if self.channel_layer is None or self._released or self.channel_name is not None:
            return
        self._loop = asyncio.get_running_loop()
        self.channel_name = await self.channel_layer.new_channel(prefix="djust_sse")
        if self._released:
            # Released while the channel was being created (a networked layer
            # may yield there): start no reader, join nothing.
            return
        self._receive_task = self._loop.create_task(self._receive_loop())
        try:
            await join_view_push_group(self, view)
            await join_listen_channels(self, view)
        finally:
            self._leave_if_released()

    async def sync(self, view: Any) -> None:
        """Join what the view asked for since: ``listen()`` channels and its
        current ``push_scope`` (leaving dropped scopes).

        Run after ``mount()`` and at the end of every turn, while the turn
        still holds the render lock, as the WebSocket transport does (#2962,
        #3004). Idempotent and cheap when nothing changed.
        """
        from .push import sync_push_scope_groups
        from .runtime import join_listen_channels

        if not self.active:
            return
        try:
            await join_listen_channels(self, view)
            await sync_push_scope_groups(self, view)
        finally:
            self._leave_if_released()

    # ------------------------------------------------------------------ #
    # Leaving
    # ------------------------------------------------------------------ #

    def release(self) -> None:
        """Stop receiving and leave every group (the view's teardown).

        Synchronous and safe from any thread: ``SSESession.shutdown`` runs on
        a worker thread when the response is abandoned. The receive loop and
        the group leaves belong to the loop that attached, so the work is
        handed to it. Idempotent.
        """
        if self._released:
            return
        self._released = True
        loop = self._loop
        if loop is None or loop.is_closed():
            # Never attached, or its loop is gone with every task on it. On a
            # closed (or stopped, below) loop the groups cannot be left from
            # here: as for a WebSocket consumer whose loop died before its
            # ``disconnect`` ran, the layer drops the memberships when they
            # reach its ``group_expiry`` (a day by default).
            return
        try:
            here: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if here is loop:
            self._stop_and_leave()
        elif loop.is_running():
            loop.call_soon_threadsafe(self._stop_and_leave)
        # else: a loop that is not running can run nothing; see above.

    def _leave_if_released(self) -> None:
        """A release that raced a join: leave what the join added after the
        release's own leave ran (``_joined`` records it, whichever attribute
        the shared helper had already reset)."""
        if self._released and self._loop is not None and not self._loop.is_closed():
            self._schedule_leave()

    def _stop_and_leave(self) -> None:
        task = self._receive_task
        self._receive_task = None
        if task is not None and not task.done():
            task.cancel()
        self._schedule_leave()

    def _schedule_leave(self) -> None:
        loop = self._loop
        if loop is None:
            return
        task = loop.create_task(self._leave())
        _PENDING_LEAVES.add(task)
        task.add_done_callback(_PENDING_LEAVES.discard)

    async def _leave(self) -> None:
        from .runtime import leave_consumer_view_groups

        # Logs, never raises, and resets the group attributes.
        await leave_consumer_view_groups(self)
        # Whatever the attributes no longer name: a join that landed after an
        # earlier leave reset them.
        for group in sorted(self._joined):
            try:
                await self.channel_layer.group_discard(group, self.channel_name)
            except Exception:  # noqa: BLE001 - best effort, as the shared leave
                logger.warning("Error leaving channel group %s", group)

    # ------------------------------------------------------------------ #
    # Receiving
    # ------------------------------------------------------------------ #

    async def _receive_loop(self) -> None:
        """Read this view's channel until released; each message is one delivery."""
        layer, channel = self.channel_layer, self.channel_name
        while True:
            try:
                message = await layer.receive(channel)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a layer outage must not end the loop
                from ._exposure_diagnostics import log_failure

                log_failure(
                    logger,
                    exc,
                    "SSE: channel-layer receive failed (%s); retrying",
                    type(exc).__name__,
                    level="warning",
                )
                await asyncio.sleep(_RECEIVE_RETRY_S)
                continue
            try:
                await self.deliver(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad message must not end the loop
                from ._exposure_diagnostics import log_failure_for

                view = getattr(self._transport.runtime, "view_instance", None)
                log_failure_for(
                    logger,
                    (view,) if view is not None else (),
                    exc,
                    "SSE: delivering a channel-layer message failed (%s)",
                    type(exc).__name__,
                    level="warning",
                )

    async def deliver(self, message: Dict[str, Any]) -> None:
        """Apply one channel-layer message to the view, as the WebSocket's
        ``server_push`` / ``db_notify`` handlers do.

        Presence events are not delivered: presence is not supported over SSE.
        """
        from .push import push_tenant_matches

        if not isinstance(message, dict) or self._released:
            return
        runtime = self._transport.runtime
        view = runtime.view_instance
        if view is None:
            return
        kind = message.get("type")
        if kind == "server_push":
            if not push_tenant_matches(view, message):
                logger.debug(
                    "[djust] server_push on %s dropped — stamped for another tenant",
                    type(view).__name__,
                )
                return
            sender = message.get("sender_channel")
            if sender and sender == self.channel_name:
                # The session's own handler pushed to its own view; its event
                # answer already carried the state (#1677).
                logger.debug(
                    "[djust] server_push on %s skipped — own self-broadcast (#1677)",
                    type(view).__name__,
                )
                return
            await runtime.server_push(view, message)
        elif kind == "db_notify":
            await runtime.db_notify(
                view,
                {
                    "type": "db_notify",
                    "channel": message.get("channel", ""),
                    "payload": message.get("payload", {}),
                },
            )
