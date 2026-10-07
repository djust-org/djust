"""Independently live views on one WebSocket (#3252).

A page can carry several LiveViews: the eager page view, ``dj-lazy`` views that
hydrate later, and the views of a ``mount_batch``. They share one socket, so
the socket (the :class:`~djust.websocket.LiveViewConsumer`) is the transport
and each view is a *slot* on it.

The consumer's own attributes (``view_instance``, ``_view_group``, the tick
task, the wire version, ...) describe ONE view: the page view, mounted by a
``mount`` frame without a ``target_id``. Every other view lives in a
:class:`ViewSlot`, keyed by the ``target_id`` its mount frame named (the
client's ``data-djust-target`` container). A slot is a full root view: it gets
its own :class:`~djust.runtime.ViewRuntime` (mount, auth, events, saves,
explicit-exposure authorization, all per view) and its own copy of the
per-view consumer state.

:class:`SlotConsumer` is what lets the code that already runs a view's turns
work for a slot unchanged. It stands in for the consumer: the per-view
attributes read and write the slot, everything else (the channel layer, the
render lock, the rate limiter, the socket) is the real consumer's, and a
consumer method looked up on it runs with the slot as ``self``. A turn for a
slot is therefore the code of a turn for the page view, on the slot's state,
and its frames leave stamped with the slot's ``target_id`` so the client
applies them to the right container.

Nothing here changes a page that has no sibling views: the page view is the
consumer itself, its frames carry no ``target_id``, and no slot is created.
"""

import asyncio
import inspect
import logging
import types
from typing import Any, Callable, Dict, List, Optional, TypeGuard

logger = logging.getLogger(__name__)

#: Longest ``target_id`` the consumer accepts. It is client-supplied, echoed in
#: stamped frames and used as a dict key, so it is bounded and charset-checked.
MAX_TARGET_ID_LENGTH = 200

#: Views one socket may host besides the page view, unless
#: ``LIVEVIEW_CONFIG["max_views_per_connection"]`` says otherwise. Every view is
#: a runtime, a tick task and several channel-layer group memberships.
DEFAULT_MAX_SLOTS = 64

# Printable ASCII with no whitespace, and none of the characters that quote or
# open markup. The stock client sends the container's ``id`` or
# ``dj-target-<random>``.
_FORBIDDEN_TARGET_CHARS = frozenset("\"'`\\<>")


def valid_target_id(value: Any) -> TypeGuard[str]:
    """Whether ``value`` is acceptable as the address of a view on the socket."""
    return (
        type(value) is str
        and 0 < len(value) <= MAX_TARGET_ID_LENGTH
        and all(0x21 <= ord(c) <= 0x7E and c not in _FORBIDDEN_TARGET_CHARS for c in value)
    )


def _empty_set() -> set:
    return set()


def _empty_dict() -> dict:
    return {}


#: The consumer attributes that describe one mounted view, with the value an
#: unmounted slot reads for each. A name absent from the table is shared by
#: every view on the socket.
PER_VIEW_ATTRS: Dict[str, Callable[[], Any]] = {
    "view_instance": lambda: None,
    "actor_handle": lambda: None,
    "use_actors": lambda: False,
    "_view_group": lambda: None,
    "_view_path": lambda: "",
    "_presence_group": lambda: None,
    "_presence_groups": _empty_set,
    "_presence_scope_group": lambda: None,
    "_presence_scope_auto": lambda: None,
    "_push_scope_groups": _empty_dict,
    "_push_scope_invalid_logged": lambda: False,
    "_db_notify_channels": _empty_set,
    "_tick_task": lambda: None,
    # The wire version a view's frames carry is per view: the client keeps one
    # cursor per container, so a slot's frames are numbered from its own mount.
    "_last_sent_version": lambda: 0,
    "_recovery_html": lambda: None,
    "_recovery_version": lambda: 0,
    "_recovery_contracts": lambda: None,
    "_recovery_owner": lambda: None,
    "_sticky_preserved": _empty_dict,
    "_sticky_auto_reattached": _empty_set,
    "_runtime": lambda: None,
    "_mounting_in_batch": lambda: False,
    "_current_event_name": lambda: None,
    "_current_event_ref": lambda: None,
    # When a ``reauth_on_event`` re-check of a server-originated turn last
    # passed FOR THIS VIEW: each view re-checks its own authority, so one view's
    # pass must never cover a sibling with different permissions.
    "_server_turn_reauth_at": lambda: None,
    # What serializes one view's renders, and what the view's tick and pushes
    # consult: a view's own, so a slow turn in one view does not hold up the
    # renders of another (#3252). The page view's are the consumer's own.
    "_render_lock": asyncio.Lock,
    "_processing_user_event": lambda: False,
    "_deferred_pushes": lambda: None,
    "_push_drain_task": lambda: None,
    # The queue its inbound frames run on while the connection hosts several
    # views (``_view_lanes``); created when the first frame is queued.
    "_lane": lambda: None,
}

