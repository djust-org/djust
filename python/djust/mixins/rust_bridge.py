"""
RustBridgeMixin - Rust backend integration for LiveView.
"""

import hashlib
import logging
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Union
from urllib.parse import parse_qs, urlencode

from ..security import sanitize_for_log
from ..serialization import normalize_django_value
from ..utils import get_template_dirs
from .context import _is_json_serializable

logger = logging.getLogger(__name__)


# #1353: Concurrent same-session HTTP renders previously panicked with
# ``RuntimeError: Already borrowed`` because the in-memory state backend
# returned the SAME Python object on cache hits, so two threads both
# called ``&mut self`` Rust methods (``update_state``, ``render``,
# ``set_template_dirs``, etc.) on the shared ``RustLiveView`` and
# collided inside Rust's ``RefCell::borrow_mut``. The race window
# spanned more than the ``_sync_state_to_rust`` mutation calls:
# ``render()`` itself holds ``&mut self`` across template evaluation
# that yields the GIL via ``Context::resolve_dotted_via_getattr``
# (``crates/djust_core/src/context.rs``), so a peer thread entering any
# ``&mut self`` method during that window panicked.
#
# Resolution: ``InMemoryStateBackend.get()`` now returns an isolated
# clone of the cached view (``serialize_msgpack`` →
# ``deserialize_msgpack``) — mirroring how ``RedisStateBackend.get``
# already behaves. With each caller holding its own ``RustLiveView``
# instance, no two threads can share a Rust ``&mut self`` borrow and
# the race class is eliminated at the source. No Python-side lock is
# needed anymore.


# Keys excluded from set_changed_keys — these are framework-internal values
# that change id() every render but don't affect template output.
_FRAMEWORK_KEYS = frozenset(
    {
        "csrf_token",
        "kwargs",
        "temporary_assigns",
        "DATE_FORMAT",
        "TIME_FORMAT",
    }
)

# Immutable types for id() comparison filtering — these never need
# id() checks since value equality is sufficient and Python's int
# cache makes id() unreliable for small ints across calls.
_IMMUTABLE_TYPES_FOR_SYNC = (int, float, bool, str, bytes, type(None))

# Sentinel for "key not in previous context" in value-equality comparisons.
# Using a unique object ensures `prev.get(k, _MISSING) != value` returns
# True when the key was absent, even when value is None/0/"" (which would
# collide with a default-None fallback).
_MISSING = object()

try:
    from .._rust import RustLiveView
except ImportError:
    RustLiveView = None  # type: ignore[assignment,misc]


# Process-level guard for the custom-filter bootstrap (#1121). Set after
# the first successful walk of Django's filter libraries. Re-bootstrapping
# is idempotent (re-registering an existing name overwrites in the Rust
# registry), but the guard skips the walk on every subsequent
# ``_initialize_rust_view`` call so steady-state cost is one branch.
_CUSTOM_FILTERS_BRIDGED = False


# Maximum recursion depth for ``_normalize_db_values``. Bounded to keep the
# normalize pass O(N * depth) instead of unbounded for pathological nesting.
# Three levels is enough for ``list[list[Model]]`` (depth 2) and any
# ``list[list[list[Model]]]`` (depth 3) shape that's plausible in practice.
# Beyond depth 3, nested DB content is most likely an unintentional shape
# that should hit the existing ``_deep_serialize_dict`` path or the user's
# own override, not the framework's defensive normalize pass.
_NORMALIZE_DEPTH_LIMIT = 3


def _normalize_db_values(value: Any, depth: int = 0) -> Any:
    """Recursively normalize raw Django DB values (Model / QuerySet /
    list[Model] / list[list[Model]]) into JSON-serializable dicts.

    Used by ``_sync_state_to_rust`` to catch DB values that snuck past the
    JIT pipeline (e.g., user added them via ``get_context_data`` override
    AFTER ``super()``). After normalization, change-detection compares
    ``list[dict]`` element-wise via ``dict.__eq__`` (structural) instead of
    ``Model.__eq__`` (pk-only), correctly catching field mutations.

    Returns the value unchanged if no DB content found. Idempotent on
    already-serialized values — calling repeatedly is a no-op once the
    structure is dict-only.

    Shape coverage:
    - ``Model``            → dict (#1205)
    - ``QuerySet``         → list[dict] (#1205)
    - ``list[Model]``      → list[dict] (any-position-Model; heterogeneous
                             ``[dict, Model]`` is covered, not just
                             ``[Model, ...]``) (#1207 expansion)
    - ``list[list[...]]``  → recursive normalize with depth bound (#1207)

    The depth bound (``_NORMALIZE_DEPTH_LIMIT``) prevents pathological
    recursion on dict-of-list-of-dict-of-list shapes; beyond that depth,
    the value is returned unchanged (best-effort).
    """
    from django.db.models import Model, QuerySet

    if depth >= _NORMALIZE_DEPTH_LIMIT:
        return value

    if isinstance(value, Model):
        return normalize_django_value(value)

    if isinstance(value, QuerySet):
        return [normalize_django_value(item) for item in value]

    if isinstance(value, list) and len(value) > 0:
        # Heterogeneous-safe: scan the entire list for any Model, not just
        # value[0]. ``[dict, Model]`` and ``[Model, dict]`` both trigger.
        if any(isinstance(item, Model) for item in value):
            return [
                normalize_django_value(item)
                if isinstance(item, Model)
                else _normalize_db_values(item, depth + 1)
                for item in value
            ]
        # Nested-list recursion: ``[[Model, ...], [Model]]`` reaches here
        # because the outer list has no Model directly; recurse into each
        # sublist.
        if isinstance(value[0], list):
            return [_normalize_db_values(item, depth + 1) for item in value]

    return value


