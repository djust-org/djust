"""Independently live views on one SSE session (#3252).

The WebSocket hosts the page view plus any number of views mounted beside it
(``dj-lazy`` containers, ``mount_batch``): :mod:`djust._view_slots`. An SSE
session has the same shape. The stream (``EventSource``) is the transport, the
page view is the session's own, and every other view is a *slot* keyed by the
``target_id`` its ``mount`` frame named (the client's ``data-djust-target``
container).

A slot is a full root view: its own :class:`~djust.runtime.ViewRuntime` (mount,
authorization, events, saves, explicit-exposure turns) over an
:class:`~djust.runtime.SSESessionTransport` that wraps an :class:`SSESlotSession`
instead of the session. The facade keeps the per-view attributes of the session
(the mounted view, the mount request) for its slot, forwards the rest (the
queue, the dispatch and render locks, the owner binding, the rate limiter) to
the real session, and stamps every frame the slot sends with its ``target_id``
so the client applies it to the right container.

The page view is untouched: it is the session itself, its frames carry no
``target_id``, and a page with no sibling views creates no slot. SSE has no
server push, presence, ``db_notify`` or tick for any view, so a slot has none
either; what a slot gets is its own mount, events, async work, saved state and
teardown.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from ._view_slots import PAGE_LEVEL_FRAMES, refusal_frame, valid_target_id

logger = logging.getLogger(__name__)

__all__ = ["SSESlot", "SSESlotSession", "valid_target_id"]


class SSESlotSession:
    """The SSE session, as one view mounted beside the page view sees it."""

    def __init__(self, session: Any, target_id: str) -> None:
        self._parent = session
        self.target_id = target_id
        #: The view this slot mounted (set by ``SSESessionTransport.on_view_mounted``).
        self.view_instance: Optional[Any] = None
        #: The request the slot's view was mounted against.
        self._request: Optional[Any] = None
        #: Only the page view is ever replaced by a navigation.
        self._replacing_view = False
        #: True once the slot's ``mount`` frame has been sent.
        self.mount_sent = False
        #: What serializes this view's renders (event turns, background
        #: results); the view's own, so a slow turn in another view does not
        #: hold it up (#3252).
        self._render_lock = asyncio.Lock()
        #: The POST request of the turn being dispatched for this view.
        self._event_request: Optional[Any] = None
        #: True once a ``view_refused`` frame has been sent for the view.
        self.refused = False

    # -- what differs for a slot -------------------------------------------

    @property
    def session_id(self) -> str:
        """A distinct id per view: the Rust state cache and the observability
        registry key on it, so two views must not share one."""
        return f"{self._parent.session_id}.{self.target_id}"

    def push(self, msg: Dict[str, Any]) -> None:
        """Enqueue a frame addressed to this slot's container."""
        if isinstance(msg, dict):
            if msg.get("type") == "mount":
                self.mount_sent = True
            if msg.get("type") not in PAGE_LEVEL_FRAMES:
                msg = {**msg}
                msg.setdefault("target_id", self.target_id)
        self._parent.push(msg)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        kwargs.setdefault("target_id", self.target_id)
        await self._parent.send_error(error, **kwargs)

    async def send_refusal(self, reason: str, to: Optional[str] = None) -> None:
        """Tell the client this view is refused (see ``SlotConsumer.send_refusal``)."""
        if self.refused:
            return
        self.refused = True
        self.push(refusal_frame(reason, to))

    async def close(self, code: int = 1000) -> None:
        """Close what this view runs on.

        An authorization refusal (4401 / 4403) ends this view only: the stream
        also carries the views that are still allowed to be live. Any other
        code is the stream's.
        """
        if code in (4401, 4403):
            if not self.refused:
                await self.send_refusal("permission_denied")
            await self._parent._release_slot(self.target_id, reason="view_refused")
            return
        await self._parent.close(code=code)

    # -- the session's own state, shared by every view on it ---------------

    def __getattr__(self, name: str) -> Any:
        # Only called when normal lookup fails: the render lock, the rate
        # limiter, the client address, the owner binding, ... belong to the
        # connection, not to a view.
        return getattr(object.__getattribute__(self, "_parent"), name)


class SSESlot:
    """One view mounted on an SSE session besides the page view."""

    __slots__ = ("target_id", "session", "runtime", "dispatch_lock")

    def __init__(self, session: Any, target_id: str) -> None:
        from .runtime import SSESessionTransport, ViewRuntime

        self.target_id = target_id
        #: One turn of this view at a time (its events, its unmount); other
        #: views' turns run beside it.
        self.dispatch_lock = asyncio.Lock()
        self.session = SSESlotSession(session, target_id)
        self.runtime = ViewRuntime(
            SSESessionTransport(self.session), rate_limiter=session._rate_limiter
        )

    @property
    def view(self) -> Optional[Any]:
        return self.session.view_instance
