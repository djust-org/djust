"""
Server-push API for djust LiveView.

Allows background tasks (Celery, management commands, cron jobs) to push
state updates to connected LiveView clients.
"""

import contextvars
import hashlib
import logging
import re
from typing import Any, FrozenSet, Optional, Union

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

#: The most scopes one session may be in: every scope is a channel-layer group
#: membership (a join per scope, and a key per scope with the Redis layer).
MAX_PUSH_SCOPES = 64

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


def presence_scope_group_name(view_path: str, presence_key: str) -> str:
    """Return the channel-layer group of the sessions of ``view_path`` that
    share one presence key (#3095).

    ``PresenceMixin`` puts every WebSocket session of a presence view in the
    group of its presence key, and sends a scoped presence-change broadcast
    there, so a join or leave wakes only the sessions that count the same
    presence. A separate namespace from :func:`push_scope_group_name`, so no
    ``push_scope`` value can collide with it.
    """
    digest = hashlib.sha256(f"{view_path}\x00presence\x00{presence_key}".encode()).hexdigest()[:40]
    return f"djust_pscope_{digest}"


def view_push_scopes(view: Any) -> FrozenSet[str]:
    """The scope keys a view instance asks to receive scoped pushes for.

    Reads ``view.push_scope``: ``None`` (the default) means none; a str or an
    int is one scope; a list, tuple or set is several (at most
    ``MAX_PUSH_SCOPES``). An invalid value raises ``TypeError`` /
    ``ValueError``.
    """
    raw = getattr(view, "push_scope", None)
    if raw is None:
        return frozenset()
    if isinstance(raw, (str, int)):
        return frozenset((_scope_key(raw),))
    # A list, tuple or set -- NOT any iterable: a generator or ``map`` would
    # be used up by the first sync, and the next one would leave every group.
    if isinstance(raw, (list, tuple, set, frozenset)):
        if len(raw) > MAX_PUSH_SCOPES:
            raise ValueError(
                f"push_scope has {len(raw)} scopes; at most {MAX_PUSH_SCOPES} are allowed"
            )
        return frozenset(_scope_key(item) for item in raw)
    raise TypeError(
        "push_scope must be None, a str, an int, or a list, tuple or set of them; "
        f"got {type(raw).__name__}"
    )


async def sync_push_scope_groups(consumer: Any, view: Any) -> None:
    """Make ``consumer``'s scoped-push group membership match ``view.push_scope``.

    Joins the group of every scope the view now has and leaves the groups of
    scopes it dropped, so a view can move between scopes (a player changing
    rooms) by assigning ``self.push_scope``. Idempotent; called by the
    WebSocket transport after mount and after every event, server-push, tick
    and ``handle_info`` turn, while the turn still holds the render lock. An
    invalid ``push_scope`` is logged (once, until it is valid again) and
    treated as "no scopes"; a failed join or leave is logged and retried on
    the next call.
    """
    view_path = getattr(consumer, "_view_path", None) or ""
    joined: dict = getattr(consumer, "_push_scope_groups", None) or {}
    try:
        wanted = view_push_scopes(view) if view is not None and view_path else frozenset()
    except (TypeError, ValueError):
        # Once per consumer until the value becomes valid again: this runs
        # after every tick, and a tick can be every few milliseconds.
        if not getattr(consumer, "_push_scope_invalid_logged", False):
            logger.warning(
                "%s.push_scope is invalid (a str, an int, a list, tuple or set of at "
                "most %d of them, or None); it receives no scoped pushes",
                type(view).__name__,
                MAX_PUSH_SCOPES,
            )
            consumer._push_scope_invalid_logged = True
        wanted = frozenset()
    else:
        consumer._push_scope_invalid_logged = False
    channel_layer = getattr(consumer, "channel_layer", None)
    if channel_layer is not None:
        await _sync_presence_scope_group(consumer, view, view_path, channel_layer)
    if channel_layer is None or (not wanted and not joined):
        return
    current = dict(joined)
    for key in sorted(set(current) - wanted):
        try:
            await channel_layer.group_discard(current[key], consumer.channel_name)
        except Exception:  # noqa: BLE001 - kept, so the next sync retries the leave
            logger.warning("Error leaving a scoped push group of %s", view_path)
            continue
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


def _presence_probe(view: Any, need_key: bool, need_count: bool) -> tuple:
    """Runs on the session's thread: the view's presence key (application code
    that may touch the database) and whether its ``online_count`` is stale."""
    key = view.get_presence_key() if need_key else None
    stale = False
    if need_count:
        try:
            stale = len(view.list_presences()) != view.online_count
        except Exception:  # noqa: BLE001 - a backend error must not break the turn
            stale = False
    return key, stale


