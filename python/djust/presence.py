"""
Presence tracking system for djust LiveView.

Allows LiveView instances to track which users are currently viewing a page,
similar to Phoenix LiveView's Presence system.

Example usage:

    class DocumentView(PresenceMixin, LiveView):
        presence_key = "document:{doc_id}"  # Group key

        def mount(self, request, **kwargs):
            self.doc_id = kwargs.get("doc_id")
            # Auto-track this user's presence
            self.track_presence(meta={"name": request.user.username, "color": "#6c63ff"})

        def get_context_data(self):
            ctx = super().get_context_data()
            # Each record is {"id": ..., "joined_at": ..., "meta": {...}} — the
            # caller-supplied meta (name/color/avatar/...) is nested under "meta",
            # NOT flattened onto the record.
            ctx["presences"] = self.list_presences()
            ctx["presence_count"] = self.presence_count()
            return ctx

        def handle_presence_join(self, presence):
            self.push_event("flash", {"message": f"{presence['meta']['name']} joined"})

        def handle_presence_leave(self, presence):
            pass

Template usage:

    <div class="presence-bar">
      {{ presence_count }} users online
      {% for p in presences %}
        <span class="avatar" style="background: {{ p.meta.color }}">{{ p.meta.name.0 }}</span>
      {% endfor %}
    </div>

Connections and users (#3254):

Presence is stored per connection and reported per user. Each mounted presence
view (one browser tab) is one *connection* of its user in the room. A user is
present until their LAST connection leaves or times out, so closing or
navigating one tab never removes the user while another tab is open:

* ``list_presences()`` / ``presence_count()`` have one entry per user;
* ``handle_presence_join`` fires when the user's FIRST connection arrives, and
  ``handle_presence_leave`` when their LAST one leaves;
* each connection has its own heartbeat and its own 60 s timeout, so a dead tab
  is dropped without touching the user's other tabs.

Presence-record shape (every backend):

    {"id": <user_id>, "joined_at": <epoch>, "meta": {<caller-supplied dict>}}

The caller-supplied ``meta`` (passed to ``track_presence(meta=...)``) is nested
under the ``"meta"`` key — access it as ``p.meta.name`` / ``p["meta"]["name"]``,
never ``p.name``.
"""

import contextlib
import logging
import threading
import time
import uuid
import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.core.cache import cache

from ._exposure import uses_legacy_exposure
from .decorators import event_handler
from .push import push_to_presence_scope, push_to_view, view_push_scopes

if TYPE_CHECKING:
    from .backends.base import PresenceBackend

logger = logging.getLogger(__name__)

# Clock ensures scheduled from track_presence on a running loop (see below).
_PENDING_CLOCK_ENSURES: "set[asyncio.Task[Any]]" = set()

# Cache keys
PRESENCE_KEY_PREFIX = "djust_presence"
HEARTBEAT_KEY_PREFIX = "djust_heartbeat"
PRESENCE_GROUP_PREFIX = "djust_presence"

# Timeouts
HEARTBEAT_INTERVAL = 30  # seconds
PRESENCE_TIMEOUT = 60  # seconds - stale if no heartbeat for this long
CLEANUP_INTERVAL = 300  # seconds - cleanup every 5 minutes


def tenant_scoped_presence_key(view: Any, key: str) -> str:
    """Prefix ``key`` with ``tenant:<id>:`` when ``view`` is a resolved TenantMixin view.

    The one place the tenant scope of a presence key is decided (#2973), so
    ``PresenceMixin`` and ``TenantMixin`` agree whichever comes first in the
    MRO. Idempotent: an already-scoped key is returned unchanged, so the two
    overrides can both apply it.
    """
    import sys

    # A view can only be a TenantMixin if the module defining it was imported,
    # so apps without tenants never pay for importing it.
    if "djust.tenants.mixin" not in sys.modules:
        return key
    # Take the class with a normal import, never off the sys.modules entry
    # (#3079): while another thread is still running the module's first
    # import, that entry is a partially initialised module with no
    # ``TenantMixin`` yet. A normal import waits on the module's import lock
    # until the other thread has finished.
    from djust.tenants.mixin import TenantMixin

    if not isinstance(view, TenantMixin):
        return key
    tenant = getattr(view, "_tenant", None)
    if tenant is None:
        return key
    prefix = f"tenant:{tenant.id}:"
    return key if key.startswith(prefix) else prefix + key


