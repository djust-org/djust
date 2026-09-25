"""
Server-push API for djust LiveView.

Allows background tasks (Celery, management commands, cron jobs) to push
state updates to connected LiveView clients.
"""

import contextvars
import hashlib
import logging
import re
from typing import Any, FrozenSet, Iterable, Optional, Union

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

#: What ``scope=`` and a view's ``push_scope`` accept: a string or an int (a
#: room slug, a document id), or, for ``push_scope`` only, several of them.
PushScope = Union[str, int]

_VIEW_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")

# Set by ``LiveViewConsumer.handle_event`` to the originating session's channel
# name while a user event is being handled (and reset afterward). When a handler
# calls ``push_to_view`` for its OWN view, the broadcast is tagged with this
# origin so the originating session can skip its own self-broadcast (#1677):
# that session's direct event response already reflects the state, and
# re-applying the redundant self-broadcast churns the single client-side VDOM
# version counter — under rapid event bursts that reads as non-sequential
# versions → a full-HTML ``request_html`` recovery storm + intermittent WS
# reconnect. The ContextVar is ``None`` outside event handling (Celery,
# management commands, cron, cross-view pushes), so those broadcasts are never
# suppressed. Worst case if the context doesn't propagate: the tag is ``None``
# and nothing is suppressed (no regression).
origin_channel: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "djust_push_origin_channel", default=None
)


def view_group_name(view_path: str) -> str:
    """Return the channel-layer group name for a view path."""
    return f"djust_view_{view_path.replace('.', '_')}"


def _scope_key(scope: Any) -> str:
    """Validate one scope value and return its canonical string."""
    if isinstance(scope, bool) or not isinstance(scope, (str, int)):
        raise TypeError(
            f"A push scope must be a str or an int, got {type(scope).__name__}: {scope!r}"
        )
    key = str(scope)
    if not key:
        raise ValueError("A push scope must not be empty")
    return key


def push_scope_group_name(view_path: str, scope: PushScope) -> str:
    """Return the channel-layer group for ONE scope of a view (#3004).

    Sessions of ``view_path`` whose view sets ``push_scope`` to ``scope`` are
    in this group, and ``push_to_view(view_path, scope=scope)`` sends to it.
    The name is a digest of the view path and the scope, so any scope string
    maps to a valid group name (Channels allows only ``[A-Za-z0-9_.-]``, under
    100 characters) and two different scopes never share a group.
    """
    key = _scope_key(scope)
    digest = hashlib.sha256(f"{view_path}\x00{key}".encode()).hexdigest()[:40]
    return f"djust_scope_{digest}"


def view_push_scopes(view: Any) -> FrozenSet[str]:
    """The scope keys a view instance asks to receive scoped pushes for.

    Reads ``view.push_scope``: ``None`` (the default) means none; a str or an
    int is one scope; any other iterable is several. An invalid value raises
    ``TypeError`` / ``ValueError``.
    """
    raw = getattr(view, "push_scope", None)
    if raw is None:
        return frozenset()
    if isinstance(raw, (str, int)):
        return frozenset((_scope_key(raw),))
    if isinstance(raw, Iterable) and not isinstance(raw, (bytes, bytearray, dict)):
        return frozenset(_scope_key(item) for item in raw)
    raise TypeError(
        f"push_scope must be None, a str, an int, or an iterable of them; got {type(raw).__name__}"
    )


async def sync_push_scope_groups(consumer: Any, view: Any) -> None:
    """Make ``consumer``'s scoped-push group membership match ``view.push_scope``.

    Joins the group of every scope the view now has and leaves the groups of
    scopes it dropped, so a view can move between scopes (a player changing
    rooms) by assigning ``self.push_scope``. Idempotent; called by the
    WebSocket transport after mount and after every event and server-push
    turn, while the turn still holds the render lock. An invalid
    ``push_scope`` is logged and treated as "no scopes"; a join failure is
    logged and retried on the next call.
    """
    view_path = getattr(consumer, "_view_path", None) or ""
    joined: dict = getattr(consumer, "_push_scope_groups", None) or {}
    try:
        wanted = view_push_scopes(view) if view is not None and view_path else frozenset()
    except (TypeError, ValueError):
        logger.warning(
            "%s.push_scope is invalid (a str, an int, an iterable of them, or None); "
            "it receives no scoped pushes",
            type(view).__name__,
        )
        wanted = frozenset()
    channel_layer = getattr(consumer, "channel_layer", None)
    if channel_layer is None or (not wanted and not joined):
        return
    current = dict(joined)
    for key in sorted(set(current) - wanted):
        try:
            await channel_layer.group_discard(current[key], consumer.channel_name)
        except Exception:  # noqa: BLE001 - leaving is best effort
            logger.warning("Error leaving a scoped push group of %s", view_path)
        del current[key]
    for key in sorted(wanted - set(current)):
        group = push_scope_group_name(view_path, key)
        try:
            await channel_layer.group_add(group, consumer.channel_name)
        except Exception:  # noqa: BLE001 - retried on the next turn
            logger.warning("Error joining a scoped push group of %s", view_path)
            continue
        current[key] = group
    consumer._push_scope_groups = current