async def _sync_presence_scope_group(
    consumer: Any, view: Any, view_path: str, channel_layer: Any
) -> None:
    """Keep ``consumer`` in the presence-scope group of its view's presence key.

    Every WebSocket session of a ``PresenceMixin`` view joins, whether or not
    it tracks its own presence (a read-only viewer still shows
    ``online_count``), unless the view sets ``presence_broadcast_scoped =
    False``. While the view tracks, the key is the one ``track_presence``
    recorded (``view._presence_scope_key``). Otherwise it is computed on the
    session's own thread, once per view and ``push_scope`` value, so a viewer
    that moves rooms follows. A failed join is logged and retried on the next
    turn.

    When the join is new and the view broadcasts scoped, a peer may have joined
    or left between mount and this join (the view-wide group is joined before
    mount; this one only now). If ``online_count`` no longer matches the
    backend, the session sends itself one ``_on_presence_change``.
    """
    from .presence import PresenceMixin

    current = getattr(consumer, "_presence_scope_group", None)
    wanted: Optional[str] = None
    key: Any = None
    if (
        view is not None
        and view_path
        and isinstance(view, PresenceMixin)
        and getattr(view, "presence_broadcast_scoped", None) is not False
    ):
        if getattr(view, "_presence_tracked", False):
            key = getattr(view, "_presence_scope_key", None)
        if key is None:
            try:
                basis: Any = view_push_scopes(view)
            except (TypeError, ValueError):
                basis = None
            auto = getattr(consumer, "_presence_scope_auto", None)
            if auto is not None and auto[0] == id(view) and auto[1] == basis:
                key = auto[2]
            else:
                from asgiref.sync import sync_to_async

                try:
                    key, _ = await sync_to_async(_presence_probe)(view, True, False)
                except Exception:  # noqa: BLE001 - app code; the view-wide broadcast still works
                    key = None
                if not isinstance(key, str):
                    # Once per view and push_scope value: this runs after every turn.
                    logger.warning(
                        "%s.get_presence_key() failed or returned a non-string; the "
                        "session gets no scoped presence broadcasts",
                        type(view).__name__,
                    )
                    key = None
                consumer._presence_scope_auto = (id(view), basis, key)
        if isinstance(key, str):
            wanted = presence_scope_group_name(view_path, key)
    if wanted == current:
        return
    if current:
        try:
            await channel_layer.group_discard(current, consumer.channel_name)
        except Exception:  # noqa: BLE001 - kept, so the next sync retries the leave
            logger.warning("Error leaving the presence-scope group of %s", view_path)
            return
        consumer._presence_scope_group = None
    if not wanted:
        return
    try:
        await channel_layer.group_add(wanted, consumer.channel_name)
    except Exception:  # noqa: BLE001 - retried on the next turn
        logger.warning("Error joining the presence-scope group of %s", view_path)
        return
    consumer._presence_scope_group = wanted
    if hasattr(view, "online_count") and view._presence_broadcast_is_scoped():
        from asgiref.sync import sync_to_async

        _, stale = await sync_to_async(_presence_probe)(view, False, True)
        if stale:
            # sender_channel None: never skipped as this session's own push.
            await channel_layer.send(
                consumer.channel_name,
                {
                    "type": "server_push",
                    "state": None,
                    "handler": "_on_presence_change",
                    "payload": {},
                    "sender_channel": None,
                },
            )


async def leave_push_scope_groups(consumer: Any) -> None:
    """Leave every scoped-push group ``consumer`` joined (disconnect, redirect),
    and its presence-scope group (#3095)."""
    joined: dict = getattr(consumer, "_push_scope_groups", None) or {}
    consumer._push_scope_groups = {}
    presence_group = getattr(consumer, "_presence_scope_group", None)
    consumer._presence_scope_group = None
    channel_layer = getattr(consumer, "channel_layer", None)
    if channel_layer is None:
        return
    if isinstance(presence_group, str) and presence_group:
        try:
            await channel_layer.group_discard(presence_group, consumer.channel_name)
        except Exception:  # noqa: BLE001 - leaving is best effort
            logger.warning("Error leaving presence-scope group %s", presence_group)
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


def _server_push_message(
    state: Optional[dict[str, Any]],
    handler: Optional[str],
    payload: Optional[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
        # Originating session's channel (#1677), if pushed from within an event
        # handler — lets that session skip its redundant self-broadcast.
        "sender_channel": origin_channel.get(),
    }


def push_to_presence_scope(
    view_path: str,
    presence_key: str,
    *,
    handler: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """Push to the sessions of ``view_path`` that share ``presence_key`` (#3095).

    What ``PresenceMixin`` uses for a scoped presence-change broadcast; see
    :func:`presence_scope_group_name`. Works from sync code only, like
    :func:`push_to_view`.
    """
    if not _VIEW_PATH_RE.match(view_path):
        raise ValueError(
            f"Invalid view_path: {view_path!r}. Expected dotted Python path like 'myapp.views.MyView'"
        )
    group = presence_scope_group_name(view_path, presence_key)
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(group, _server_push_message(None, handler, payload))


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
    message = _server_push_message(state, handler, payload)
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
    message = _server_push_message(state, handler, payload)
    await channel_layer.group_send(group, message)