#: Frames whose effect is the page's, not one container's: the client acts on
#: them wherever they came from, so they carry no ``target_id``.
PAGE_LEVEL_FRAMES = frozenset(
    {
        "navigate",
        "navigation",
        "flash",
        "page_metadata",
        "layout",
        "accessibility",
        "focus",
        "i18n",
        "reload",
        "hvr-applied",
        "push_event",
        "presence_event",
        "sticky_hold",
        "rate_limit_exceeded",
        "connect",
        "pong",
        "time_travel_state",
        "time_travel_event",
        "bug_capture_share_result",
    }
)

#: Consumer methods that must run on the real consumer even when asked for
#: through a slot: they are the socket itself, or they manage every view on it
#: (the registry, the deferred-push queue, the teardown of all views), so they
#: must see the page view and every slot, not one view's state.
_CONSUMER_METHODS = frozenset(
    {
        "close",
        "send",
        "_send_frame",
        "accept",
        "_slot_map",
        "_view_consumers",
        "_mounted_views",
        "_default_consumer",
        "_route_consumer",
        "_consumer_for_view",
        "_channel_targets",
        "_untrack_presence_of",
        "_release_before_mount",
        "_release_consumer_view",
        "_release_mounted_views",
        "_release_slot",
        "_mount_slot",
        "_cancel_deferred_pushes",
        "_flush_deferred_presence_untrack",
        "_defer_presence_untrack",
        "_take_deferred_presence_untrack",
    }
)

#: Why a view beside the page view was refused, as ``view_refused`` frames name it.
REFUSAL_REASONS = frozenset({"login_required", "permission_denied", "redirect"})


def refusal_frame(reason: str, to: Optional[str] = None) -> Dict[str, Any]:
    """The ``view_refused`` frame for a view that may not be shown (#3252).

    ``reason`` is ``login_required`` (the view needs a sign-in; ``to`` is the
    sign-in URL), ``permission_denied`` or ``redirect`` (an ``on_mount`` hook
    sent the view elsewhere; ``to`` is where). The frame carries no message
    text: the client words it, and the application can restyle or replace it
    through the ``djust:view-refused`` event.
    """
    frame: Dict[str, Any] = {
        "type": "view_refused",
        "reason": reason if reason in REFUSAL_REASONS else "permission_denied",
        "code": "permission_denied",
    }
    if isinstance(to, str) and to:
        frame["to"] = to
    return frame


# Auth refusals. A refused slot is torn down on its own: the socket also carries
# the views that are still allowed to be live.
_AUTH_CLOSE_CODES = frozenset({4401, 4403})


class ViewSlot:
    """One view mounted on the socket besides the page view."""

    __slots__ = ("target_id", "state", "capture", "runtime", "facade", "closing", "refused")

    def __init__(self, consumer: Any, target_id: str) -> None:
        self.target_id = target_id
        #: The slot's values of :data:`PER_VIEW_ATTRS`; unset ones read the default.
        self.state: Dict[str, Any] = {}
        #: While a list, frames the slot sends are collected instead of sent
        #: (``mount_batch`` gathers each entry's mount frame).
        self.capture: Optional[List[Dict[str, Any]]] = None
        self.closing = False
        #: True once a ``view_refused`` frame told the client this view is not
        #: allowed (sent at most once).
        self.refused = False
        self.facade = SlotConsumer(consumer, self)
        from .runtime import ViewRuntime, WSConsumerTransport

        main = consumer._get_runtime()
        self.runtime = ViewRuntime(
            WSConsumerTransport(self.facade),
            scope=consumer.scope,
            rate_limiter=consumer._rate_limiter,
            renderer_factory=main.renderer_factory,
        )
        self.state["_runtime"] = self.runtime

    @property
    def view(self) -> Any:
        return self.state.get("view_instance")