class PresenceManager:
    """
    Manages presence state across the application.

    Delegates to the configured presence backend (memory or Redis).
    See ``djust.backends`` for backend implementations.
    """

    @staticmethod
    def _backend() -> "PresenceBackend":
        from djust.backends.registry import get_presence_backend

        return get_presence_backend()

    @staticmethod
    def presence_group_name(presence_key: str) -> str:
        """Get the channels group name for a presence key."""
        return f"{PRESENCE_GROUP_PREFIX}_{presence_key.replace(':', '_').replace('{', '').replace('}', '')}"

    @classmethod
    def per_connection(cls) -> bool:
        """Whether the configured backend stores one record per connection (#3254).

        False for a third-party backend on the old three-method contract. The
        transports hold a replaced view's untrack until after the replacement
        mounts only when this is true: with one record per user the replacement's
        join and the old view's leave would address the same record.
        """
        from djust.backends.base import uses_per_connection

        return uses_per_connection(cls._backend())

    @classmethod
    def join_presence(
        cls,
        presence_key: str,
        user_id: str,
        meta: Dict[str, Any],
        connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a user to a presence group.

        Args:
            presence_key: The presence group identifier
            user_id: Unique identifier for the user
            meta: Metadata about the user (name, color, etc.)
            connection_id: Which of the user's connections this is (#3254). The
                user stays present until every connection has left. Omitted,
                it is the user's one legacy connection: the user as a whole.

        Returns:
            The user's presence record
        """
        backend = cls._backend()
        if connection_id is None:
            return backend.join(presence_key, user_id, meta)
        return backend.join_connection(presence_key, user_id, connection_id, meta)[0]

    @classmethod
    def join_connection(
        cls, presence_key: str, user_id: str, connection_id: str, meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        """Add one connection of a user (#3254).

        Returns ``(record, first)``: the user's presence record and whether this
        connection made the user present, which is when ``handle_presence_join``
        fires.
        """
        return cls._backend().join_connection(presence_key, user_id, connection_id, meta)

    @classmethod
    def leave_presence(
        cls, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Remove a user, or one of their connections, from a presence group.

        Args:
            presence_key: The presence group identifier
            user_id: Unique identifier for the user
            connection_id: The connection to remove (#3254). Omitted, every
                connection of the user is removed.

        Returns:
            The user's presence record when they are no longer in the group,
            or None if they were not found or are still present through
            another connection
        """
        backend = cls._backend()
        if connection_id is None:
            return backend.leave(presence_key, user_id)
        return backend.leave_connection(presence_key, user_id, connection_id)

    @classmethod
    def leave_connection(
        cls, presence_key: str, user_id: str, connection_id: str
    ) -> Optional[Dict[str, Any]]:
        """Remove one connection of a user (#3254).

        Returns the user's record when that was their last connection, which is
        when ``handle_presence_leave`` fires, and None otherwise.
        """
        return cls._backend().leave_connection(presence_key, user_id, connection_id)

    @classmethod
    def list_presences(cls, presence_key: str) -> List[Dict[str, Any]]:
        """
        Get all active presences for a group.

        Args:
            presence_key: The presence group identifier

        Returns:
            List of presence records, one per user
        """
        return cls._backend().list(presence_key)

    @classmethod
    def presence_count(cls, presence_key: str) -> int:
        """Get the count of active users in a presence group."""
        return cls._backend().count(presence_key)

    @classmethod
    def update_heartbeat(
        cls, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> None:
        """Update the heartbeat timestamp for a user's connection (#3254).

        Without ``connection_id`` every connection of the user is refreshed.
        """
        backend = cls._backend()
        if connection_id is None:
            backend.heartbeat(presence_key, user_id)
        else:
            backend.heartbeat_connection(presence_key, user_id, connection_id)


class PresenceMixin:
    """
    Mixin that provides presence tracking capabilities to LiveView.

    Usage:
        class MyView(PresenceMixin, LiveView):
            presence_key = "my_view:{id}"  # Define the presence group

            def mount(self, request, **kwargs):
                self.track_presence(meta={"name": request.user.username})

    List the mixin BEFORE ``LiveView``. ``__init__`` below sets the presence
    state, and Django's ``View.__init__`` does not call ``super().__init__()``,
    so a mixin listed after ``LiveView`` never initialises. Such a class is
    refused with a ``TypeError`` when it is defined (#3109).
    """

    room_clock: Any = None  # Opt-in ADR-042 presence-bound process clock
    presence_key: Optional[str] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from django.views import View

        mro = cls.__mro__
        if View in mro and mro.index(View) < mro.index(PresenceMixin):
            mixin = next(
                c for c in mro if issubclass(c, PresenceMixin) and c.__module__ == __name__
            )
            host = next(c for c in mro if issubclass(c, View) and c is not cls)
            raise TypeError(
                "%s lists %s after %s. Django's View.__init__ does not call "
                "super().__init__(), so %s.__init__ never runs and presence "
                "state is missing. Put the mixin first: class %s(%s, %s)."
                % (
                    cls.__name__,
                    mixin.__name__,
                    host.__name__,
                    mixin.__name__,
                    cls.__name__,
                    mixin.__name__,
                    host.__name__,
                )
            )

    # When True, anonymous users get a per-WebSocket-connection unique id
    # (``anon_conn_<ws_session_id>``) instead of one collapsing across tabs of
    # the same browser session. Authenticated users are unaffected — they
    # always collapse to ``str(user.id)`` so multi-tab counts as a single
    # presence. See issue #1613.
    presence_unique_per_connection: bool = False

    # Who a join or leave wakes (#3095). The broadcast calls
    # ``_on_presence_change`` on other sessions so they refresh
    # ``online_count``; only the sessions that share this session's presence
    # key can see a different count.
    #
    # - ``None`` (default): only those sessions when the view sets a
    #   ``push_scope`` (it has opted in to scoped delivery), otherwise every
    #   session of the view, as before.
    # - ``True``: only the sessions that share the presence key.
    # - ``False``: every session of the view in every room (the 1.2 behaviour,
    #   for an ``_on_presence_change`` override that reacts to other keys).
    presence_broadcast_scoped: Optional[bool] = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # The presence key whose sessions a scoped broadcast reaches, and whose
        # presence-scope group this session's WebSocket joins (#3095). Set by
        # track_presence / _restore_presence; for a session that never tracks,
        # the WebSocket transport fills it in once after mount.
        self._presence_scope_key: Optional[str] = None
        self._presence_tracked = False
        self._presence_user_id: Optional[str] = None
        self._presence_meta: Optional[Dict[str, Any]] = None
        # Which of the user's connections this view is (#3254). One per
        # tracking view, minted by ``track_presence`` / ``_restore_presence``.
        self._presence_connection_id: Optional[str] = None

    def _new_presence_connection_id(self) -> str:
        """A fresh id for this view's presence connection (#3254).

        Per view, not per socket: the transport session id leads it (for
        diagnostics) and a random suffix makes it unique. A replacement view on
        the same socket (navigation) and a reconnect's view (SSE reuses its
        session id) each get their own, so tearing down the old view can only
        ever remove the old view's connection.
        """
        transport = getattr(self, "_websocket_session_id", None) or "view"
        return f"{transport}:{uuid.uuid4().hex[:12]}"

    def _refresh_online_count(self) -> None:
        """Recompute ``self.online_count`` from the backend.

        Set as an instance attribute (not a method or property) so djust's
        diff dirty-tracking emits a patch when the value changes. Templates
        can reference ``{{ online_count }}`` with zero scaffolding (#1611).

        Defensive: if ``presence_key`` is unset or the backend raises, leaves
        ``online_count`` at its current value (or 0 if never set).
        """
        try:
            self.online_count = len(self.list_presences())
        except Exception as exc:  # noqa: BLE001 — backend errors must not break track/untrack
            logger.debug("PresenceMixin._refresh_online_count: %s", exc)
            self.online_count = getattr(self, "online_count", 0)

    def _presence_broadcast_is_scoped(self) -> bool:
        """Whether a join or leave wakes only the sessions sharing the presence
        key (#3095). See ``presence_broadcast_scoped``."""
        flag = getattr(self, "presence_broadcast_scoped", None)
        if flag is not None:
            return bool(flag)
        try:
            return bool(view_push_scopes(self))
        except (TypeError, ValueError):
            # An invalid push_scope gets no scoped pushes (push.py logs it);
            # keep the presence broadcast view-wide so nobody misses a change.
            return False

    def _broadcast_presence_change(self) -> None:
        """Push ``_on_presence_change`` to the peer sessions of this view.

        Scoped (#3095, see ``presence_broadcast_scoped``): to the sessions
        whose presence key is this session's key. Otherwise: to every active
        session of this view class.

        Failures are swallowed (logged at debug) so a misconfigured channel
        layer, an invalid view-path regex (test-local class paths often fail
        the ``_VIEW_PATH_RE`` check), or any other transient backend error
        never breaks ``track_presence`` / ``untrack_presence`` (#1614).
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            from asgiref.sync import sync_to_async

            # The sync push API bridges group_send back to this serving loop.
            # Execute that bridge in a worker when track runs in an async event.
            loop.create_task(sync_to_async(self._broadcast_presence_change)())
            return

        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        try:
            if self._presence_broadcast_is_scoped():
                key = getattr(self, "_presence_scope_key", None)
                if key is None:
                    key = self.get_presence_key()
                push_to_presence_scope(view_path, key, handler="_on_presence_change", payload={})
            else:
                push_to_view(view_path, handler="_on_presence_change", payload={})
        except Exception as exc:  # noqa: BLE001 — broadcast must never kill track/untrack
            logger.debug("PresenceMixin._broadcast_presence_change: push_to_view failed: %s", exc)

    def get_presence_key(self) -> str:
        """
        Get the presence key for this view instance.

        Override this method for dynamic presence keys, or set the class attribute.
        The key can contain format variables that will be resolved from view attributes.

        Example:
            presence_key = "document:{doc_id}"

        Returns:
            The formatted presence key
        """
        if not self.presence_key:
            # Default to view class path
            module = self.__class__.__module__
            name = self.__class__.__name__
            return tenant_scoped_presence_key(self, f"{module}.{name}")

        # Format the presence key with view attributes
        try:
            key = self.presence_key.format(**self.__dict__)
        except KeyError as e:
            logger.warning("Presence key format error: %s. Using unformatted key.", e)
            key = self.presence_key
        # #2973: scope by tenant here too, not only in TenantMixin's override —
        # with PresenceMixin listed BEFORE TenantMixin this method wins the MRO
        # and the tenant prefix used to be dropped, sharing presence groups
        # across tenants.
        return tenant_scoped_presence_key(self, key)

    def get_presence_user_id(self) -> str:
        """
        Get the unique user identifier for presence tracking.

        Override this method to customize user identification. The default
        prefers (in order):

        1. Authenticated users → ``str(request.user.id)``. ALWAYS collapses
           across tabs to one presence; ``presence_unique_per_connection``
           does NOT affect authenticated users — multi-tab same-user is a
           single online presence by design. Each tab is still its own
           connection (#3254): the user stays present until the last one
           leaves.
        2. Anonymous + ``presence_unique_per_connection=True`` →
           ``f"anon_conn_{_websocket_session_id}"`` so each tab counts as
           a distinct presence. Falls back to ``f"anon_{id(self)}"`` if
           the WS-session attribute is missing (only happens when the
           #1612 guard didn't catch us first; defensive).
        3. Anonymous (default flag=False) → ``f"anon_{session_key}"``
           (collapses across tabs of the same browser session).
        4. No request/session → ``"unknown_user"``.

        Returns:
            Unique user identifier
        """
        if hasattr(self, "request") and self.request.user.is_authenticated:
            return str(self.request.user.id)

        if getattr(self, "presence_unique_per_connection", False):
            ws_sid = getattr(self, "_websocket_session_id", None)
            if ws_sid:
                return f"anon_conn_{ws_sid}"
            # Defensive fallback — should not normally hit because #1612
            # guard returns early when _websocket_session_id is absent.
            return f"anon_{id(self)}"

        # Fallback to session key for anonymous users. An UNSAVED session has
        # ``session_key is None``, which would make every such session collapse
        # onto the single identity ``anon_None`` — many clients counting as one
        # presence. Reachable from ``LiveViewTestClient`` (whose sessions are
        # deliberately not saved, and which since #2821 takes the WebSocket
        # branch by default, so a view's mount()-time ``track_presence()``
        # really runs). Fall back to the instance id, matching the branch above.
        if hasattr(self, "request") and hasattr(self.request, "session"):
            session_key = self.request.session.session_key
            if session_key:
                return f"anon_{session_key}"
            return f"anon_{id(self)}"

        # Last resort - use a default identifier
        return "unknown_user"

    def track_presence(self, meta: Optional[Dict[str, Any]] = None) -> None:
        """
        Start tracking this user's presence.

        ``meta`` is application output: it is stored in the presence backend,
        returned to every peer by ``list_presences()`` and, with
        :class:`LiveCursorMixin`, rebroadcast to the presence group on every
        cursor move. Put in it only what every peer may see.

        Legacy views also get the authenticated user's ``name`` (username) and
        ``user_id`` filled in when absent. Under ``exposure_policy="explicit"``
        (ADR-038 D-c) nothing is added: only the meta you pass is tracked.

        This view becomes one *connection* of the user in the room (#3254).
        ``handle_presence_join`` fires only when it is the user's first
        connection; a second tab of a user who is already present joins
        silently.

        Args:
            meta: Metadata to associate with the user (name, color, avatar, etc.)
        """
        if self._presence_tracked:
            return

        # #1612 — HTTP-mount guard. The throwaway HTTP-mount view instance
        # does not have ``_websocket_session_id`` set (that attribute is
        # assigned only on the WS-mount path at websocket.py:1797). If we
        # registered presence here, no untrack would fire on instance
        # disposal and an orphan presence record would linger for ~60s
        # until ``PRESENCE_TIMEOUT`` cleanup.
        if not hasattr(self, "_websocket_session_id"):
            logger.debug(
                "PresenceMixin.track_presence: skipping — no _websocket_session_id "
                "(HTTP-mount context). Presence only registers under WebSocket."
            )
            return

        presence_key = self.get_presence_key()
        user_id = self.get_presence_user_id()

        if meta is None:
            meta = {}

        # Add default metadata (legacy only; ADR-038 D-c: peers see meta, so
        # explicit views track exactly what the application passed).
        if (
            uses_legacy_exposure(self)
            and hasattr(self, "request")
            and hasattr(self.request, "user")
            and self.request.user.is_authenticated
        ):
            meta.setdefault("name", self.request.user.username)
            meta.setdefault("user_id", user_id)

        self._presence_user_id = user_id
        self._presence_meta = meta
        self._presence_scope_key = presence_key

        # Join presence as one connection of the user (#3254).
        connection_id = self._new_presence_connection_id()
        presence_data, first_connection = PresenceManager.join_connection(
            presence_key, user_id, connection_id, meta
        )
        self._presence_connection_id = connection_id

        self._presence_tracked = True
        self._ensure_room_clock(presence_key)

        # #1611 — refresh online_count after the backend join so this user's
        # own join is included.
        self._refresh_online_count()

        # #1614 — broadcast to peer sessions of this view class so they
        # refresh their own online_count. Default _on_presence_change
        # handler is exclusively a count refresh (no track_presence call),
        # so the broadcast terminates after one hop.
        self._broadcast_presence_change()

        # Call presence join handler if it exists. It means "this user arrived":
        # a user already present through another connection did not.
        if first_connection and hasattr(self, "handle_presence_join"):
            try:
                self.handle_presence_join(presence_data)
            except Exception as e:
                from ._exposure_diagnostics import log_failure_for

                # handle_presence_join is application code (ADR-038).
                log_failure_for(
                    logger, (self,), e, "Error in handle_presence_join: %s", e, traceback=True
                )

    def _restore_presence(self) -> None:
        """Re-register this view's presence with the process-wide manager.

        Called by the WebSocket consumer's state-restoration path (issue
        #893). When ``mount()`` is skipped because pre-rendered session
        state exists, the restored ``_presence_tracked`` / ``_presence_user_id``
        / ``_presence_meta`` attrs survive the JSON round-trip, but the
        side-effect registration with :class:`PresenceManager` does not
        — it lives in a per-process singleton. This method replays the
        registration so other users see the restored user and so
        ``handle_presence_join`` for this user's own join fires.

        No-op if the view was never tracked, if required attrs are
        missing, or if the backend raises (logged but swallowed —
        restoration must not break the WS).
        """
        if not getattr(self, "_presence_tracked", False):
            return
        user_id = getattr(self, "_presence_user_id", None)
        if not user_id:
            return
        meta = getattr(self, "_presence_meta", None) or {}
        try:
            presence_key = self.get_presence_key()
            # A fresh connection, never the id the saved state carries: that one
            # belongs to the connection the state was saved from, which another
            # live tab of this user may still hold (#3254).
            connection_id = self._new_presence_connection_id()
            PresenceManager.join_connection(presence_key, user_id, connection_id, meta)
            self._presence_connection_id = connection_id
            self._presence_scope_key = presence_key
            self._ensure_room_clock(presence_key)
            # #1611 / #1614 — also refresh local count and broadcast so the
            # reconnected session has online_count set for its first
            # post-restore patch, and peer sessions learn the user came back.
            self._refresh_online_count()
            self._broadcast_presence_change()
        except Exception as exc:  # noqa: BLE001 — restoration must never kill the WS
            logger.warning(
                "PresenceMixin._restore_presence: failed to re-register presence "
                "for user_id=%s (issue #893): %s",
                user_id,
                exc,
            )

    def _ensure_room_clock(self, presence_key: str) -> None:
        """Bind every tracked/restored connection, including silent second tabs.

        Use the actual recorded presence key for liveness. Namespace the clock
        key separately and let its tenant helper add the canonical prefix.
        """
        clock = self.room_clock
        if clock is None:
            return
        key = presence_key
        tenant = getattr(self, "_tenant", None)
        if tenant is not None:
            prefix = f"tenant:{tenant.id}:"
            if key.startswith(prefix):
                key = key[len(prefix) :]
        from .push import view_push_scopes, MAX_PUSH_SCOPES

        scope = clock.scope(self, key)
        scopes = set(view_push_scopes(self))
        previous = getattr(self, "_room_clock_scope", None)
        if previous is not None:
            scopes.discard(previous)
        scopes.add(scope)
        if len(scopes) > MAX_PUSH_SCOPES:
            raise ValueError("room_clock needs one available push scope")
        self.push_scope = scope if len(scopes) == 1 else sorted(scopes)
        self._room_clock_scope = scope
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            clock.ensure(self, key, presence_key=presence_key)
        else:
            # track_presence is synchronous even in async event handlers. Keep
            # the join/count/broadcast path synchronous and observe failures.
            task = loop.create_task(clock.aensure(self, key, presence_key=presence_key))
            # The loop keeps only a weak reference to a task; hold it until done.
            _PENDING_CLOCK_ENSURES.add(task)

            def ensured(task: "asyncio.Task[Any]") -> None:
                _PENDING_CLOCK_ENSURES.discard(task)
                exc = None if task.cancelled() else task.exception()
                if exc is not None:
                    from ._exposure_diagnostics import log_failure_for

                    log_failure_for(
                        logger,
                        (self,),
                        exc,
                        "Presence clock ensure failed",
                        traceback=True,
                    )

            task.add_done_callback(ensured)

    def untrack_presence(self) -> None:
        """Stop tracking this view's presence.

        Removes this view's connection only (#3254). ``handle_presence_leave``
        fires when it was the user's LAST connection; a user who is still open
        in another tab stays present and gets no leave event.
        """
        if not self._presence_tracked:
            return

        presence_key = self.get_presence_key()
        user_id = self._presence_user_id
        connection_id = self._presence_connection_id

        if user_id and connection_id:
            presence_data = PresenceManager.leave_connection(presence_key, user_id, connection_id)

            # Call presence leave handler if it exists
            if presence_data:
                self._on_presence_user_left(presence_key, user_id)
                if hasattr(self, "handle_presence_leave"):
                    try:
                        self.handle_presence_leave(presence_data)
                    except Exception as e:
                        from ._exposure_diagnostics import log_failure_for

                        # handle_presence_leave is application code (ADR-038).
                        log_failure_for(
                            logger,
                            (self,),
                            e,
                            "Error in handle_presence_leave: %s",
                            e,
                            traceback=True,
                        )

        self._presence_tracked = False
        self._presence_user_id = None
        self._presence_meta = None
        self._presence_connection_id = None

        # #1611 / #1614 — refresh local count (now excludes the leaving user
        # unless another tab keeps them present) and broadcast to peer sessions.
        self._refresh_online_count()
        scope = getattr(self, "_room_clock_scope", None)
        if (
            self.room_clock is not None
            and scope is not None
            and getattr(self, "online_count", None) == 0
        ):
            self.room_clock._presence_empty(scope)
        self._broadcast_presence_change()

    def _on_presence_user_left(self, presence_key: str, user_id: str) -> None:
        """Hook: ``user_id``'s last connection left ``presence_key`` (#3254).

        Runs before ``handle_presence_leave``. ``LiveCursorMixin`` drops the
        user's cursor here.
        """

    @event_handler
    def _on_presence_change(self, **kwargs: Any) -> None:
        """Default handler invoked when another session's track/untrack broadcasts.

        Refreshes ``self.online_count`` from the backend. The body MUST NOT
        call ``track_presence`` / ``untrack_presence`` (would create an
        unbounded broadcast loop). Subclasses overriding this method should
        either preserve this invariant or call
        ``super()._on_presence_change(**kwargs)`` to keep the count refresh.

        Decorated with ``@event_handler`` so the WS consumer's handler-name
        gate (``websocket.py:4954``) accepts it — the gate only allows
        ``handle_*``-prefixed names OR explicitly-decorated handlers, and
        the underscore-prefixed name needs the explicit signal (#1614).
        """
        self._refresh_online_count()

    def list_presences(self) -> List[Dict[str, Any]]:
        """Get all active presences for this view's presence group."""
        presence_key = self.get_presence_key()
        return PresenceManager.list_presences(presence_key)

    def presence_count(self) -> int:
        """Get the count of active users in this view's presence group."""
        presence_key = self.get_presence_key()
        return PresenceManager.presence_count(presence_key)

    def update_presence_heartbeat(self) -> None:
        """Update the heartbeat for this view's presence connection (#3254)."""
        if (
            not self._presence_tracked
            or not self._presence_user_id
            or not self._presence_connection_id
        ):
            return

        presence_key = self.get_presence_key()
        PresenceManager.update_heartbeat(
            presence_key, self._presence_user_id, self._presence_connection_id
        )

    def handle_presence_join(self, presence: Dict[str, Any]) -> None:
        """
        Called when a user joins the presence group: when their FIRST
        connection arrives (#3254). A second tab of a user who is already
        present does not call it.

        Override this method to handle presence join events.

        Args:
            presence: The presence record of the user who joined
        """
        pass

    def handle_presence_leave(self, presence: Dict[str, Any]) -> None:
        """
        Called when a user leaves the presence group: when their LAST
        connection leaves (#3254). Closing or navigating away from one of
        several tabs does not call it.

        Override this method to handle presence leave events.

        Args:
            presence: The presence record of the user who left
        """
        pass

    def broadcast_to_presence(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """
        Broadcast an event to all users in the presence group.

        Args:
            event: Event name
            payload: Event payload
        """
        if payload is None:
            payload = {}

        presence_key = self.get_presence_key()
        group_name = PresenceManager.presence_group_name(presence_key)

        channel_layer = get_channel_layer()
        if channel_layer:
            message = {
                "type": "presence_event",
                "event": event,
                "payload": payload,
                # The group it was sent to: a socket that hosts several views
                # forwards it for the views that joined it (#3252).
                "group": group_name,
            }
            async_to_sync(channel_layer.group_send)(group_name, message)


def presence_groups_of(consumer: Any) -> List[str]:
    """Every presence group ``consumer`` has joined (#3202).

    A ``mount_batch`` can join several (one per presence view), so the
    consumer keeps a set in ``_presence_groups``; ``_presence_group`` is the
    most recent one, kept for code that reads the single attribute.
    """
    groups = set(getattr(consumer, "_presence_groups", None) or ())
    single = getattr(consumer, "_presence_group", None)
    if isinstance(single, str) and single:
        groups.add(single)
    return sorted(groups)


async def leave_presence_groups(consumer: Any) -> None:
    """Leave every presence group ``consumer`` joined and forget them (#3202).

    Called on disconnect, on a ``live_redirect`` teardown and when a mount is
    refused after it joined. A failed discard is logged, never raised.
    """
    groups = presence_groups_of(consumer)
    consumer._presence_groups = set()
    consumer._presence_group = None
    channel_layer = getattr(consumer, "channel_layer", None)
    if channel_layer is None:
        return
    for group in groups:
        try:
            await channel_layer.group_discard(group, consumer.channel_name)
        except Exception:  # noqa: BLE001 - leaving is best effort
            logger.warning("Error leaving presence group %s", group)


# Cursor tracking for live cursors (bonus feature)
# Guards the cache get -> modify -> set sequences below within this process
# (#3074): with ``LIVEVIEW_CONFIG["worker_threads"]`` two sessions' cursor
# updates can run at the same time, and one would overwrite the other's.
# Only for the in-process (local-memory) cache: a shared cache (Redis,
# memcached) is last-writer-wins across processes anyway, and holding a
# process-wide lock across its network round trips would queue every cursor
# update in the process behind one another.
_CURSOR_LOCK = threading.Lock()


def _cursor_lock() -> Any:
    from django.core.cache import DEFAULT_CACHE_ALIAS, caches
    from django.core.cache.backends.locmem import LocMemCache

    if isinstance(caches[DEFAULT_CACHE_ALIAS], LocMemCache):
        return _CURSOR_LOCK
    return contextlib.nullcontext()


class CursorTracker:
    """Manages live cursor positions for collaborative features."""

    CURSOR_KEY_PREFIX = "djust_cursors"
    CURSOR_TIMEOUT = 10  # seconds

    @classmethod
    def cursor_cache_key(cls, presence_key: str) -> str:
        """Get cache key for cursor positions."""
        return f"{cls.CURSOR_KEY_PREFIX}:{presence_key}"

    @classmethod
    def update_cursor(
        cls, presence_key: str, user_id: str, x: int, y: int, meta: Optional[Dict[str, Any]] = None
    ) -> None:
        """Update cursor position for a user."""
        cache_key = cls.cursor_cache_key(presence_key)
        with _cursor_lock():
            cursors = cache.get(cache_key, {})
            cursors[user_id] = {
                "x": x,
                "y": y,
                "timestamp": time.time(),
                "meta": meta or {},
            }
            cache.set(cache_key, cursors, timeout=cls.CURSOR_TIMEOUT + 5)

    @classmethod
    def get_cursors(cls, presence_key: str) -> Dict[str, Dict[str, Any]]:
        """Get all active cursor positions."""
        cache_key = cls.cursor_cache_key(presence_key)
        with _cursor_lock():
            cursors = cache.get(cache_key, {})

            # Clean up stale cursors
            now = time.time()
            active_cursors = {}

            for user_id, cursor_data in cursors.items():
                if (now - cursor_data["timestamp"]) < cls.CURSOR_TIMEOUT:
                    active_cursors[user_id] = cursor_data

            # Update cache if we cleaned up stale cursors
            if len(active_cursors) != len(cursors):
                cache.set(cache_key, active_cursors, timeout=cls.CURSOR_TIMEOUT + 5)

        return active_cursors

    @classmethod
    def remove_cursor(cls, presence_key: str, user_id: str) -> None:
        """Remove cursor for a user."""
        cache_key = cls.cursor_cache_key(presence_key)
        with _cursor_lock():
            cursors = cache.get(cache_key, {})
            if user_id in cursors:
                del cursors[user_id]
                cache.set(cache_key, cursors, timeout=cls.CURSOR_TIMEOUT + 5)


class LiveCursorMixin(PresenceMixin):
    """
    Extends PresenceMixin with live cursor tracking capabilities.

    Usage:
        class MyView(LiveCursorMixin, LiveView):
            presence_key = "document:{doc_id}"

            def handle_cursor_move(self, x, y):
                # Called when client sends cursor position
                pass
    """

    def update_cursor_position(self, x: int, y: int) -> None:
        """Update cursor position for this user.

        Broadcasts ``{"user_id", "x", "y", "meta"}`` to every peer in the
        presence group, where ``meta`` is what was passed to
        ``track_presence`` (application output; see its docstring).
        """
        if not self._presence_tracked or not self._presence_user_id:
            return

        presence_key = self.get_presence_key()
        meta = self._presence_meta or {}

        CursorTracker.update_cursor(presence_key, self._presence_user_id, x, y, meta)

        # Broadcast to other users in the presence group
        self.broadcast_to_presence(
            "cursor_move",
            {
                "user_id": self._presence_user_id,
                "x": x,
                "y": y,
                "meta": meta,
            },
        )

    def get_cursors(self) -> Dict[str, Dict[str, Any]]:
        """Get all active cursor positions for this presence group."""
        presence_key = self.get_presence_key()
        return CursorTracker.get_cursors(presence_key)

    def handle_cursor_move(self, x: int, y: int) -> None:
        """
        Handler called when cursor position is received from client.

        Override this method to add custom cursor move logic.

        Args:
            x: X coordinate
            y: Y coordinate
        """
        self.update_cursor_position(x, y)

    def _on_presence_user_left(self, presence_key: str, user_id: str) -> None:
        """Also remove the cursor once the user's last connection has left (#3254)."""
        CursorTracker.remove_cursor(presence_key, user_id)
