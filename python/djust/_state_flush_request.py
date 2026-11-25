"""Ask the connection that holds a tab's unsaved state to save it (#4164 item 1).

With ``DJUST_STATE_SAVE_INTERVAL`` set, a socket's turns are saved after their
replies. When the browser reconnects (a network blip, a load balancer that
moved it) the old connection may still be open on this or another process,
holding changes the page already shows and no save has written. The page
sends the turn serial of the last state it was shown (``state_serial``); when
the stored state's serial is lower, the reconnect asks the tab's holder to
save, through the channel layer, and waits for the store a short, bounded time
before it restores and claims the tab. Whatever is stored then is restored;
if it is still behind what the page showed, the page is told
("Recent changes could not be restored").

A WebSocket holder joins the tab's flush group the first time one of its
turns is deferred (:func:`flush_group`). An SSE holder has no channel of its
own and is not reachable: its changes are saved by its interval or its close.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: The channel-layer message type of a flush request: the consumer method
#: ``djust_state_flush`` answers it.
MESSAGE_TYPE = "djust.state_flush"

#: How long a reconnect waits for the holder's save before restoring what is
#: stored. Short: a holder that is gone (the process was killed) never answers.
WAIT_S = 1.0
POLL_S = 0.1

#: The page's notice when the restore is behind what it showed.
NOT_RESTORED_MESSAGE = "Your most recent changes could not be restored."
NOT_RESTORED_CODE = "state_not_restored"


def flush_group(view: Any, request: Any) -> Optional[str]:
    """The channel-layer group of ``view``'s tab in ``request``'s session, or
    None when the view's state is not saved per tab. Value-free: a digest of
    the session key and the tab's storage key."""
    from . import _automatic_state

    try:
        store = _automatic_state._store(view, request, create=False)
    except Exception:  # noqa: BLE001 — no group is the safe answer
        return None
    if store is None:
        return None
    session_key = getattr(store.session, "session_key", None)
    if not session_key:
        return None
    digest = hashlib.sha256(("%s\0%s" % (session_key, store.key)).encode("utf-8")).hexdigest()
    return "djust_state_flush_" + digest[:48]


def stored_serial(view: Any, request: Any) -> Optional[int]:
    """The turn serial of what the store holds for ``view``'s tab NOW (a fresh
    read of the session, and of the state store when one is configured), or
    None when nothing readable is stored. Claims nothing."""
    import copy

    from . import _automatic_state

    session = getattr(request, "session", None)
    key = getattr(session, "session_key", None)
    if session is None or not key:
        return None
    fresh = copy.copy(request)
    fresh.session = type(session)(key)
    store = _automatic_state._store(view, fresh, create=False)
    if store is None:
        return None
    found = _automatic_state._read(view, store)
    return found.serial if found is not None else None


def _reload_session(session: Any) -> None:
    """Drop ``session``'s loaded data, so its next read is fresh, unless code
    of this request wrote to it (nothing written is ever dropped)."""
    from ._session_merge import TrackedSessionData, track_session

    data = getattr(session, "_session_cache", None)
    if data is None or getattr(session, "modified", False):
        return
    tracked = type(data) is TrackedSessionData
    if tracked and data.has_foreign_changes():
        return
    del session._session_cache
    if tracked:
        track_session(session)


async def request_flush(view: Any, request: Any, page_serial: int) -> None:
    """Before a reconnect restores ``view``: when the store is behind the
    serial the page showed, ask the tab's holder to save, and wait up to
    :data:`WAIT_S` for the store to catch up. Never raises."""
    from asgiref.sync import sync_to_async

    try:
        group = await sync_to_async(flush_group)(view, request)
        if group is None:
            return
        stored = await sync_to_async(stored_serial)(view, request)
        if stored is not None and stored >= page_serial:
            return
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        await layer.group_send(group, {"type": MESSAGE_TYPE})
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WAIT_S
        while loop.time() < deadline:
            await asyncio.sleep(POLL_S)
            stored = await sync_to_async(stored_serial)(view, request)
            if stored is not None and stored >= page_serial:
                # The mount's session was read before the holder saved.
                await sync_to_async(_reload_session)(getattr(request, "session", None))
                return
    except Exception:  # noqa: BLE001 — a reconnect never fails over the request
        logger.warning("djust: could not ask the tab's other connection to save its state")


__all__ = [
    "MESSAGE_TYPE",
    "NOT_RESTORED_CODE",
    "NOT_RESTORED_MESSAGE",
    "POLL_S",
    "WAIT_S",
    "flush_group",
    "request_flush",
    "stored_serial",
]
