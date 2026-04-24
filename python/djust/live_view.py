"""
LiveView base class and decorator for reactive Django views
"""

import io
import json
import logging
import socket
import threading
from typing import Any, Callable, Dict, List, Optional, Union

from django.views import View

from ._context_provider import ContextProviderMixin  # noqa: F401  # re-exported for back-compat
from .serialization import DjangoJSONEncoder  # noqa: F401
from .session_utils import (  # noqa: F401
    DEFAULT_SESSION_TTL,
    cleanup_expired_sessions,
    get_session_stats,
    _jit_serializer_cache,
    _get_model_hash,
    clear_jit_cache,
    Stream,
)

from .mixins import (
    AsyncWorkMixin,
    StreamsMixin,
    StreamingMixin,
    TemplateMixin,
    ComponentMixin,
    JITMixin,
    ContextMixin,
    RustBridgeMixin,
    HandlerMixin,
    RequestMixin,
    PostProcessingMixin,
    ModelBindingMixin,
    PushEventMixin,
    NavigationMixin,
    FlashMixin,
    PageMetadataMixin,
    LayoutMixin,
    WaiterMixin,
    NotificationMixin,
    StickyChildRegistry,
)

# Configure logger
logger = logging.getLogger(__name__)

try:
    from ._rust import (
        RustLiveView,
        SessionActorHandle,
        extract_template_variables,  # noqa: F401 — re-exported, used by JIT and template tests
    )
except ImportError:
    RustLiveView = None
    SessionActorHandle = None
    extract_template_variables = None  # noqa: F401 — fallback for re-export

__all__ = [
    "LiveView",
    "live_view",
    "DjangoJSONEncoder",
    "DEFAULT_SESSION_TTL",
    "cleanup_expired_sessions",
    "get_session_stats",
    "_jit_serializer_cache",
    "_get_model_hash",
    "clear_jit_cache",
    "Stream",
    "extract_template_variables",
]


# Framework-internal attributes that MUST NOT be surfaced as reactive user state
# in get_state(), _snapshot_assigns(), or observability debug payloads (#762).
#
# Three buckets of leakage:
#   1. Django ``View``-inherited (set by ``as_view()``):
#      http_method_names, args, kwargs, response_class, content_type
#   2. djust ``LiveView``/mixin-set config attrs that happen to live on
#      ``self.__dict__`` rather than on the class — without this filter they
#      get mistaken for reactive state.
#   3. Static config surfaced by mixins (PageMetadataMixin, LayoutMixin, etc.).
#
# This is the non-breaking fix: attribute names are unchanged. Renaming these
# to ``_*`` would break every downstream template / integration that reads
# e.g. ``template_name`` from the context.
_FRAMEWORK_INTERNAL_ATTRS: frozenset = frozenset(
    {
        # Django View-inherited (set by as_view())
        "http_method_names",
        "args",
        "kwargs",
        "response_class",
        "content_type",
        # djust LiveView base config
        "sync_safe",
        "use_actors",
        "view_is_async",
        "tick_interval",
        "login_required",
        "login_url",
        "permission_required",
        "allowed_model_fields",
        "on_mount",
        "on_mount_count",
        "static_assigns",
        "static_assigns_count",
        "template",
        "template_name",
        "base_template",
        "page_meta",
        "page_slug",
        "page_title",
        "temporary_assigns",
        "sticky",
        "sticky_id",
    }
)


