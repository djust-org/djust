"""
Server-push API for djust LiveView.

Allows background tasks (Celery, management commands, cron jobs) to push
state updates to connected LiveView clients.
"""

import contextvars
import hashlib
import json
import logging
import re
import sys
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


def push_scope_group_name(view_path: str, scope: PushScope, tenant_scope: str = "") -> str:
    """Return the channel-layer group for ONE scope of a view (#3004).

    Sessions of ``view_path`` whose view sets ``push_scope`` to ``scope`` are
    in this group, and ``push_to_view(view_path, scope=scope)`` sends to it.
    The name is a digest of the view path and the scope, so any scope string
    maps to a valid group name (Channels allows only ``[A-Za-z0-9_.-]``, under
    100 characters) and two different scopes never share a group.

    ``tenant_scope`` (``"tenant:<id>"``, see ``djust._tenant_state``) puts the
    group in that tenant's namespace: two tenants that use the same scope
    string (a room named ``general``, a document id ``7``) do not share a
    group. ``""`` (a view that is not tenant-scoped) keeps the unqualified
    name. The tenant-qualified name is a different family (``djust_tscope_``),
    hashed over a JSON array, so no scope string, whatever it contains, can
    spell another tenant's name.
    """
    key = _scope_key(scope)
    if tenant_scope:
        basis = json.dumps([view_path, tenant_scope, key])
        return f"djust_tscope_{hashlib.sha256(basis.encode()).hexdigest()[:40]}"
    digest = hashlib.sha256(f"{view_path}\x00{key}".encode()).hexdigest()[:40]
    return f"djust_scope_{digest}"


def _is_tenant_scoped(obj: Any) -> bool:
    """Whether a view (or view class) is tenant-scoped: it has the
    ``get_state_key_prefix`` hook ``TenantMixin`` provides, the test the saved
    state and the snapshot token use (:mod:`djust._tenant_state`)."""
    return callable(getattr(obj, "get_state_key_prefix", None))


def view_tenant_scope(view: Any) -> Optional[str]:
    """The tenant namespace of ``view``'s scoped-push groups.

    ``""`` for a view that is not tenant-scoped, ``"tenant:<id>"`` for a
    tenant view with a resolved tenant, ``None`` for a tenant view with none:
    it joins no scoped group, as it reads and writes no saved state.

    The namespace is derived from the tenant's id, the one derivation the
    publishing side (``push_to_view(tenant=...)``) also uses, not from
    ``get_state_key_prefix()``: an override of that hook changes the scope of
    saved state and must not move the view out of the groups a publisher
    names. Reading an attribute also keeps application code off the event
    loop, where this runs after every turn.
    """
    if not _is_tenant_scoped(view):
        return ""
    tenant_id = getattr(getattr(view, "_tenant", None), "id", None)
    if tenant_id is None:
        return None
    from ._tenant_state import tenant_scope_for_id

    return tenant_scope_for_id(tenant_id)


class _AmbientTenant:
    """Default of ``push_to_view(tenant=...)``: the tenant bound to this context."""

    def __repr__(self) -> str:
        return "AMBIENT_TENANT"


AMBIENT_TENANT: Any = _AmbientTenant()


def _target_is_tenant_scoped(view_path: str) -> Optional[bool]:
    """Whether the view class at ``view_path`` is tenant-scoped, or ``None``
    when this process has not imported it (a Celery worker, a management
    command). Never imports anything."""
    module, _, name = view_path.rpartition(".")
    loaded = sys.modules.get(module)
    cls = getattr(loaded, name, None) if loaded is not None else None
    return _is_tenant_scoped(cls) if isinstance(cls, type) else None


#: View paths already warned about by :func:`_publish_tenant_scope`.
_WARNED_NO_TENANT: set = set()