class SlotConsumer:
    """The consumer, as one slot sees it (see the module docstring)."""

    def __init__(self, consumer: Any, slot: ViewSlot) -> None:
        object.__setattr__(self, "_slot_consumer", consumer)
        object.__setattr__(self, "_slot", slot)

    # -- attribute routing -------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Only called when normal lookup fails, i.e. for everything but the
        # two attributes above and the methods defined on this class.
        slot: ViewSlot = object.__getattribute__(self, "_slot")
        if name in PER_VIEW_ATTRS:
            try:
                return slot.state[name]
            except KeyError:
                value = PER_VIEW_ATTRS[name]()
                slot.state[name] = value
                return value
        consumer = object.__getattribute__(self, "_slot_consumer")
        # The raw class attribute: a staticmethod, classmethod or property is
        # looked up on the consumer as usual; only a plain method is rebound.
        function = inspect.getattr_static(type(consumer), name, None)
        if (
            name not in _CONSUMER_METHODS
            and isinstance(function, types.FunctionType)
            and not name.startswith("__")
        ):
            # A consumer method runs with the slot as ``self``: its reads and
            # writes of per-view state land on the slot.
            return types.MethodType(function, self)
        return getattr(consumer, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in PER_VIEW_ATTRS:
            object.__getattribute__(self, "_slot").state[name] = value
        else:
            setattr(object.__getattribute__(self, "_slot_consumer"), name, value)

    def __delattr__(self, name: str) -> None:
        if name in PER_VIEW_ATTRS:
            object.__getattribute__(self, "_slot").state.pop(name, None)
        else:
            delattr(object.__getattribute__(self, "_slot_consumer"), name)

    # -- what differs for a slot -------------------------------------------

    @property
    def target_id(self) -> str:
        return object.__getattribute__(self, "_slot").target_id

    @property
    def session_id(self) -> Optional[str]:
        """A distinct id per view: the Rust state cache, the observability
        registry and the actor registry key on it, so two views must not share
        one."""
        consumer = object.__getattribute__(self, "_slot_consumer")
        base = getattr(consumer, "session_id", None)
        return f"{base}.{self.target_id}" if base else base

    def _get_runtime(self) -> Any:
        return object.__getattribute__(self, "_slot").runtime

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send a frame addressed to this slot's container.

        While the slot is capturing (a ``mount_batch`` entry), the frame is
        collected unstamped: the batch reply stamps it.
        """
        slot: ViewSlot = object.__getattribute__(self, "_slot")
        if slot.capture is not None:
            slot.capture.append(data)
            return
        consumer = object.__getattribute__(self, "_slot_consumer")
        if isinstance(data, dict) and data.get("type") not in PAGE_LEVEL_FRAMES:
            data = {**data}
            data.setdefault("target_id", slot.target_id)
        await consumer.send_json(data)

    async def send_refusal(self, reason: str, to: Optional[str] = None) -> None:
        """Tell the client this view is refused (a ``view_refused`` frame).

        The client renders the refusal into this view's container; the page
        view, the other views and the page's URL are untouched. At most once
        per view. ``to`` is the sign-in URL a login redirect named, if any.
        """
        slot: ViewSlot = object.__getattribute__(self, "_slot")
        if slot.refused:
            return
        slot.refused = True
        await self.send_json(refusal_frame(reason, to))

    async def close(self, code: Optional[int] = None, reason: Optional[str] = None) -> None:
        """Close what this view runs on.

        An authorization refusal (4401 / 4403) ends this view only: the socket
        still carries views that are allowed. Any other code is the socket's.
        """
        consumer = object.__getattribute__(self, "_slot_consumer")
        if code in _AUTH_CLOSE_CODES:
            slot = object.__getattribute__(self, "_slot")
            if not slot.refused:
                # A refusal that arrives after the view mounted (its authority
                # was revoked): the container shows it too.
                await self.send_refusal("permission_denied")
            await consumer._release_slot(slot, reason="view_refused")
            return
        await consumer.close(code, reason)