class LiveView(
    ContextProviderMixin,
    StreamsMixin,
    StreamingMixin,
    TemplateMixin,
    ComponentMixin,
    JITMixin,
    ContextMixin,
    RustBridgeMixin,
    HandlerMixin,
    RequestMixin,
    PostProcessingMixin,
    ModelBindingMixin,
    PushEventMixin,
    NavigationMixin,
    FlashMixin,
    PageMetadataMixin,
    LayoutMixin,
    WaiterMixin,
    AsyncWorkMixin,
    NotificationMixin,
    StickyChildRegistry,
    View,
):
    """
    Base class for reactive LiveView components.

    Usage:
        class CounterView(LiveView):
            template_name = 'counter.html'
            use_actors = True  # Enable actor-based state management (optional)

            def mount(self, request, **kwargs):
                self.count = 0

            def increment(self):
                self.count += 1

            def decrement(self):
                self.count -= 1

    Memory Optimization with temporary_assigns:
        For views with large collections (chat messages, feed items, etc.),
        use temporary_assigns to clear data from server memory after each render.

        class ChatView(LiveView):
            template_name = 'chat.html'
            temporary_assigns = {'messages': []}  # Clear after each render

            def mount(self, request, **kwargs):
                self.messages = Message.objects.all()[:50]

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.messages = [msg]  # Only new messages sent to client

        IMPORTANT: When using temporary_assigns, use dj-update="append" in your
        template to tell the client to append new items instead of replacing:

            <ul dj-update="append" id="messages">
                {% for msg in messages %}
                    <li id="msg-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>

    Streams API (recommended for collections):
        For a more ergonomic API, use streams instead of temporary_assigns:

        class ChatView(LiveView):
            template_name = 'chat.html'

            def mount(self, request, **kwargs):
                self.stream('messages', Message.objects.all()[:50])

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.stream_insert('messages', msg)

        Template:
            <ul dj-stream="messages">
                {% for msg in streams.messages %}
                    <li id="messages-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>

    Background Work with start_async():
        For long-running operations (LLM calls, file processing), use start_async()
        to run work in the background without blocking the UI:

        class SpecGeneratorView(LiveView):
            template_name = 'generator.html'

            def mount(self, request, **kwargs):
                self.generating = False
                self.spec = ""
                self.error = None

            @event_handler
            def generate(self, prompt: str = "", **kwargs):
                self.generating = True  # Show loading state immediately
                self.start_async(self._generate_spec, prompt=prompt, name="generation")

            def _generate_spec(self, prompt: str):
                '''Runs in background thread.'''
                self.spec = call_llm_api(prompt)  # Slow operation
                self.generating = False
                # View auto-re-renders when this completes

            def handle_async_result(self, name: str, result=None, error=None):
                '''Optional: handle completion or errors.'''
                if error:
                    self.error = f"Generation failed: {error}"
                    self.generating = False

            @event_handler
            def cancel_generation(self, **kwargs):
                self.cancel_async("generation")
                self.generating = False

        Or use the @background decorator for simpler syntax:

        class SimpleGeneratorView(LiveView):
            template_name = 'generator.html'

            @event_handler
            @background
            def generate(self, prompt: str = "", **kwargs):
                '''Entire handler runs in background.'''
                self.generating = True
                self.spec = call_llm_api(prompt)
                self.generating = False

        Multiple concurrent tasks are supported with named tasks. Phoenix LiveView users
        will find this API familiar.
    """

    template_name: Optional[str] = None
    template: Optional[str] = None
    use_actors: bool = False  # Enable Tokio actor-based state management (Phase 5+)
    tick_interval: Optional[int] = None  # Periodic tick in ms (e.g. 2000 for 2s)

    # Memory optimization: assigns to clear after each render
    # Format: {'assign_name': default_value, ...}
    # Example: {'messages': [], 'feed_items': [], 'notifications': []}
    temporary_assigns: Dict[str, Any] = {}

    # Render optimization: assigns sent to Rust only on first render.
    # Rust retains them via state merging (update_state extends, not replaces).
    # Use for large, unchanging context (pre-rendered HTML, static config).
    # Pair with dj-update="ignore" on the template element for full optimization.
    static_assigns: List[str] = []

    # Authentication & authorization
    login_required: Optional[bool] = None  # True = must be authenticated
    permission_required: Optional[Union[str, List[str]]] = None  # Django permission string(s)
    login_url: Optional[str] = None  # Override settings.LOGIN_URL

    # on_mount hooks — cross-cutting mount logic (Phoenix on_mount parity)
    on_mount: List[Any] = []

    # HTTP API exposure (ADR-008) — opt-in; see djust.api and the
    # ``docs/website/guides/http-api.md`` guide.
    #
    # ``api_name`` is the stable URL slug under ``/djust/api/<slug>/``. If left
    # ``None``, the slug is derived from the module path + lowercased class name,
    # but the derived slug changes when the class is moved or renamed — set
    # ``api_name`` explicitly for any view with ``expose_api=True`` handlers.
    #
    # ``api_auth_classes`` is a list of auth classes (instances or classes) tried
    # in order; the first one whose ``authenticate(request)`` returns a non-None
    # user wins. CSRF is enforced unless the winning class sets
    # ``csrf_exempt = True``. When ``None``, djust uses ``[SessionAuth]``.
    api_name: Optional[str] = None
    api_auth_classes: Optional[List[Any]] = None

    # Sticky LiveViews (Phase B of v0.6.0 Sticky LiveViews).
    #
    # When ``sticky = True`` and the view is embedded via
    # ``{% live_render "dotted.path" sticky=True %}``, the instance, its
    # DOM subtree, form values, scroll/focus, and background tasks
    # SURVIVE ``live_redirect`` navigations — provided the destination
    # layout contains a matching ``<div dj-sticky-slot="<id>">`` element.
    # ``sticky_id`` is the stable identifier shared between server and
    # client; if left ``None``, the template tag errors at render time
    # because there is no slot key to match.
    sticky: bool = False
    sticky_id: Optional[str] = None

    # State snapshot (v0.6.0 — Service Worker advanced features).
    #
    # Opt-in per-view flag that enables back-navigation state restoration
    # via the client-side Service Worker state cache. When True and the
    # client posts a ``state_snapshot`` payload alongside a
    # ``live_redirect_mount`` (typically from a popstate event on the
    # back button), the server restores public view attributes from the
    # snapshot in lieu of calling ``mount()``. Use
    # :meth:`_should_restore_snapshot` to reject stale snapshots based on
    # auth context, request headers, or freshness.
    #
    # Security: snapshots are JSON only (no pickle). Restore happens
    # AFTER auth checks and uses ``safe_setattr`` to block dunder keys.
    # Never opt in for views whose public attributes contain credentials
    # or PII — system check ``djust.C304`` warns on common PII naming
    # patterns (``password``, ``token``, ``secret``, ``api_key``, ``pii``).
    enable_state_snapshot: bool = False

    # Streaming initial render (v0.6.1 — Phase 1).
    #
    # Opt-in per-view flag that returns a ``StreamingHttpResponse`` from the
    # HTTP GET path instead of an ``HttpResponse``. The response body is
    # flushed in three chunks — shell-open (everything before the
    # ``<div dj-root>``), main content (the ``<div dj-root>...</div>`` block),
    # and shell-close (``</body></html>`` + trailing markup). Browsers can
    # begin parsing the ``<head>`` and loading CSS/JS while the server is
    # still computing the main content — competitive with Next.js
    # ``renderToPipeableStream``.
    #
    # Backward-compatible default (``False``) preserves the existing
    # ``HttpResponse`` path. When ``True``, the response omits the
    # ``Content-Length`` header (HTTP chunked transfer) and sets
    # ``X-Djust-Streaming: 1`` for observability.
    #
    # Caveats: some reverse proxies buffer chunked responses by default;
    # middleware that inspects response bodies must be streaming-aware.
    # Lazy-child streaming (the full Next.js-style partial hydration) is
    # tracked for v0.6.2 as Phase 2.
    streaming_render: bool = False

    # Time-travel debugging (v0.6.1 — dev-only).
    #
    # Opt-in per-view flag that enables a per-instance ring buffer of
    # :class:`djust.time_travel.EventSnapshot` entries — one for every
    # ``@event_handler`` dispatch, capturing ``state_before`` /
    # ``state_after``. The debug panel exposes a "Time Travel" tab that
    # lets developers scrub through history and jump back to any past
    # state; the server restores the snapshot + re-renders.
    #
    # Safe default (``False``) costs zero when disabled. Gated on
    # ``DEBUG=True`` at the WebSocket consumer so enabling it in a
    # release build silently no-ops. See
    # :mod:`djust.time_travel` for the recording machinery.
    time_travel_enabled: bool = False

    # ============================================================================
    # INITIALIZATION & SETUP
    # ============================================================================

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rust_view: Optional[RustLiveView] = None
        self._actor_handle: Optional[SessionActorHandle] = None
        self._session_id: Optional[str] = None
        self._cache_key: Optional[str] = None
        self._handler_metadata: Optional[dict] = None  # Cache for decorator metadata
        self._components: Dict[str, Any] = {}  # Registry of child components by ID
        self._temporary_assigns_initialized: bool = False  # Track if temp assigns are set up
        self._streams: Dict[str, Stream] = {}  # Stream collections
        self._stream_operations: list = []  # Pending stream operations for this render
        # Initialize navigation support (live_patch, live_redirect)
        self._init_navigation()

        # Initialize child-view registry (Phase A of Sticky LiveViews).
        # Required before any {% live_render %} tag tries to register.
        self._init_sticky()

        # Phase B: per-instance stash of sticky children preserved across
        # a live_redirect. Populated by the consumer's
        # ``handle_live_redirect_mount`` flow via
        # :meth:`_preserve_sticky_children` before the old view is torn
        # down. Re-registered onto the new parent once its template
        # surfaces matching ``[dj-sticky-slot]`` elements.
        self._sticky_preserved: Dict[str, Any] = {}

        # Track user-defined _private attr names (populated by
        # _snapshot_user_private_attrs after mount, or _restore_private_state).
        self._user_private_keys: set = set()

        # Time-travel debugging (v0.6.1) — lazy per-instance ring buffer.
        # Allocated only when the subclass opts in via the class attr so
        # the 99% of views that don't use it pay zero memory cost.
        self._time_travel_buffer = None
        if getattr(self.__class__, "time_travel_enabled", False):
            try:
                from djust.time_travel import TimeTravelBuffer
                from djust.config import config as _djust_config

                self._time_travel_buffer = TimeTravelBuffer(
                    max_events=_djust_config.get("time_travel_max_events", 100)
                )
            except Exception:  # noqa: BLE001
                # Never let a misconfigured buffer break __init__.
                logger.exception("time_travel: failed to allocate buffer")
                self._time_travel_buffer = None

        # Snapshot framework-set attrs so we can distinguish them from
        # user-defined _private attrs set in mount() or event handlers.
        self._framework_attrs: frozenset = frozenset(self.__dict__.keys())

    # ============================================================================
    # DIRTY TRACKING — cumulative since mount or last mark_clean() (v0.5.1)
    # ============================================================================

    def _dirty_fingerprint(self) -> Dict[str, Any]:
        """Shallow fingerprint of public assigns for dirty tracking.

        Mirrors the WS consumer's ``_snapshot_assigns`` but scoped to public
        attributes only (no leading underscore), so dirty tracking never
        reports framework-internal changes.
        """
        static_skip = set(getattr(self, "static_assigns", []))
        fp: Dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if k.startswith("_") or k in static_skip:
                continue
            if isinstance(v, (int, float, bool, str, bytes)) or v is None:
                fp[k] = ("v", v)
            elif isinstance(v, (list, tuple)):
                fp[k] = ("seq", id(v), len(v))
            elif isinstance(v, dict):
                fp[k] = ("dict", id(v), len(v), tuple(v.keys())[:16])
            elif isinstance(v, set):
                fp[k] = ("set", id(v), len(v))
            else:
                fp[k] = ("id", id(v))
        return fp

    def _capture_dirty_baseline(self) -> None:
        """Snapshot current public assigns as the dirty-tracking baseline.

        Called once after ``mount()`` completes (by the WS consumer) and again
        whenever the user calls :meth:`mark_clean`.
        """
        self._dirty_baseline = self._dirty_fingerprint()

    def mark_clean(self) -> None:
        """Reset the dirty-tracking baseline to the current state.

        Call this after persisting the view's state (e.g., after a successful
        save handler) so subsequent mutations show up as ``is_dirty``.
        """
        self._capture_dirty_baseline()

    class _FrameworkProperty(property):
        """Marker subclass so ``get_context_data`` can skip framework-derived properties."""

        _djust_framework_derived = True

    @_FrameworkProperty
    def changed_fields(self) -> set:
        """Set of public attribute names that have changed since the baseline.

        The baseline is captured after ``mount()`` completes, and reset by
        :meth:`mark_clean`. Returns an empty set if the baseline hasn't been
        captured yet (e.g., during ``mount()`` itself).
        """
        baseline = getattr(self, "_dirty_baseline", None)
        if baseline is None:
            return set()
        current = self._dirty_fingerprint()
        changed = set()
        for k in set(baseline) | set(current):
            if k not in baseline or k not in current:
                changed.add(k)
            elif baseline[k] != current[k]:
                changed.add(k)
        return changed

    @_FrameworkProperty
    def is_dirty(self) -> bool:
        """True if any public attribute has changed since the baseline.

        Use for "unsaved changes" UI patterns, conditional save buttons, and
        ``beforeunload`` warnings. Combine with :meth:`mark_clean` to reset
        after a successful save.
        """
        return bool(self.changed_fields)

    # ============================================================================
    # STABLE UNIQUE IDs — React 19 useId equivalent (v0.5.1)
    # ============================================================================

    def unique_id(self, suffix: str = "") -> str:
        """Return a deterministic per-view ID stable across renders.

        Each call within the same mount-render cycle returns a new unique ID,
        but the sequence is stable: if the template renders ``unique_id()``
        three times, the same three IDs are generated on every render. Useful
        for ``aria-labelledby``, form field IDs, and any element that needs a
        stable identifier without depending on DOM ordering.

        The ID format is ``djust-<view-slug>-<n>[-<suffix>]`` where ``<n>`` is a
        monotonically incrementing per-call counter that resets at the start of
        each render cycle (``reset_unique_ids()``).
        """
        counter = self.__dict__.setdefault("_djust_id_counter", 0)
        self._djust_id_counter = counter + 1
        slug = getattr(self, "_djust_id_slug", None)
        if slug is None:
            slug = type(self).__name__.lower()
            self._djust_id_slug = slug
        base = f"djust-{slug}-{counter}"
        return f"{base}-{suffix}" if suffix else base

    def reset_unique_ids(self) -> None:
        """Reset the ``unique_id()`` counter — called at the start of each render.

        The WS consumer calls this before invoking ``get_context_data``; tests
        can call it manually to assert stable-across-renders behavior.
        """
        self._djust_id_counter = 0

    # Component context sharing methods are provided by ``ContextProviderMixin``
    # below (declared at module scope) and mixed into both LiveView and
    # LiveComponent so ``provide_context`` / ``consume_context`` work across
    # the full render tree.

    def _snapshot_user_private_attrs(self) -> None:
        """Snapshot current _-prefixed attrs as user-defined private state names.

        Called after mount() completes. Any ``_``-prefixed attr that exists now
        but was NOT present after ``__init__`` is a user-defined private attr.
        Later render-cycle attrs won't be included because they haven't been
        set yet.
        """
        framework = getattr(self, "_framework_attrs", frozenset())
        # Exclude the tracking attrs themselves — they are infrastructure, not
        # user state, and must never leak into the persisted private state.
        meta_attrs = {"_framework_attrs", "_user_private_keys"}
        self._user_private_keys: set = {
            k
            for k in self.__dict__
            if k.startswith("_") and k not in framework and k not in meta_attrs
        }

    def _get_private_state(self) -> Dict[str, Any]:
        """Return serializable user-defined _private attributes (not framework internals).

        Only persists attrs tracked in ``_user_private_keys`` — a set populated
        by ``_snapshot_user_private_attrs()`` (after mount) and
        ``_restore_private_state()``. Event handlers that add NEW private attrs
        should add the name to ``self._user_private_keys`` directly, e.g.
        ``self._user_private_keys.add('_name')``, or the attr will not be
        persisted in subsequent save cycles.

        Non-serializable values (locks, file handles, etc.) are silently skipped.
        """
        result: Dict[str, Any] = {}
        user_keys = getattr(self, "_user_private_keys", set())
        for key in user_keys:
            if key not in self.__dict__:
                continue
            value = self.__dict__[key]
            # Skip callables (bound methods, lambdas stored as attrs)
            if callable(value):
                continue
            # Attempt serialization — skip if not possible
            try:
                json.dumps(value, cls=DjangoJSONEncoder)
                result[key] = value
            except (TypeError, ValueError, OverflowError):
                logger.debug(
                    "Skipping non-serializable private attr %s.%s (%s)",
                    type(self).__name__,
                    key,
                    type(value).__name__,
                )
                continue
        return result

    def _restore_private_state(self, private_state: Dict[str, Any]) -> None:
        """Restore previously-saved private attributes onto this instance."""
        framework = getattr(self, "_framework_attrs", frozenset())
        meta_attrs = {"_framework_attrs", "_user_private_keys"}
        for key, value in private_state.items():
            if key.startswith("_") and key not in framework and key not in meta_attrs:
                setattr(self, key, value)
                # Track restored attrs as user-defined so they persist
                # through subsequent save cycles.
                user_keys = getattr(self, "_user_private_keys", None)
                if user_keys is not None:
                    user_keys.add(key)

    # ============================================================================
    # STATE SNAPSHOT — v0.6.0 back-navigation restoration (opt-in)
    # ============================================================================

    def _capture_snapshot_state(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of public view state.

        Filters out private (``_``-prefixed) attributes, framework-internal
        attrs enumerated in ``_FRAMEWORK_INTERNAL_ATTRS``, callables, and any
        value that fails a ``DjangoJSONEncoder`` round-trip. Used by the
        client to post a ``STATE_SNAPSHOT`` message to the service worker
        when opt-in via :attr:`enable_state_snapshot` is True.

        The server never calls this directly — it's primarily exposed for
        testing and observability. Restoration uses
        :meth:`_restore_snapshot`.
        """
        result: Dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                continue
            if key in _FRAMEWORK_INTERNAL_ATTRS:
                continue
            if callable(value):
                continue
            try:
                # json.dumps serves as the serializability check; the
                # accompanying json.loads round-trips to a *disconnected*
                # value so later in-place mutations to the source object
                # (e.g. ``self.items.append(...)``) do not retroactively
                # rewrite this snapshot. Without the round-trip the
                # time-travel state_before / state_after fields aliased
                # the live view attrs — see Stage 11 Fix B. The v0.6.0
                # back-navigation state snapshot benefits from this same
                # fix: previously a mutable public attr captured here
                # could be mutated before the snapshot was serialized
                # and sent to the client.
                result[key] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
            except (TypeError, ValueError, OverflowError):
                # Skip non-serializable — matches _get_private_state pattern.
                continue
        return result

    def _restore_snapshot(self, state: Dict[str, Any]) -> None:
        """Apply a previously captured snapshot to this view.

        Default implementation iterates the dict and calls ``safe_setattr``
        for every key, refusing to set dunder attributes or anything that
        fails the ``SAFE_ATTRIBUTE_PATTERN`` regex. Subclasses can override
        to implement custom restoration logic (e.g. re-hydrating ORM
        instances from pk, re-fetching cached data).

        The state is the JSON-decoded payload from the client — treat it
        as untrusted and never pass it to ``exec``/``eval`` or raw
        ``setattr``.
        """
        from .security import safe_setattr

        for key, value in state.items():
            safe_setattr(self, key, value, allow_private=False)

    def _should_restore_snapshot(self, request) -> bool:
        """Return True to allow snapshot restoration for this request.

        Default implementation returns True — the class-level
        :attr:`enable_state_snapshot` flag already gates opt-in. Override
        to reject stale snapshots on permission changes, feature-flag
        toggles, or time-based TTLs (e.g. refuse snapshots older than one
        hour by inspecting a ``_snapshot_ts`` attr).

        Runs AFTER Django auth / ``check_view_auth`` — a returning user
        whose permissions were revoked will already have been redirected
        away before this hook fires.
        """
        return True

    def handle_tick(self):
        """Override for periodic server-side updates. Called every tick_interval ms."""
        pass

    # ============================================================================
    # STATE SERIALIZATION VALIDATION
    # ============================================================================

    @staticmethod
    def _is_serializable(value: Any) -> bool:
        """Check if a value can be safely serialized to JSON for state transfer.

        Returns True for primitives, collections, Django models/QuerySets, and
        any value that json.dumps can handle. Returns False for service instances,
        connections, file handles, threads, and other non-serializable objects.
        """
        # Primitives are always fine
        if value is None or isinstance(value, (str, int, float, bool)):
            return True

        # Collections: check recursively would be expensive; allow them and
        # let actual serialization catch nested issues
        if isinstance(value, (list, tuple, set, frozenset)):
            return True

        if isinstance(value, dict):
            return True

        # Django models and QuerySets are serialized by JIT pipeline
        try:
            from django.db import models
            from django.db.models import QuerySet

            if isinstance(value, (models.Model, QuerySet)):
                return True
        except ImportError:
            pass  # Django ORM not available; skip model/queryset check

        # Non-serializable types: file handles, threads, locks, sockets
        _non_serializable = (io.IOBase, threading.Thread, socket.socket)
        try:
            # threading.Lock() returns _thread.lock which isn't directly a type
            import _thread

            _non_serializable = _non_serializable + (_thread.LockType,)
        except (ImportError, AttributeError):
            pass  # _thread.LockType unavailable on some platforms; skip lock detection
        if isinstance(value, _non_serializable):
            return False

        # Detect common service/client patterns by type name
        type_name = type(value).__name__.lower()
        _suspect_names = ("service", "client", "session", "connection", "api")
        if any(name in type_name for name in _suspect_names):
            return False

        # Detect objects with generic repr like '<ClassName object at 0x...>'
        try:
            obj_repr = repr(value)
            if " object at 0x" in obj_repr:
                return False
        except Exception:
            return False

        # Final check: try to actually serialize it
        try:
            json.dumps(value, cls=DjangoJSONEncoder)
            return True
        except (TypeError, ValueError, OverflowError):
            return False

    def get_state(self) -> Dict[str, Any]:
        """Get serializable state from this LiveView instance.

        Iterates over public (non-underscore) instance attributes and validates
        that each value can be serialized. In DEBUG mode, raises TypeError with
        a helpful message for non-serializable values. In production, logs an
        error and skips the attribute.

        Returns:
            Dictionary of {attribute_name: value} for all serializable public state.
        """
        from django.conf import settings

        state = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                continue

            # #762: Skip framework-internal attrs so they don't pollute
            # user-facing reactive state.
            if key in _FRAMEWORK_INTERNAL_ATTRS:
                continue

            if callable(value):
                continue

            if not self._is_serializable(value):
                class_name = self.__class__.__name__
                value_type = type(value).__name__
                msg = (
                    f"Non-serializable value in {class_name}.{key}: "
                    f"{value_type} cannot be stored in LiveView state. "
                    f"Service instances, connections, and file handles must "
                    f"be created in event handlers or accessed via utility "
                    f"functions — not stored as instance attributes. "
                    f"See: https://djust.org/docs/guides/services.md"
                )
                if getattr(settings, "DEBUG", False):
                    raise TypeError(msg)
                else:
                    logger.error(msg)
                    continue

            state[key] = value

        return state

    # ============================================================================
    # TEMPORARY ASSIGNS - Memory optimization for large collections
    # ============================================================================

    def _reset_temporary_assigns(self) -> None:
        """
        Reset temporary assigns to their default values after rendering.

        Called automatically after each render to free memory for large collections.
        """
        if not self.temporary_assigns:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if hasattr(self, assign_name):
                # Reset to default value (make a copy to avoid sharing state)
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

                logger.debug(
                    f"[LiveView] Reset temporary assign '{assign_name}' to {type(default_value).__name__}"
                )

        # Also reset streams
        self._reset_streams()

    def _initialize_temporary_assigns(self) -> None:
        """Initialize temporary assigns with their default values on first mount."""
        if self._temporary_assigns_initialized:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if not hasattr(self, assign_name):
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

        self._temporary_assigns_initialized = True


def live_view(template_name: Optional[str] = None, template: Optional[str] = None):
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            def decrement():
                nonlocal count
                count -= 1

            return locals()

    Args:
        template_name: Path to Django template
        template: Inline template string

    Returns:
        View function
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(request, *args, **kwargs):
            # Create a dynamic LiveView class
            class DynamicLiveView(LiveView):
                pass

            if template_name:
                DynamicLiveView.template_name = template_name
            if template:
                DynamicLiveView.template = template

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                for key, value in result.items():
                    if not callable(value):
                        setattr(view, key, value)
                    else:
                        setattr(view, key, value)

            # Handle the request
            if request.method == "GET":
                return view.get(request, *args, **kwargs)
            elif request.method == "POST":
                return view.post(request, *args, **kwargs)

            return None

        return wrapper

    return decorator