def _ensure_custom_filters_bridged() -> None:
    """One-shot bootstrap that forwards Django's ``@register.filter``
    callables to the Rust filter registry. Idempotent and non-fatal on
    failure — filters still work in the Python render path even if the
    Rust bridge is unavailable.
    """
    global _CUSTOM_FILTERS_BRIDGED
    if _CUSTOM_FILTERS_BRIDGED:
        return
    try:
        from ..template_filters import bootstrap_django_filters

        bootstrap_django_filters()
    except Exception:  # noqa: BLE001 — defensive; never block render
        logger.warning(
            "Failed to bridge Django custom filters to Rust template engine; "
            "filters will still work in the Python render path",
            exc_info=True,
        )
    finally:
        # Set the guard whether bootstrap succeeded or threw — we never
        # want to re-attempt on every render and re-log the warning.
        _CUSTOM_FILTERS_BRIDGED = True


def _collect_safe_keys(
    value: Any, prefix: str = "", visited: Optional[Set[int]] = None
) -> List[str]:
    """
    Recursively scan a value for SafeString instances and return dotted paths.

    Args:
        value: The value to scan (SafeString, dict, list, or any other type)
        prefix: Current path prefix (e.g., "items.0" or "data")
        visited: Set of object IDs already visited (prevents circular references)

    Returns:
        List of dotted paths to SafeString values

    Examples:
        items = [{"content": mark_safe("<b>Item</b>")}]
        → ["items.0.content"]

        data = {"content": mark_safe("<u>Text</u>")}
        → ["data.content"]
    """
    from django.utils.safestring import SafeString

    if visited is None:
        visited = set()

    safe_keys = []

    # Check if value itself is SafeString
    if isinstance(value, SafeString):
        if prefix:
            safe_keys.append(prefix)
        return safe_keys

    # Recurse into dicts
    if isinstance(value, dict):
        # Prevent circular reference loops
        value_id = id(value)
        if value_id in visited:
            return safe_keys
        visited.add(value_id)

        for key, sub_value in value.items():
            sub_prefix = f"{prefix}.{key}" if prefix else key
            safe_keys.extend(_collect_safe_keys(sub_value, sub_prefix, visited))

        visited.remove(value_id)

    # Recurse into lists/tuples
    elif isinstance(value, (list, tuple)):
        # Prevent circular reference loops
        value_id = id(value)
        if value_id in visited:
            return safe_keys
        visited.add(value_id)

        for index, sub_value in enumerate(value):
            sub_prefix = f"{prefix}.{index}" if prefix else str(index)
            safe_keys.extend(_collect_safe_keys(sub_value, sub_prefix, visited))

        visited.remove(value_id)

    return safe_keys