def _publish_tenant_scope(view_path: str, tenant: Any) -> str:
    """The tenant namespace a scoped push to ``view_path`` is published into.

    Only a tenant-scoped view has tenant namespaces: a push to a view that is
    known not to be one (``TenantMixin`` is not in its bases) is unqualified
    whichever tenant is bound, so a tenant's handler, or an HTTP view under
    ``TenantMiddleware``, still reaches the plain view's sessions. For a
    tenant-scoped view, or one this process has not imported:

    ``tenant`` is a tenant id, a ``TenantInfo`` (anything with an ``id``),
    ``None`` for "no tenant", or ``AMBIENT_TENANT`` (the default) for the
    tenant bound to the running context: a tenant view's event handler and
    push or NOTIFY turn run with their own tenant bound, so a push they
    publish stays in that tenant without saying so. A context with no tenant
    (a Celery task, a management command) publishes to the unqualified
    groups, which no tenant-scoped session joins; a task that serves a tenant
    passes ``tenant=``. That case is logged once per view path.
    """
    known = _target_is_tenant_scoped(view_path)
    if known is False:
        return ""
    ambient = tenant is AMBIENT_TENANT
    if ambient:
        tenant = None
        # Only if the tenants package is in use: a project without tenants
        # never pays for importing it.
        if "djust.tenants.middleware" in sys.modules:
            from djust.tenants.middleware import get_current_tenant

            tenant = get_current_tenant()
    if tenant is None:
        # Only a push that named no tenant and found none bound is logged: an
        # explicit ``tenant=None`` is the caller saying there is none.
        if ambient and known is not False and view_path not in _WARNED_NO_TENANT:
            _WARNED_NO_TENANT.add(view_path)
            if known:
                logger.warning(
                    "A scoped push to %s has no tenant, so no session of that tenant-scoped "
                    "view receives it; pass tenant= (a tenant id) from code that runs "
                    "outside a tenant's request",
                    view_path,
                )
            else:
                logger.warning(
                    "A scoped push to %s has no tenant, and this process has not imported "
                    "the view: if it is tenant-scoped no session receives the push, pass "
                    "tenant= (a tenant id); if it is not, pass tenant=None",
                    view_path,
                )
        return ""
    from ._tenant_state import tenant_scope_for_id

    return tenant_scope_for_id(getattr(tenant, "id", tenant))


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
    tenant_scope = ""
    try:
        wanted = view_push_scopes(view) if view is not None and view_path else frozenset()
        if wanted:
            try:
                scope_of = view_tenant_scope(view)
            except Exception:  # noqa: BLE001 - app hook; fail closed, as for no tenant
                scope_of = None
            if scope_of is None:
                # A tenant view with no resolved tenant has no tenant
                # namespace to join: it receives no scoped pushes. Once per
                # consumer until it resolves: this runs after every turn.
                if not getattr(consumer, "_push_scope_tenantless_logged", False):
                    logger.warning(
                        "%s has a push_scope but no resolved tenant; it receives no scoped pushes",
                        type(view).__name__,
                    )
                    consumer._push_scope_tenantless_logged = True
                wanted = frozenset()
            else:
                consumer._push_scope_tenantless_logged = False
                tenant_scope = scope_of
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
    wanted_groups = {key: push_scope_group_name(view_path, key, tenant_scope) for key in wanted}
    # A group another view of the socket still names stays joined: the channel
    # is one member of it for every view (#3252). A scope whose group name
    # changed (the view's tenant did) is left and joined again below.
    kept = _groups_of_other_views(consumer)
    for key in sorted(k for k in current if wanted_groups.get(k) != current[k]):
        try:
            if current[key] not in kept:
                await channel_layer.group_discard(current[key], consumer.channel_name)
        except Exception:  # noqa: BLE001 - kept, so the next sync retries the leave
            logger.warning("Error leaving a scoped push group of %s", view_path)
            continue
        del current[key]
    for key in sorted(set(wanted_groups) - set(current)):
        group = wanted_groups[key]
        try:
            await channel_layer.group_add(group, consumer.channel_name)
        except Exception:  # noqa: BLE001 - retried on the next turn
            logger.warning("Error joining a scoped push group of %s", view_path)
            continue
        current[key] = group
    consumer._push_scope_groups = current


def _groups_of_other_views(consumer: Any) -> set:
    from .runtime import _groups_of_other_views as groups

    return groups(consumer)


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

    Every WebSocket session of a ``PresenceMixin`` view whose broadcast is
    scoped (``push_scope`` set, or ``presence_broadcast_scoped = True``)
    joins, whether or not it tracks its own presence (a read-only viewer still
    shows ``online_count``). Other presence views join nothing, so a view that
    does not opt in pays nothing. While the view tracks, the key is the one ``track_presence``
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
        and view._presence_broadcast_is_scoped()
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
                    # Once per view and push_scope value on this connection:
                    # this runs after every turn.
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
            if current not in _groups_of_other_views(consumer):
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
    if hasattr(view, "online_count"):
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
                    "group": wanted,
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


def _push_group(view_path: str, scope: Optional[PushScope], tenant: Any = AMBIENT_TENANT) -> str:
    if not _VIEW_PATH_RE.match(view_path):
        raise ValueError(
            f"Invalid view_path: {view_path!r}. Expected dotted Python path like 'myapp.views.MyView'"
        )
    if scope is None:
        return view_group_name(view_path)
    return push_scope_group_name(view_path, scope, _publish_tenant_scope(view_path, tenant))


def _server_push_message(
    state: Optional[dict[str, Any]],
    handler: Optional[str],
    payload: Optional[dict[str, Any]],
    group: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
        # The group this push was sent to. A socket that hosts several views
        # delivers the push to the views that joined it (#3252).
        "group": group,
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
    async_to_sync(channel_layer.group_send)(
        group, _server_push_message(None, handler, payload, group)
    )


def push_to_view(
    view_path: str,
    *,
    state: Optional[dict[str, Any]] = None,
    handler: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    scope: Optional[PushScope] = None,
    tenant: Any = AMBIENT_TENANT,
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
        tenant: With ``scope``, the tenant whose sessions it reaches: a tenant
            id or a ``TenantInfo``. A scope belongs to one tenant, so two
            tenants that use the same room name do not share it. The default
            is the tenant bound to the running context (an event handler of a
            ``TenantMixin`` view has its own); pass ``tenant=`` from a task
            that has none (Celery, a management command). ``None`` means no
            tenant: only views that are not tenant-scoped receive it. It only
            matters for a view that uses ``TenantMixin``: a push to a view
            class that is not tenant-scoped is unqualified whatever tenant is
            bound (when the class is imported in the publishing process; from
            one that has not imported it, pass ``tenant=None``). Ignored
            without ``scope``.

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
    group = _push_group(view_path, scope, tenant)
    channel_layer = get_channel_layer()
    message = _server_push_message(state, handler, payload, group)
    async_to_sync(channel_layer.group_send)(group, message)


async def apush_to_view(
    view_path: str,
    *,
    state: Optional[dict[str, Any]] = None,
    handler: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    scope: Optional[PushScope] = None,
    tenant: Any = AMBIENT_TENANT,
) -> None:
    """
    Async version of :func:`push_to_view`.

    Use from async contexts (async views, async Celery tasks, etc.).
    ``scope`` and ``tenant`` work as in :func:`push_to_view`.

    Raises:
        ValueError: If view_path is not a valid dotted Python path, or scope
            is an empty string.
        TypeError: If scope is not a str or an int.
    """
    group = _push_group(view_path, scope, tenant)
    channel_layer = get_channel_layer()
    message = _server_push_message(state, handler, payload, group)
    await channel_layer.group_send(group, message)