async def leave_push_scope_groups(consumer: Any) -> None:
    """Leave every scoped-push group ``consumer`` joined (disconnect, redirect)."""
    joined: dict = getattr(consumer, "_push_scope_groups", None) or {}
    consumer._push_scope_groups = {}
    channel_layer = getattr(consumer, "channel_layer", None)
    if channel_layer is None:
        return
    for group in joined.values():
        try:
            await channel_layer.group_discard(group, consumer.channel_name)
        except Exception:  # noqa: BLE001 - leaving is best effort
            logger.warning("Error leaving scoped push group %s", group)


def _push_group(view_path: str, scope: Optional[PushScope]) -> str:
    if not _VIEW_PATH_RE.match(view_path):
        raise ValueError(
            f"Invalid view_path: {view_path!r}. Expected dotted Python path like 'myapp.views.MyView'"
        )
    if scope is None:
        return view_group_name(view_path)
    return push_scope_group_name(view_path, scope)


def push_to_view(
    view_path: str,
    *,
    state: Optional[dict[str, Any]] = None,
    handler: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    scope: Optional[PushScope] = None,
) -> None:
    """
    Push an update to all clients connected to a LiveView.

    Works from any synchronous context: Celery tasks, management commands,
    Django signals, cron jobs, etc.

    Args:
        view_path: Dotted path to the view class (e.g. "myapp.views.DashboardView")
        state: Dict of attribute names → values to set on the view instance
        handler: Name of a handler method to call on the view instance
        payload: Dict passed as kwargs to the handler method
        scope: Reach only the sessions whose view set ``push_scope`` to this
            value (a room, a document id), instead of every session of the
            view (#3004). ``None`` (the default) reaches every session.

    Raises:
        ValueError: If view_path is not a valid dotted Python path, or scope
            is an empty string.
        TypeError: If scope is not a str or an int.

    Example::

        from djust import push_to_view

        # From a Celery task
        @shared_task
        def refresh_dashboard(new_count):
            push_to_view("myapp.views.DashboardView", state={"count": new_count})

        # Call a handler
        push_to_view("myapp.views.ChatView", handler="on_new_message",
                      payload={"text": "hello"})

        # Only the sessions in one room (their view set push_scope = room)
        push_to_view("games.views.RoomView", handler="handle_refresh",
                     scope="room-42")
    """
    group = _push_group(view_path, scope)
    channel_layer = get_channel_layer()
    message = {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
        # Originating session's channel (#1677), if pushed from within an event
        # handler — lets that session skip its redundant self-broadcast.
        "sender_channel": origin_channel.get(),
    }
    async_to_sync(channel_layer.group_send)(group, message)


async def apush_to_view(
    view_path: str,
    *,
    state: Optional[dict[str, Any]] = None,
    handler: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    scope: Optional[PushScope] = None,
) -> None:
    """
    Async version of :func:`push_to_view`.

    Use from async contexts (async views, async Celery tasks, etc.).
    ``scope`` works as in :func:`push_to_view`.

    Raises:
        ValueError: If view_path is not a valid dotted Python path, or scope
            is an empty string.
        TypeError: If scope is not a str or an int.
    """
    group = _push_group(view_path, scope)
    channel_layer = get_channel_layer()
    message = {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
        # Originating session's channel (#1677), if pushed from within an event
        # handler — lets that session skip its redundant self-broadcast.
        "sender_channel": origin_channel.get(),
    }
    await channel_layer.group_send(group, message)