def _collect_sub_ids(
    value: Any,
    collected: Set[int],
    visited: Optional[Set[int]] = None,
    depth: int = 0,
) -> None:
    """Collect id() of all objects reachable from *value* (dicts, lists, tuples).

    Used by ``_sync_state_to_rust`` to detect derived context variables that
    share an inner object with a changed instance attribute.  Depth-capped at
    8 and cycle-safe via a *visited* set.
    """
    if depth > 8:
        return
    if visited is None:
        visited = set()
    value_id = id(value)
    if value_id in visited:
        return
    visited.add(value_id)
    collected.add(value_id)
    if isinstance(value, dict):
        for v in value.values():
            _collect_sub_ids(v, collected, visited, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect_sub_ids(v, collected, visited, depth + 1)


class RustBridgeMixin:
    """Rust integration: _initialize_rust_view, _sync_state_to_rust."""

    if TYPE_CHECKING:
        # Cooperating attributes/methods supplied by the host class (LiveView)
        # and sibling mixins. Declared type-only so the strict-island mypy run
        # resolves them on this mixin without a runtime change — the real
        # definitions live on LiveView / the other mixins (this mixin is never
        # instantiated standalone). See streaming.py for the same pattern.
        request: Any
        _rust_view: Any
        _websocket_session_id: Optional[str]
        _django_session_key: Optional[str]
        _cached_csrf_token: Optional[str]

        def get_context_data(self, **kwargs: Any) -> Dict[str, Any]: ...

        def _apply_context_processors(
            self, context: Dict[str, Any], request: Any
        ) -> Dict[str, Any]: ...

        def get_template(self) -> str: ...

    # Cached per-template hash slot — a class-level cache written into
    # ``cls.__dict__`` by ``_get_cached_template_hash_slot``. Declared here so
    # the strict-island resolves the dynamic class-attribute write without a
    # runtime change.
    _djust_template_hash_slot: str

    def _apply_loop_render_cache_flag(self) -> None:
        """Wire ``LIVEVIEW_CONFIG['loop_render_cache_enabled']`` → Rust (#1967).

        Reads the config flag (default-True since #2062; explicit False is
        the opt-out kill-switch) and forwards it to the Rust
        ``RustLiveView`` via ``set_loop_render_cache_enabled``. Idempotent and
        cheap (a single bool set on the Rust side). Called on every
        ``_rust_view`` (re)initialization — including cache HITs, where the
        flag is not part of the serialized view state. A no-op if the Rust
        build predates the setter (defensive ``hasattr`` guard).
        """
        rust_view = getattr(self, "_rust_view", None)
        if rust_view is None or not hasattr(rust_view, "set_loop_render_cache_enabled"):
            return
        try:
            from ..config import get_config

            enabled = bool(get_config().get("loop_render_cache_enabled", False))
        except Exception:  # pragma: no cover - config access is defensive
            logger.debug("[LiveView] loop_render_cache flag read failed; defaulting OFF")
            enabled = False
        rust_view.set_loop_render_cache_enabled(enabled)

    def _apply_template_auto_call_flag(self) -> None:
        """Wire ``LIVEVIEW_CONFIG['template_auto_call']`` → Rust (ADR-024).

        Reads the (default-True) kill-switch and forwards it to the Rust
        ``RustLiveView`` via ``set_template_auto_call``. Mirrors the #1967
        ``_apply_loop_render_cache_flag`` plumbing: idempotent, cheap (one
        bool set), called on every ``_rust_view`` (re)initialization —
        including cache HITs and msgpack restores, where the flag is not
        part of the serialized view state. A no-op if the Rust build
        predates the setter (defensive ``hasattr`` guard).
        """
        rust_view = getattr(self, "_rust_view", None)
        if rust_view is None or not hasattr(rust_view, "set_template_auto_call"):
            return
        try:
            from ..config import get_config

            enabled = bool(get_config().get("template_auto_call", True))
        except Exception:  # pragma: no cover - config access is defensive
            logger.debug("[LiveView] template_auto_call flag read failed; defaulting ON")
            enabled = True
        rust_view.set_template_auto_call(enabled)

    def _initialize_rust_view(self, request: Any = None) -> None:
        """Initialize the Rust LiveView backend"""

        logger.debug("[LiveView] _initialize_rust_view() called, _rust_view=%s", self._rust_view)

        # Bootstrap project-defined ``@register.filter`` callables into the
        # Rust filter registry the first time any LiveView is initialized
        # (#1121). Subsequent calls are cheap — a process-level guard
        # short-circuits, and re-bootstrapping is idempotent for late-
        # loaded apps. Failure is non-fatal: filters still work in
        # Python-rendered paths.
        _ensure_custom_filters_bridged()

        if self._rust_view is None:
            # Derive the per-template 8-hex hash for the cache key (#1362
            # section 1) — operators no longer need to set
            # ``REDIS_KEY_PREFIX = f"djust:{BUILD_ID}:"`` to avoid stale
            # state acting as a diff baseline post-deploy; the framework
            # now invalidates the cache automatically whenever the
            # primary template's bytes change.
            #
            # Multi-template caveat: the cache key uses the PRIMARY
            # template's source hash. Sub-template changes via
            # ``{% include %}`` / ``{% extends %}`` parents that don't
            # alter the primary's source bytes won't invalidate by
            # themselves. In practice the primary nearly always shifts
            # when included templates change downstream (block content
            # moves, include filenames change, etc.), so this is
            # acceptable for v0.9.4-2; if a deploy ever ships a pure
            # sub-template-only edit, operators can clear the backend
            # explicitly via ``djust clear --all``.
            #
            # Perf note: ``_get_cached_template_hash_slot`` caches the
            # 8-hex hash on the view class so cache HITs don't pay the
            # ``get_template()`` cost on every WS reconnect. Pre-#1362
            # the cache HIT path skipped ``get_template()`` entirely;
            # this preserves that property by only loading + hashing
            # the template source once per class lifetime.
            template_hash_slot = self._get_cached_template_hash_slot()
            template_source = None  # loaded lazily on cache MISS only

            # Try to get from cache if we have a session
            if hasattr(self, "_websocket_session_id") and self._websocket_session_id:
                ws_path = getattr(self, "_websocket_path", "/")
                ws_query = getattr(self, "_websocket_query_string", "")

                query_hash = ""
                if ws_query:
                    params = parse_qs(ws_query)
                    sorted_query = urlencode(sorted(params.items()), doseq=True)
                    query_hash = hashlib.md5(sorted_query.encode()).hexdigest()[:8]

                # Use the actual page path to match the HTTP render cache key.
                # Prefer the request parameter (passed from render()/render_with_diff()),
                # then fall back to self.request (set on the view during WS mount).
                # Falls back to view class name + ws_path if neither is available.
                page_path = getattr(request, "path", None) or getattr(
                    getattr(self, "request", None), "path", None
                )
                if page_path:
                    view_key = f"liveview_{page_path}"
                else:
                    view_class = self.__class__.__name__
                    view_key = f"liveview_{view_class}_{ws_path}"
                if query_hash:
                    view_key = f"{view_key}_{query_hash}"
                session_key = (
                    getattr(self, "_django_session_key", None) or self._websocket_session_id
                )

                from ..state_backend import get_backend

                backend = get_backend()
                self._cache_key = f"{session_key}_{view_key}{template_hash_slot}"
                # codeql[py/log-injection] — cache_key may contain request.path; sanitize
                logger.debug(
                    "[LiveView] Cache lookup (WebSocket): cache_key=%s",
                    sanitize_for_log(self._cache_key),
                )

                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    # template_dirs are not serialized; restore them after cache hit
                    self._rust_view.set_template_dirs(get_template_dirs())
                    # loop_render_cache flag is transient (not serialized);
                    # re-apply it from config after a cache hit (#1967).
                    self._apply_loop_render_cache_flag()
                    self._apply_template_auto_call_flag()
                    logger.debug("[LiveView] Cache HIT! Using cached RustLiveView")
                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    logger.debug("[LiveView] Cache MISS! Will create new RustLiveView")
            elif request and hasattr(request, "session"):
                view_key = f"liveview_{request.path}"
                if request.GET:
                    query_hash = hashlib.md5(request.GET.urlencode().encode()).hexdigest()[:8]
                    view_key = f"{view_key}_{query_hash}"
                session_key = request.session.session_key
                if not session_key:
                    request.session.create()
                    session_key = request.session.session_key

                from ..state_backend import get_backend

                backend = get_backend()
                self._cache_key = f"{session_key}_{view_key}{template_hash_slot}"
                # codeql[py/log-injection] — cache_key may contain request.path; sanitize
                logger.debug(
                    "[LiveView] Cache lookup (HTTP): cache_key=%s",
                    sanitize_for_log(self._cache_key),
                )

                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    # template_dirs are not serialized; restore them after cache hit
                    self._rust_view.set_template_dirs(get_template_dirs())
                    # loop_render_cache flag is transient (not serialized);
                    # re-apply it from config after a cache hit (#1967).
                    self._apply_loop_render_cache_flag()
                    self._apply_template_auto_call_flag()
                    logger.debug("[LiveView] Cache HIT! Using cached RustLiveView")
                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    logger.debug("[LiveView] Cache MISS! Will create new RustLiveView")

            # codeql[py/log-injection] — cache_key may contain request.path; sanitize
            logger.debug(
                "[LiveView] Creating NEW RustLiveView for cache_key=%s",
                sanitize_for_log(self._cache_key),
            )
            # Lazy template-source load: pre-PR #1362-Iter-1 fix the source
            # was hoisted before the cache lookup, but the cache HIT path
            # doesn't actually need it (the cached RustLiveView already
            # carries its compiled template). Defer to here so cache HITs
            # avoid the Django template loader + inheritance resolution
            # cost on every WS reconnect.
            if template_source is None:
                template_source = self.get_template()
            logger.debug("[LiveView] Template length: %d chars", len(template_source))
            logger.debug("[LiveView] Template preview: %s...", template_source[:200])

            template_dirs = get_template_dirs()
            self._rust_view = RustLiveView(template_source, template_dirs)
            # Apply the per-item loop render cache flag (#1967, default OFF).
            self._apply_loop_render_cache_flag()
            self._apply_template_auto_call_flag()

            if self._cache_key:
                from ..state_backend import get_backend

                backend = get_backend()
                backend.set(self._cache_key, self._rust_view)

    def _get_cached_template_hash_slot(self) -> str:
        """Return the ``_t<8hex>`` cache-key slot for this view's template.

        Caches the slot on the view CLASS (not the instance) so the cost
        of ``get_template()`` (Django template loader + inheritance
        resolution) and ``compute_template_hash()`` (Rust call) is paid
        ONCE per class lifetime, not on every cache lookup.

        Pre-PR #1362-Iter-1 ``_initialize_rust_view`` did NOT call
        ``get_template()`` on cache HIT — the cached ``RustLiveView`` was
        returned without re-loading the source. Hoisting the source load
        before the cache check (to derive the per-template hash for the
        cache key) introduced a real perf cost on the WS reconnect hot
        path. This method preserves the original property: cache HITs no
        longer pay the per-call template-load cost.

        Why class-level (not instance-level): the ``template`` /
        ``template_name`` class attributes are stable for the lifetime of
        the process in 99%+ of apps. Hot-reload's class-replacement
        already produces a new class object (different ``cls`` →
        different ``_template_hash_slot_cache`` slot), so dev-time
        template edits naturally invalidate without explicit busting.

        Falls back to an empty slot ("") if the Rust extension is
        unavailable for any reason. Cache invalidation falls back to
        TTL — same behavior as v0.9.4-1 and earlier.
        """
        cls = type(self)
        # Per-class cache: written into ``cls.__dict__`` (NOT inherited
        # via MRO lookups) so subclasses with different templates don't
        # see the parent's hash. Using ``__dict__`` access avoids the
        # standard attribute resolution that would walk the MRO.
        cached: Optional[str] = cls.__dict__.get("_djust_template_hash_slot")
        if cached is not None:
            return cached
        try:
            from .._rust import compute_template_hash

            template_source = self.get_template()
            slot = f"_t{compute_template_hash(template_source)}"
        except Exception:
            # Defensive: if the Rust extension is unavailable for any
            # reason, fall back to the legacy un-hashed key shape rather
            # than raising. Don't memoize the empty fallback so a future
            # call (after the Rust extension comes back) still has a
            # chance to populate the cache. Empty-slot path is a
            # defensive fallback that virtually never triggers in
            # practice; the steady-state cost of recomputing it on
            # failure is negligible compared to the failure mode itself.
            logger.exception(
                "[LiveView] compute_template_hash failed; cache key will not "
                "include template hash slot (fallback to TTL-based invalidation)"
            )
            return ""
        # Memoize on the class so subsequent calls are O(1).
        cls._djust_template_hash_slot = slot
        return slot

    def _get_template_deps(self) -> Optional[Dict[str, Any]]:
        """Build template dependency map: which context keys does the template use?

        Caches the result per template content hash so it's computed only once.
        Returns None if extraction is unavailable (no Rust backend).
        """
        deps: Optional[Dict[str, Any]] = getattr(self, "_template_deps", None)
        if deps is not None:
            return deps

        try:
            from ..mixins.jit import _cached_extract_template_variables
        except ImportError:
            return None

        template_content = getattr(self, "_template_content", None)
        if not template_content:
            return None

        deps = _cached_extract_template_variables(template_content)
        self._template_deps = deps
        return deps

    def set_changed_keys(self, keys: Union[str, Iterable[str], None] = None) -> None:
        """Mark one or more public attrs as changed, forcing a re-render.

        djust's auto change-detection uses a fast identity + shallow-fingerprint
        snapshot (``_snapshot_assigns``) that deliberately does NOT deep-copy
        state (~100x faster). The trade-off is that an *in-place* mutation of a
        nested container — e.g. ``self.rows[0]["done"] = True`` or
        ``self.columns[0]["cards"].append(card)`` — shares the same object with
        the previous snapshot, so it is invisible to change detection and
        produces no re-render. See the "Nested state" note in
        ``docs/website/guides/state-primitives.md``.

        Call this from an event handler AFTER such a mutation to force a
        re-render::

            def toggle(self, i: int):
                self.rows[i]["done"] = not self.rows[i]["done"]  # in-place
                self.set_changed_keys("rows")

        Because the previous state is aliased (the snapshot shares the mutated
        object), djust cannot compute a *targeted* VDOM diff for the changed
        subtree, so this forces a full re-render of the view. When a targeted
        diff matters (large views, hot paths), prefer building a NEW value
        immutably instead — djust diffs that efficiently::

            self.rows = [
                {**r, "done": not r["done"]} if j == i else r
                for j, r in enumerate(self.rows)
            ]

        Zero-arg form (#1992): call ``set_changed_keys()`` with NO arguments
        when a handler changed only EXTERNAL state (a DB row, a cache) and
        touched no public ``self.*`` attribute, but ``get_context_data()`` will
        return different HTML on the next render (it re-queries the DB). Auto
        change-detection sees no changed attr and would auto-skip the event; the
        zero-arg form forces a full re-render without naming a key::

            def switch_branch(self, message_id, child_id):
                msg = Message.objects.get(id=message_id)
                msg.active_child_id = child_id
                msg.save(update_fields=["active_child_id"])  # DB only, no self.*
                self.set_changed_keys()                       # force the re-render

        Args:
            keys: an attr name, or an iterable of attr names, to mark changed.
                Omit entirely (``None``) for the zero-arg force-render form above
                — a DB/external-only change with no changed public attr.

        Note:
            Distinct from the Rust-side ``RustLiveView.set_changed_keys`` (the
            PyO3 partial-sync primitive `_sync_state_to_rust` drives internally)
            — this Python-level method is the user-facing "force a re-render"
            hatch and does not talk to the Rust view directly.
        """
        if keys is None:
            # Zero-arg form (#1992): the handler changed only EXTERNAL state (a
            # DB row, a cache) with no public ``self.*`` change, but
            # ``get_context_data()`` will render different HTML next time. Force
            # a full re-render without naming a key — ``_force_full_html`` below
            # is the actual bypass; there is no key to add to ``_changed_keys``.
            self._force_full_html = True
            return
        key_iter: Iterable[str] = (keys,) if isinstance(keys, str) else keys
        existing: Set[str] = getattr(self, "_changed_keys", None) or set()
        # Optional[...]: the attr is also cleared to None after each render sync.
        self._changed_keys: Optional[Set[str]] = existing | set(key_iter)
        # Force the render. Both flags written here are in
        # ``_FRAMEWORK_INTERNAL_ATTRS`` and therefore EXCLUDED from the pre/post
        # assigns snapshot — assigning them cannot perturb the fingerprint, so
        # the in-place mutation still looks unchanged and the event would
        # auto-skip. ``_force_full_html`` is the sanctioned skip bypass: honored
        # on every skip path (runtime event spine, WS deferred-activity, WS tick)
        # and consumed (reset) after the render it forces.
        self._force_full_html = True

    def _sync_state_to_rust(self, preloaded_context: Optional[Dict[str, Any]] = None) -> None:
        """Sync Python state to Rust backend.

        Phoenix-style change tracking: only sends values that actually changed
        since the last render. Rust's update_state() merges (extends), so
        unchanged keys are retained from the previous render.

        Args:
            preloaded_context: If provided, use this instead of calling
                get_context_data(). Used by the async path where context
                was already fetched with await.

        Three-layer detection:
          1. Instance attribute changes — snapshot-based (_changed_keys from handle_event)
          2. TypedState dirty flags — in-place dict mutation tracking
          3. Computed value changes — id() reference comparison against previous render
        """
        if self._rust_view:
            from ..components.base import Component, LiveComponent
            from django import forms

            full_context = (
                preloaded_context if preloaded_context is not None else self.get_context_data()
            )

            # Defensive normalize pass for DB values added after super() (#1205, #1207).
            # When a user overrides ``get_context_data`` and sets a raw DB
            # value (Model / QuerySet / list[Model]) AFTER calling ``super()``,
            # the JIT pipeline never sees it — and downstream change-detection
            # would compare list[Model] via ``Model.__eq__`` (pk-only),
            # missing in-place field mutations.
            #
            # The recursive helper below covers four shapes:
            #   1. ``Model``                  → dict (full field extraction)
            #   2. ``QuerySet``               → list[dict]
            #   3. ``list[Model]`` (any pos)  → list[dict] (heterogeneous-safe;
            #                                   #1207 — dict-in-position-0 no
            #                                   longer escapes)
            #   4. ``list[list[Model]]``      → recurse with depth bound
            #                                   (#1207 — nested grouping shape)
            # ADR-024: capture RAW Model instances BEFORE the normalize loop
            # replaces them with serialized dicts. The eager dict stays the
            # fast path (and wins every hit), but reverse relations, managers
            # and non-``get_``-prefixed methods are absent from the dict —
            # resolvable only via the sidecar getattr walk
            # ({{ workspace.memberships.count }}, the #1985 symptom). Without
            # this, only request-scoped models (excluded from normalize) ever
            # reached the sidecar.
            from django.db.models import Model as _DjModel

            _raw_models_for_sidecar = {
                _key: _val for _key, _val in full_context.items() if isinstance(_val, _DjModel)
            }

            for _key, _val in list(full_context.items()):
                _normalized = _normalize_db_values(_val)
                if _normalized is not _val:
                    full_context[_key] = _normalized

            # Apply Django context processors so context-processor vars
            # (e.g. djust theming's {{ theme_panel }} / {{ theme_head }})
            # are available everywhere the dj-root template renders —
            # including inside {% include %} partials (#1722, completes
            # #233). render_full_template applies processors only to the
            # outer page shell (template.py); this is the equivalent for
            # the dj-root template that render()/render_with_diff() drive
            # on the initial GET and on every WebSocket update.
            #
            # _apply_context_processors only ADDS keys absent from
            # full_context (view context wins) and is a no-op when
            # request is None, so it is safe on the WS path. The vars are
            # sent once on the first sync and Rust's merging update_state
            # retains them across subsequent partial syncs.
            request = getattr(self, "request", None)
            if request is not None:
                full_context = self._apply_context_processors(full_context, request)

            # Request-scoped, framework-provided context keys (#1786): the
            # ``request`` itself plus everything the standard context
            # processors contribute (auth ``user`` / ``perms``, messages
            # storage, etc.). The view never assigns these to ``self`` — they
            # are folded in above purely so the Rust template can render
            # ``{{ user }}`` / ``{% csrf_token %}`` etc. We must keep them OUT
            # of:
            #   (a) the change-detection fingerprint (``_prev_context_refs`` /
            #       immutables / containers) — they get a fresh ``id()`` every
            #       event, so otherwise they bloat the fingerprint (the
            #       58-key truncation warning) and show as "changed" on every
            #       render; and
            #   (b) the non-serializable-value warning path
            #       (``normalize_django_value``) — request / ``PermWrapper`` /
            #       ``FallbackStorage`` / ``SimpleLazyObject`` are not
            #       JSON-serializable and would log a warning on every render.
            # The non-serializable ones still reach the Rust renderer through
            # the raw-value sidecar (``set_raw_py_values`` below); serializable
            # processor outputs (e.g. theming's ``{{ theme_head }}`` SafeString)
            # stay in ``update_state`` and are unaffected.
            _request_scoped_keys = set(getattr(self, "_context_processor_keys", ()))
            _request_scoped_keys.add("request")

            # Ensure csrf_token is available for {% csrf_token %} tag (#696).
            # Cache it to avoid creating a new string object each call,
            # which would cause the change tracker to see it as "changed".
            if "csrf_token" not in full_context:
                cached_csrf = getattr(self, "_cached_csrf_token", None)
                if cached_csrf is not None:
                    full_context["csrf_token"] = cached_csrf
                else:
                    request = getattr(self, "request", None)
                    if request is not None:
                        try:
                            from django.middleware.csrf import get_token

                            token = get_token(request)
                            self._cached_csrf_token = token
                            full_context["csrf_token"] = token
                        except Exception:
                            logging.getLogger("djust.rust_bridge").warning(
                                "Failed to inject csrf_token into Rust context",
                                exc_info=True,
                            )

            # Inject DATE_FORMAT / TIME_FORMAT from Django settings so the
            # Rust |date and |time filters honour the project's configured
            # formats when no explicit format argument is given (#713).
            from django.conf import settings as _dj_settings

            for _fmt_key in ("DATE_FORMAT", "TIME_FORMAT"):
                if _fmt_key not in full_context:
                    _fmt_val = getattr(_dj_settings, _fmt_key, None)
                    if _fmt_val is not None:
                        full_context[_fmt_key] = _fmt_val

            # Dependency tracking: identify which components the template uses
            template_deps = self._get_template_deps()
            component_descriptors = getattr(type(self), "_component_descriptors", None)
            if template_deps and component_descriptors:
                # Filter out descriptor components not referenced in template
                unreferenced = set()
                for name in component_descriptors:
                    if name not in template_deps:
                        unreferenced.add(name)
                if unreferenced:
                    full_context = {k: v for k, v in full_context.items() if k not in unreferenced}

            changed_keys = getattr(self, "_changed_keys", None)
            prev_refs = getattr(self, "_prev_context_refs", {})
            # Cache of previous VALUES for immutable context keys. Needed
            # because `id()` comparison is unreliable for small ints/strings
            # (Python interns them), so we fall back to value equality for
            # these types. Catches derived immutables like
            # `completed_count = sum(...)` that change without their source
            # name appearing in `_changed_keys`.
            prev_immutables = getattr(self, "_prev_context_immutables", {})

            # _force_full_html: bypass ALL change tracking and send everything.
            # The websocket code skips _changed_keys computation when this is
            # set (line 1991), so we must treat it as a first render (#783).
            if getattr(self, "_force_full_html", False):
                prev_refs = {}

            # Determine which context to send to Rust
            if prev_refs:
                if changed_keys:
                    # Explicit change tracking from handle_event's snapshot
                    # detection. Trust it exclusively — don't also run id()
                    # comparison which produces false positives due to Python
                    # int cache misses on the double-sync path.
                    changed_sub_ids: Set[int] = set()
                    for key in changed_keys:
                        val = getattr(self, key, None) or full_context.get(key)
                        if val is not None:
                            _collect_sub_ids(val, changed_sub_ids)

                    context = {}
                    for key in changed_keys:
                        if key in full_context:
                            context[key] = full_context[key]
                    # Also include derived values that changed. The explicit
                    # `_changed_keys` only tracks direct instance attrs, so
                    # we need to detect derived values (e.g. `products` from
                    # `self._products_cache`, or `completed_count` computed
                    # from `self.todos`).
                    #
                    # Containers (dict, list, tuple) use VALUE equality
                    # instead of id() because id() is unreliable for them:
                    # CPython address reuse after GC, persistent list lookups
                    # returning the same object, etc. (#774). Unchanged
                    # containers are still skipped (previous values cached
                    # in _prev_context_containers).
                    #
                    # Immutables use value equality (Python interns them).
                    # Other types use id() comparison as a last resort.
                    prev_containers = getattr(self, "_prev_context_containers", {})
                    for key, value in full_context.items():
                        if key in context:
                            continue
                        if key not in prev_refs:
                            context[key] = value  # new key
                        elif getattr(value, "_dirty", False):
                            context[key] = value  # TypedState dirty flag
                        elif changed_sub_ids and id(value) in changed_sub_ids:
                            context[key] = value  # sub-object of changed (#703)
                        elif isinstance(value, (dict, list, tuple)):
                            # Containers: compare by VALUE, not id(). id() is
                            # unreliable for derived containers due to CPython
                            # address reuse and persistent-list lookups (#774).
                            try:
                                changed = prev_containers.get(key, _MISSING) != value
                            except (TypeError, ValueError):
                                changed = True  # Broken __eq__ → assume changed
                            if changed:
                                context[key] = value
                        elif isinstance(value, _IMMUTABLE_TYPES_FOR_SYNC):
                            # Immutable: compare by value to catch derived
                            # int/str changes (prev_immutables may miss the
                            # key if it wasn't immutable last time — treat
                            # that as "changed" since the type flipped).
                            if prev_immutables.get(key, _MISSING) != value:
                                context[key] = value
                        elif id(value) != prev_refs.get(key):
                            context[key] = value  # derived value with new id()
                else:
                    # No explicit changed_keys — use id() comparison as fallback.
                    # This path runs on the internal sync from render_with_diff
                    # and for in-place mutations without snapshot detection.
                    prev_containers = getattr(self, "_prev_context_containers", {})
                    context = {}
                    for key, value in full_context.items():
                        if key not in prev_refs:
                            context[key] = value  # new key
                        elif getattr(value, "_dirty", False):
                            context[key] = value  # TypedState dirty flag
                        elif isinstance(value, (dict, list, tuple)):
                            try:
                                changed = prev_containers.get(key, _MISSING) != value
                            except (TypeError, ValueError):
                                changed = True
                            if changed:
                                context[key] = value
                        elif isinstance(value, _IMMUTABLE_TYPES_FOR_SYNC):
                            if prev_immutables.get(key, _MISSING) != value:
                                context[key] = value
                        elif id(value) != prev_refs.get(key):
                            context[key] = value  # different object reference
            else:
                # First render: send everything
                context = full_context

            # Store refs for next comparison (full context, not filtered).
            # We also cache previous VALUES for types where id() is unreliable:
            # - Immutables (int/str/etc.) — Python interns them, so id() gives
            #   false negatives. Compared by value equality.
            # - Containers (dict/list/tuple) — CPython address reuse after GC
            #   can cause id() to match even when the value changed (#774).
            #   Compared by value equality.
            # Exclude request-scoped, framework-provided keys (#1786) from the
            # change-detection fingerprint — see the _request_scoped_keys
            # comment above. Keeping ``request`` / ``user`` / ``perms`` /
            # ``messages`` out of these dicts is what fixes the
            # ``_prev_context_refs has N keys — fingerprint truncated`` warning
            # and the per-event "changed" false positives for those values.
            self._prev_context_refs = {
                k: id(v) for k, v in full_context.items() if k not in _request_scoped_keys
            }
            self._prev_context_immutables = {
                k: v
                for k, v in full_context.items()
                if k not in _request_scoped_keys and isinstance(v, _IMMUTABLE_TYPES_FOR_SYNC)
            }
            self._prev_context_containers = {
                k: v
                for k, v in full_context.items()
                if k not in _request_scoped_keys and isinstance(v, (dict, list, tuple))
            }
            self._sync_done_this_cycle = True
            self._changed_keys = None  # Clear

            # Clear dirty flags on TypedState objects after sync
            for value in context.values():
                if hasattr(value, "_dirty"):
                    object.__setattr__(value, "_dirty", False)

            # Detect SafeString values before serialization loses the type info.
            # Fast path: skip _collect_safe_keys for JSON-native primitives
            # (int, float, bool, str, None) which can never contain SafeString.
            _JSON_PRIMITIVES = (int, float, bool, type(None))
            safe_keys: List[str] = []
            rendered_context: Dict[str, Any] = {}
            needs_normalize = False
            for key, value in context.items():
                # #1786: request-scoped, framework-provided context values
                # (the request, auth ``user`` / ``perms``, messages storage)
                # that are NOT JSON-serializable must not flow into
                # ``update_state`` / ``normalize_django_value`` — that path
                # logs a "non-serializable value: ASGIRequest / PermWrapper /
                # FallbackStorage / SimpleLazyObject" warning on every render
                # and would str()-stringify them into persisted state. They
                # still reach the Rust template through the raw-value sidecar
                # (``set_raw_py_values`` below), so ``{{ user }}`` etc. keep
                # rendering. Serializable processor outputs (e.g. theming's
                # ``{{ theme_head }}`` SafeString) are NOT skipped — they fall
                # through to the normal handling below.
                if key in _request_scoped_keys and not _is_json_serializable(value):
                    continue
                if isinstance(value, (Component, LiveComponent)):
                    # Render caching: skip render if component has clean cached HTML
                    cached = getattr(value, "_cached_html", None)
                    if cached is not None and not getattr(value, "_dirty", True):
                        rendered_html = cached
                    else:
                        rendered_html = str(value.render())
                        # Cache for next render cycle
                        try:
                            object.__setattr__(value, "_cached_html", rendered_html)
                            object.__setattr__(value, "_dirty", False)
                        except (AttributeError, TypeError):
                            pass  # Not all objects support attribute setting
                    rendered_context[key] = {"render": rendered_html}
                    safe_keys.append(key)
                    needs_normalize = True
                elif isinstance(value, forms.BaseForm):
                    from djust.serialization import render_form_value

                    rendered_context[key] = render_form_value(value)
                    for field_name in value.fields:
                        safe_keys.append(f"{key}.{field_name}")
                    needs_normalize = True
                elif isinstance(value, _JSON_PRIMITIVES):
                    # Fast path: primitives are JSON-native and can't be SafeString
                    rendered_context[key] = value
                elif isinstance(value, str):
                    # Strings: check SafeString directly (no recursion needed)
                    from django.utils.safestring import SafeString

                    if isinstance(value, SafeString):
                        safe_keys.append(key)
                    rendered_context[key] = value
                else:
                    # Complex types (dict, list, etc.): full scan
                    safe_keys.extend(_collect_safe_keys(value, key))
                    rendered_context[key] = value
                    needs_normalize = True

            # Skip normalize_django_value when context only has JSON-native types
            if needs_normalize:
                json_compatible_context = normalize_django_value(rendered_context)
            else:
                json_compatible_context = rendered_context

            # No Python-side lock needed (#1353): each HTTP/WebSocket
            # caller now holds its own ``RustLiveView`` instance because
            # ``InMemoryStateBackend.get`` returns a fresh
            # ``serialize_msgpack`` / ``deserialize_msgpack`` clone on
            # cache hits, mirroring the ``RedisStateBackend`` contract.
            # See the module-level docstring above for context.

            # Build the sidecar of raw Python objects — reads
            # ``full_context`` only, never touches Rust state.
            sidecar = None
            if hasattr(self._rust_view, "set_raw_py_values"):
                _JSON_FRIENDLY = (
                    int,
                    float,
                    bool,
                    str,
                    bytes,
                    type(None),
                    dict,
                    list,
                    tuple,
                    set,
                    frozenset,
                )
                sidecar = {}
                for _raw_key, _raw_val in full_context.items():
                    if _raw_val is None:
                        continue
                    if isinstance(_raw_val, _JSON_FRIENDLY):
                        continue
                    if isinstance(_raw_val, (Component, LiveComponent)):
                        continue
                    if isinstance(_raw_val, forms.BaseForm):
                        continue
                    sidecar[_raw_key] = _raw_val
                # ADR-024: models captured before the normalize loop replaced
                # them with dicts. The eager dict wins every direct hit; the
                # raw model serves only nested paths the dict lacks (reverse
                # relations / managers / non-get_ methods) via the sidecar
                # getattr walk. Explicit sidecar entries above take precedence.
                for _raw_key, _raw_val in _raw_models_for_sidecar.items():
                    sidecar.setdefault(_raw_key, _raw_val)
                # #1986 (ADR-024 review): the Rust getattr walk bypasses the
                # serialization floor (SECURE_DEFAULTS Pattern 1) unless raw
                # models are wrapped — else `{{ user.password }}` /
                # `{{ member.is_superuser }}` / `{{ user.get_session_auth_hash }}`
                # (and, through managers/querysets,
                # `{{ x.groups.first.user_set.first.password }}` /
                # `{% for u in qs %}{{ u.password }}`) leak to the client. Wrap
                # every sidecar value (both explicitly-assigned models above AND
                # request-scoped ones like `user`) in the floor-enforcing proxy;
                # `_protect_sidecar_value` is a no-op for non-model values
                # (request/view/etc.), and the proxy protects transitively so
                # ONE floor governs the eager and sidecar channels (#1646).
                from ..serialization import _protect_sidecar_value

                for _sk in list(sidecar.keys()):
                    sidecar[_sk] = _protect_sidecar_value(sidecar[_sk])

            # Tell Rust which context keys changed for partial rendering.
            # Only call when there are actual changes — avoids overriding a
            # previous set_changed_keys call with meaningful keys.
            # Also exclude temporary_assigns keys — they're reset to new
            # objects after each render, always getting new id().
            _temp_assigns = set(getattr(self, "temporary_assigns", {}).keys())
            # #1786: request-scoped keys are excluded from the change-detection
            # fingerprint above, so on every render they look "new" and would
            # otherwise be reported as changed keys — triggering spurious
            # partial re-renders. They never change the user-visible output
            # (their values are re-applied fresh each cycle), so skip them.
            _skip_keys = _FRAMEWORK_KEYS | _temp_assigns | _request_scoped_keys
            # Collect auto-generated _count keys (context.py adds {list_key}_count).
            # Only exclude keys where the base name is a list in full_context.
            _auto_count_keys = set()
            for k, v in full_context.items():
                if isinstance(v, list):
                    _auto_count_keys.add(f"{k}_count")
            _skip_keys |= _auto_count_keys

            # When _force_full_html cleared prev_refs, context == full_context,
            # so we must still call set_changed_keys or Rust won't do a
            # partial render (#783).
            force_full = getattr(self, "_force_full_html", False)
            user_changed: Optional[List[str]] = None
            if context and (prev_refs or force_full):
                _candidate = [k for k in context if k not in _skip_keys]
                if _candidate:
                    user_changed = _candidate

            self._rust_view.update_state(json_compatible_context)
            if safe_keys:
                self._rust_view.mark_safe_keys(safe_keys)

            # Always call set_raw_py_values (even when empty) so stale
            # objects from a previous render are cleared.
            if sidecar is not None:
                try:
                    self._rust_view.set_raw_py_values(sidecar)
                except Exception:
                    logging.getLogger("djust.rust_bridge").warning(
                        "set_raw_py_values failed; template getattr fallback disabled this cycle",
                        exc_info=True,
                    )

            if user_changed is not None:
                self._rust_view.set_changed_keys(user_changed)

            # Mark static assigns as sent — subsequent syncs will skip them
            if getattr(self, "static_assigns", None) and not getattr(
                self, "_static_assigns_sent", False
            ):
                self._static_assigns_sent = True
