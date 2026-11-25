"""
RustBridgeMixin - Rust backend integration for LiveView.
"""

import logging
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from django.utils.datastructures import MultiValueDict
from django.utils.safestring import SafeData

from .._held_rows import recorded_render
from .._rust import RustLiveView
from .._mapping import mapping_values
from .._exposure_providers import (
    RUST_RENDER_PROVIDER,
    ExplicitRenderContext,
    registered_components,
    require_no_provider_keys,
)
from .._accumulating_containers import view_accumulating_container_ids
from ..change_detection import (
    ChangedKeys,
    SnapshotEntry,
    deep_fingerprint,
    fingerprints_by_content,
    retained_fingerprint,
    warn_fingerprint_truncated,
)
from ..serialization import holds_form, normalize_django_value
from ..state_pass import convert_for_render, fingerprint_and_convert
from ..template_filters import _ensure_custom_filters_bridged
from ..utils import get_template_dirs
from ..render_env import apply_render_env
from ..request_scoped import is_request_scoped_data
from .. import _per_turn
from .context import PROCESSOR_RECORD_ATTR, PROCESSOR_REPLAY_ATTR, _keeps_out_of_state
from .context import _holds_db_value, _state_model_values
from .context import _request_scoped_keys as request_scoped_keys

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


# Maximum recursion depth for ``_normalize_db_values``. Bounded to keep the
# normalize pass O(N * depth) instead of unbounded for pathological nesting.
# Three levels is enough for ``list[list[Model]]`` (depth 2) and any
# ``list[list[list[Model]]]`` (depth 3) shape that's plausible in practice.
# Beyond depth 3, nested DB content is most likely an unintentional shape
# that should hit the existing ``_deep_serialize_dict`` path or the user's
# own override, not the framework's defensive normalize pass.
_NORMALIZE_DEPTH_LIMIT = 3


def _queryset_method_roots_for(view: Any) -> FrozenSet[str]:
    """Context names the view's template reads ``count`` / ``exists`` /
    ``first`` / ``last`` / ``all`` directly off (#4241, #4258, #4270).

    The JIT keeps an evaluated QuerySet for the render sidecar on exactly this
    condition (``queryset_method_roots``); the native render's capture of a
    context QuerySet uses it too, so no other QuerySet attribute (``.query``)
    becomes reachable. An unreadable template reads nothing.
    """
    from .jit import _jit_template_content, queryset_method_roots

    try:
        content = _jit_template_content(view)
    except Exception:  # noqa: BLE001 -- no template source means no capture
        return frozenset()
    return queryset_method_roots(content)


def _queryset_row_walk_paths_for(view: Any) -> Dict[str, FrozenSet[str]]:
    """The paths the view's template reads off each name, loop-row reads
    included (#4069). An unreadable template reads nothing."""
    from .jit import _jit_template_content, queryset_row_walk_paths

    try:
        content = _jit_template_content(view)
    except Exception:  # noqa: BLE001 -- no template source means no capture
        return {}
    return queryset_row_walk_paths(content)


def _overlay_floor(value: Any, memo: Dict[Any, Any], text_memo: Dict[int, Any]) -> Any:
    """The floor the overlay applies to one state value (#3936): the sidecar's tree floor, with
    each model handle's text kept plain (LiveView's escaping, train/2.0-ai)."""
    from ..serialization import _overlay_plain_text_handles, _protect_sidecar_tree

    floored = _protect_sidecar_tree(value, 0, memo, set())
    return _overlay_plain_text_handles(floored, text_memo)


def _method_read_rows_take_the_jit(full_context: Any) -> bool:
    """Does a method-read QuerySet (``{{ qs.count }}``) still go on to the row-walk check (review
    S1)? On an explicit context, yes: its walked rows are JIT-serialized as legacy's are. Legacy's
    JIT already ran before the sync. A function so the gate-off test can turn it off."""
    return type(full_context) is ExplicitRenderContext


def _explicit_fk_rows_take_the_jit(model_class: Any, paths: FrozenSet[str]) -> bool:
    """Does an explicit view's QuerySet go through the JIT because its rows' forward relation is read
    as one segment (review N4)? A function so the gate-off test can turn it off."""
    return _paths_read_a_relation(model_class, paths)


def _plain_rows_take_the_jit(
    value: Any, model_class: Any, paths: FrozenSet[str], context: Any
) -> bool:
    """Does an explicit view's Model, or model list / QuerySet whose rows the template reads
    only columns off, go through the JIT, reduced to the paths the template reads (#4290)?

    Yes, as on 1.x, unless:

    - it is a ``.values()`` / ``.values_list()`` QuerySet: its rows are dicts / tuples, which
      the JIT's model serializer reads empty (#4271), and they hold only the columns asked for;
    - a ``dictsort:<variable>`` names something that is not one of the row model's columns this
      render (a property, a ``get_*`` method): the JIT row carries a column only (#3910), so
      such a list keeps the normalized rows it sorted by before.

    A function so the gate-off test can turn it off.
    """
    from django.db.models import QuerySet
    from django.db.models.query import ModelIterable

    from .jit import resolve_variable_keys, split_variable_keys

    if isinstance(value, QuerySet) and not issubclass(value._iterable_class, ModelIterable):
        return False
    _, variables = split_variable_keys(sorted(paths))
    if not variables:
        return True
    return len(resolve_variable_keys(variables, context, model_class)) == len(set(variables))


def _overlay_model_class(value: Any) -> Any:
    """The model class behind one overlay value (a ``Model``, a ``QuerySet``, a model list)."""
    from django.db.models import Model, QuerySet

    if isinstance(value, QuerySet):
        return value.model
    if isinstance(value, Model):
        return type(value)
    return next((type(v) for v in value if isinstance(v, Model)), None)


def _overlay_skip_keys(
    view: Any,
    raw: Dict[str, Any],
    full_context: Any,
    walked_keys: Any = (),
    qs_roots: Optional[FrozenSet[str]] = None,
    plain_keys: Any = (),
) -> FrozenSet[str]:
    """The state keys the flag-off path already answers with Django's semantics (#3936, B-H1).

    The overlay binds a handle ABOVE the state, so it shadows every answer the JIT and the
    render sidecar give for that name. For these keys the shadow loses an answer or adds a query,
    so they keep the flag-off path (capped, prefetched rows; method answers):

    - a QuerySet the template reads ``count`` / ``exists`` / ``first`` / ``last`` off (#4241,
      #4258): the handle is a LIST of rows, which has no ``count``;
    - a name whose rows the template reads through a QuerySet method (``_all_method_rows``,
      #3801);
    - a name whose rows the JIT serialized WITH the relation paths the template walks
      (``walked_keys`` on an explicit view, #4069 S1; ``_jit_serialized_keys`` plus a live walk
      on a legacy one): the JIT prefetched them and applied ``queryset_all_row_cap`` (#3788),
      and a handle would walk each row again, one query per row.

    Every other key still gets its handle: a single model on an explicit view, a model list or
    QuerySet the template reads only columns off, and every value a filtered rebinding or a
    custom filter / tag is handed. ``plain_keys`` are those names on an explicit view: the JIT
    reduced them to the paths the template reads (#4290), but did not walk their rows, so they
    keep their handle whatever the template reads off them.
    """
    from django.db.models import QuerySet

    from .jit import rows_need_live_walk

    if not raw:
        return frozenset()
    skip = {k for k in walked_keys if k in raw}
    skip.update(k for k in (getattr(view, "_all_method_rows", None) or {}) if k in raw)
    if any(isinstance(v, QuerySet) for v in raw.values()):
        # The sync's own lookup when it made one: one per sync (#4258).
        roots = _queryset_method_roots_for(view) if qs_roots is None else qs_roots
        skip.update(k for k, v in raw.items() if isinstance(v, QuerySet) and k in roots)
    jit_keys = getattr(view, "_jit_serialized_keys", None) or ()
    pending = [k for k in raw if k in jit_keys and k not in skip and k not in plain_keys]
    if pending:
        walk_paths = _queryset_row_walk_paths_for(view)
        for key in pending:
            paths = walk_paths.get(key)
            if not paths:
                continue
            model_cls = _overlay_model_class(raw[key])
            if rows_need_live_walk(model_cls, paths, full_context) or (
                # Review B2: a legacy JIT re-queries a QuerySet WITH ``select_related`` for a
                # forward relation read as one segment (``{{ o.customer }}``), which
                # ``rows_need_live_walk`` calls a value. The handles would iterate the state
                # QuerySet again and load each row's relation lazily (N+1).
                isinstance(raw[key], QuerySet) and _paths_read_a_relation(model_cls, paths)
            ):
                skip.add(key)
    return frozenset(skip)


def _paths_read_a_relation(model_class: Any, paths: FrozenSet[str]) -> bool:
    """Does any template path start at a relation field of the row model (review B2)?"""
    from .jit import VARIABLE_KEY_PREFIX

    if model_class is None:
        return False
    for path in paths:
        if path.startswith(VARIABLE_KEY_PREFIX):
            continue
        try:
            field = model_class._meta.get_field(path.split(".", 1)[0])
        except Exception:  # noqa: BLE001 -- not a field (a property, a method, a column alias)
            continue
        if field.is_relation:
            return True
    return False


def _render_handles(raw: Dict[str, Any], full_context: Any, skip_keys: Any = ()) -> Dict[str, Any]:
    """Floored handles for the render-time overlay (#3936).

    Every value goes through ``_protect_sidecar_tree`` (one shared memo, so one
    object is one proxy); a key whose floor raises is DROPPED, so it renders
    from its serialized dict (fail closed). Only keys the context still
    carries, and never a sidecar-only key.
    """
    if not raw:
        return {}
    memo: Dict[Any, Any] = {}
    text_memo: Dict[int, Any] = {}
    handles: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in full_context or key in skip_keys:
            continue
        try:
            handles[key] = _overlay_floor(value, memo, text_memo)
        except Exception:  # noqa: BLE001 -- fail closed: the key keeps its dict
            logging.getLogger("djust.rust_bridge").warning(
                "state_models_as_handles: the floor refused %r; it renders from its dict", key
            )
    return handles


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
        for v in mapping_values(value):
            _collect_sub_ids(v, collected, visited, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect_sub_ids(v, collected, visited, depth + 1)


#: Every method of the view that runs between the post-event snapshot and
#: the sync's fingerprints: the render entry point, the sync itself, and each
#: method the explicit context is built through. The snapshot's walks are
#: reused only while each one is the framework's own
#: (``_snapshot_walks_for_sync``), so an override that writes into state before
#: calling ``super()`` turns the reuse off. ``_get_context_processors`` is left
#: out on purpose: it returns the processor paths (tests and apps override it
#: to return a list) and the processors it names receive the request, not the
#: view.
_CONTEXT_HOOKS = (
    "render_with_diff",
    "_initialize_rust_view",
    "_sync_state_to_rust",
    "_sync_state_to_rust_body",
    "_apply_render_env",
    "_context_for_render",
    "_settled_render_context",
    "get_context_data",
    "_get_explicit_context_data",
    "reset_unique_ids",
    "clear_context_providers",
    "_get_streams_context",
    "_get_upload_context",
    "get_render_only_context",
    "_apply_context_processors",
    "_csrf_token_for_render",
)

_framework_hooks: Optional[Dict[str, Any]] = None


def _framework_context_hooks() -> Dict[str, Any]:
    """``{name: function}`` of the framework's own :data:`_CONTEXT_HOOKS`.

    Resolved on ``LiveView`` itself (its MRO is the framework's mixins), then
    on the opt-in mixins ``LiveView`` does not include.
    """
    global _framework_hooks
    if _framework_hooks is None:
        from ..live_view import LiveView
        from ..uploads import UploadMixin
        from .streams import StreamsMixin

        hooks: Dict[str, Any] = {}
        for name in _CONTEXT_HOOKS:
            for owner in (LiveView, StreamsMixin, UploadMixin):
                function = getattr(owner, name, None)
                if function is not None:
                    hooks[name] = function
                    break
        _framework_hooks = hooks
    return _framework_hooks


def _snapshot_walks_for_sync(
    view: Any, changed_keys: Any, preloaded_context: Any
) -> Optional[Dict[int, SnapshotEntry]]:
    """The post-event snapshot's container walks, if this sync may take them (#3999).

    A turn walks every container on the view in its post-event snapshot and,
    until now, walked the context's containers again in the sync. The second
    walk can be replaced by the first only if nothing changed the container
    between the two, and in-place mutation leaves no trace on a ``list`` or
    ``dict``. So the reuse is allowed only when no application code can run
    in that window, and the sync walks as before otherwise:

    * ``changed_keys`` is the :class:`~djust.change_detection.ChangedKeys` the
      handler's turn computed from that snapshot, still the view's own
      ``_changed_keys`` and computed in the turn that is running now (a
      ``set_changed_keys()`` call builds a new set; a later turn has another
      ``Turn``);
    * the context is built here (no ``preloaded_context``) and every method
      on the way from the render to the fingerprints is the framework's own
      (:data:`_CONTEXT_HOOKS`), so no ``get_context_data`` override,
      ``get_render_only_context``, ``reset_unique_ids`` or ``@computed``
      called from one runs; the template is not a ``property``; no class-level component
      (whose first binding may run its ``mount``); and no
      ``sync_turn_wrapper`` is configured around the render step.

    A value is reused per object: the entry holds the object, so
    ``entry[0] is value`` proves it is the one the snapshot walked.
    """
    if type(changed_keys) is not ChangedKeys or preloaded_context is not None:
        return None
    walks = changed_keys.fingerprints
    turn_ref = changed_keys.turn
    if not walks or turn_ref is None or not _per_turn.on("snapshot_fingerprints"):
        return None
    from ..turns import current_turn

    turn = turn_ref()
    if turn is None or turn is not current_turn():
        return None
    cls = type(view)
    for name, function in _framework_context_hooks().items():
        if getattr(cls, name, function) is not function:
            return None
    from inspect import getattr_static

    if isinstance(getattr_static(cls, "template", None), property):
        return None
    if registered_components(cls):
        return None
    from ..sync_turn import _wrapper

    if _wrapper() is not None:
        return None
    return walks


#: ``_request_scoped_fingerprint``'s answer for a value it does not fingerprint.
_NO_FINGERPRINT: Any = object()

#: Exact scalar types that are JSON data and their own fingerprint leaf.
_EXACT_PLAIN_SCALARS = frozenset((str, int, float, bool, type(None)))


def _request_scoped_fingerprint(key: str, value: Any) -> Tuple[bool, Any, Any]:
    """How a request-scoped context value takes part in change detection (#3996).

    Returns ``(keeps_out, fingerprint, converted)``:

    * ``keeps_out`` is :func:`_keeps_out_of_state`: the value rides the sidecar
      and is never in the Rust state (the request, a lazy ``user``, ``perms``,
      the messages storage, a lazy HTML chunk, a ``RequestScopedList``). Such a
      value is NOT fingerprinted: nothing about it is kept between turns, and
      it is never reported as a changed key (#1786).
    * otherwise the value is plain data that ``update_state`` stores. When it is
      made only of exact builtin containers and exact leaf types (what
      ``state_pass`` converts), ``fingerprint`` is what is kept of its
      structural fingerprint (``retained_fingerprint``: a digest, #3998),
      which holds no reference to the object, and ``converted`` is
      the ``(safe paths, rendered value)`` pair of the same walk. A value of any
      other type that still counts as serialisable (a ``SimpleLazyObject`` over
      a string, a ``str`` subclass, an ``IntEnum``) gets ``_NO_FINGERPRINT``:
      its fingerprint would keep the object itself, and a lazy object can hold
      the request. It is sent on every turn and never reported, as before.

    ONE statement of the rule for the Rust sync and the scoped component
    render's check, so the two cannot disagree (#1646).
    """
    kind = type(value)
    if kind in _EXACT_PLAIN_SCALARS:
        # The common case (a settings string, a flag), answered as the two
        # calls below answer it: it is serialisable, and this is its fingerprint.
        return False, ("v", kind, value), None
    if _keeps_out_of_state(value):
        return True, _NO_FINGERPRINT, None
    fingerprint, truncated, converted = fingerprint_and_convert(value, key)
    if converted is None:
        return False, _NO_FINGERPRINT, None
    if truncated:
        # An incomplete fingerprint cannot prove equality: equal to nothing.
        return False, object(), converted
    return False, retained_fingerprint(fingerprint), converted


def _trusted_request_scoped_fingerprints(view: Any, prev_refs: Any) -> Optional[Dict[str, Any]]:
    """The fingerprints the last sync kept for request-scoped data, or None.

    They are kept as ``(refs, fingerprints)`` where ``refs`` is the very
    ``_prev_context_refs`` dict the same sync stored, and are believed only
    while *prev_refs* is still that object. Every site that discards the
    previous render (``_static_render`` after releasing the baseline, a
    ``_force_full_html`` sync, anything that assigns a new ``_prev_context_refs``)
    therefore discards these too, without having to know they exist.
    """
    stored = getattr(view, "_prev_request_scoped", None)
    if not prev_refs or type(stored) is not tuple or stored[0] is not prev_refs:
        return None
    fingerprints: Dict[str, Any] = stored[1]
    return fingerprints


def request_scoped_data_changed(view: Any, context: Dict[str, Any]) -> Optional[str]:
    """Name a context processor's plain-data value that differs from the last
    sync, or None (#3996).

    For the scoped component render (ADR-032), which sends a patch without a
    Rust sync: it runs the processors over *context* (the view's own context)
    and compares what they add with what the last sync kept. Values that ride
    the sidecar are not compared, as in the sync. With no trusted previous sync
    the answer is a reason, so the caller renders in full.
    """
    if not _per_turn.on("request_scoped_changes"):
        return None
    request = getattr(view, "request", None)
    if request is None:
        return None
    previous = _trusted_request_scoped_fingerprints(view, getattr(view, "_prev_context_refs", None))
    if previous is None:
        return "<no previous request-scoped values>"
    # Record what each processor returns. When the scoped render is refused,
    # the caller renders the page in this same event, and that render's sync
    # takes these outputs instead of calling every processor a second time.
    # They are dropped when the scoped patch is sent, and by the caller once
    # the page render is over (``runtime._drop_processor_replay``), and
    # ``_apply_context_processors`` takes them at most once and only for this
    # request object, so they cannot reach a later turn.
    view.__dict__.pop(PROCESSOR_REPLAY_ATTR, None)
    record: Dict[Any, Any] = {}
    view.__dict__[PROCESSOR_RECORD_ATTR] = record
    try:
        full = view._apply_context_processors(dict(context), request)
    finally:
        view.__dict__.pop(PROCESSOR_RECORD_ATTR, None)
    reason: Optional[str] = None
    seen: Set[str] = set()
    for key in request_scoped_keys(view):
        if key not in full:
            continue
        keeps_out, fingerprint, _converted = _request_scoped_fingerprint(key, full[key])
        if keeps_out or fingerprint is _NO_FINGERPRINT:
            continue
        seen.add(key)
        if previous.get(key, _MISSING) != fingerprint:
            reason = str(key)
            break
    else:
        for key in previous:
            if key not in seen:
                reason = str(key)
                break
    # Whatever the answer: a scoped render can still be refused after this
    # (no single node, a failed patch), and the page render then follows.
    view.__dict__[PROCESSOR_REPLAY_ATTR] = (request, record)
    return reason


def _unassigned_temporary_keys(view: Any) -> Set[str]:
    """The ``temporary_assigns`` keys that hold their reset default (#3639).

    After a render each such key is reset to a fresh copy of its default, so on
    the next sync it always looks changed (a new object whose fingerprint differs
    from the rows it held). That is why they were left out of the changed keys:
    treating the reset as a change would re-render the container with no rows.
    But a handler that ASSIGNS rows (``self.rows = [new_row]``) is a change, and
    leaving the key out as well meant the node reading it was not re-rendered
    and the new row never reached the page. So only a key still equal to its
    default is left out; a key holding anything else was assigned this turn.
    """
    out: Set[str] = set()
    for key, default in (getattr(view, "temporary_assigns", None) or {}).items():
        value = getattr(view, key, default)
        if isinstance(default, (list, dict, set, tuple)):
            same = type(value) is type(default) and len(value) == len(default) and value == default
        else:
            same = value is default or (type(value) is type(default) and value == default)
        if same:
            out.add(key)
    return out


def _temporary_key_aliases(view: Any, context: Any) -> Tuple[str, ...]:
    """Context keys that hold a ``temporary_assigns`` attribute's very object (#3647).

    ``get_context_data`` may hand the rows to the template under another name
    (``context["feed"] = self.rows``). A ``dj-update`` container reading ``feed``
    is fed by the temporary key just as one reading ``rows`` is, but nothing in the
    template says so. The two are the same object, so identity finds them; a value
    derived from the attribute (``list(self.rows)``, a slice, a filter) is a
    different object and is NOT found, which is a documented limit rather than a
    guess. Only mutable containers are compared: ``None``, ``0`` and ``""`` are
    shared by unrelated names.
    """
    declared = getattr(view, "temporary_assigns", None)
    if not declared or not isinstance(context, dict):
        return ()
    held = {}
    for key in declared:
        value = getattr(view, key, None)
        if isinstance(value, (list, dict, set)):
            held[id(value)] = key
    if not held:
        return ()
    return tuple(
        sorted(
            name for name, value in context.items() if name not in declared and id(value) in held
        )
    )


def _warn_rows_without_id(view: Any) -> None:
    """Say, once per view class and in DEBUG only, that rows without an ``id`` were dropped.

    An accumulating container adds a row only by its ``id`` (as the client does),
    so a row without one never reaches the page; without a word that reads as
    a lost update. The count comes from the render before this sync.
    """
    take = getattr(view._rust_view, "take_accumulate_dropped", None)
    dropped = take() if take is not None else 0
    cls = type(view)
    if not dropped or "_djust_warned_rows_without_id" in cls.__dict__:
        return
    from django.conf import settings

    if getattr(settings, "DEBUG", False):
        cls._djust_warned_rows_without_id = True  # type: ignore[attr-defined]
        logger.warning(
            "%s: %d row(s) in a dj-update container fed by temporary_assigns have no id "
            "attribute and were not added to the page. An appended or prepended row is "
            'added only by its id; give each row one (for example id="row-{{ r.pk }}").',
            cls.__name__,
            dropped,
        )


# ``_force_full_html`` holds one of three values (#4286):
#
# - falsy: nothing was requested;
# - ``True``: send this turn's render as full HTML (``request_full_render()``,
#   a restore, a dropped save, a catch-up), discarding the diff;
# - ``RENDER_REQUESTED``: ``set_changed_keys()`` asked for the turn to RENDER
#   (past the unchanged-state skip) but the render is still diffed and sent as
#   a ``patch``.
#
# The sentinel is truthy on purpose. Every site that bypasses the skip,
# consumes the flag or resyncs the full context to Rust keeps its truthiness
# test, so a site that forgets about the sentinel still renders; only the
# sites that DISCARD PATCHES test for ``True`` (:func:`full_html_requested`),
# and one that forgets fails safe as a full-HTML frame (the parallel-path
# drift class, #1646). A plain ``str`` so it pickles with the view.
RENDER_REQUESTED = "render"


def full_html_requested(view: Any) -> bool:
    """Whether ``view`` asked for this turn's render as full HTML (#4286).

    True only for an explicit full-page request (``_force_full_html is
    True``); ``set_changed_keys()``'s :data:`RENDER_REQUESTED` forces the
    render but keeps the diff.
    """
    return getattr(view, "_force_full_html", False) is True


class RustBridgeMixin:
    """Rust integration: _initialize_rust_view, _sync_state_to_rust."""

    # ADR-038 E2-1: keys _sync_state_to_rust adds for the Rust renderer.
    _djust_context_providers = (RUST_RENDER_PROVIDER,)

    if TYPE_CHECKING:
        # Cooperating attributes/methods supplied by the host class (LiveView)
        # and sibling mixins. Declared type-only so the strict-island mypy run
        # resolves them on this mixin without a runtime change — the real
        # definitions live on LiveView / the other mixins (this mixin is never
        # instantiated standalone). See streaming.py for the same pattern.
        request: Any
        _rust_view: Any
        # False, True (full HTML) or RENDER_REQUESTED (render, keep the diff).
        _force_full_html: Union[bool, str]
        _websocket_session_id: Optional[str]
        _django_session_key: Optional[str]
        _cached_csrf_token: Optional[str]
        _authenticated_template_source: Optional[str]

        def get_context_data(self, **kwargs: Any) -> Dict[str, Any]: ...

        def _context_for_render(
            self, preloaded_context: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]: ...

        def _apply_context_processors(
            self, context: Dict[str, Any], request: Any
        ) -> Dict[str, Any]: ...

        def get_template(self) -> str: ...

        _jit_serialized_keys: Set[str]

        def _jit_serialize_context(
            self,
            context: Dict[str, Any],
            template_content: str,
            keys: Optional[Iterable[str]] = None,
            into: Optional[Set[str]] = None,
        ) -> Set[str]: ...

        def _deep_serialize_dict(
            self, d: Dict[str, Any], template_content: Optional[str] = None, var_name: str = ""
        ) -> Dict[str, Any]: ...

        def _deep_serialize_sequence(
            self, items: List[Any], template_content: Optional[str] = None, var_name: str = ""
        ) -> List[Any]: ...

    #: Adaptive loop-cache bypass (#3071). After this many consecutive
    #: renders whose loop-cache hit rate is below
    #: ``_LOOP_CACHE_MIN_HIT_RATIO``, the view turns the loop render cache off
    #: for the rest of that view instance's lifetime: loop items that change on
    #: almost every render make the cache hash and track every item for no
    #: reuse (+20-30% render time measured). It is never re-probed; a new
    #: instance (a reconnect, another page load) starts with the cache on and
    #: measures again. ``0`` disables the bypass for a view class.
    _LOOP_CACHE_BYPASS_AFTER: ClassVar[int] = 8
    _LOOP_CACHE_MIN_HIT_RATIO: ClassVar[float] = 0.2

    def _apply_loop_render_cache_flag(self) -> None:
        """Wire ``LIVEVIEW_CONFIG['loop_render_cache_enabled']`` → Rust (#1967).

        Reads the config flag (default-True since #2062; explicit False is
        the opt-out kill-switch) and forwards it to the Rust
        ``RustLiveView`` via ``set_loop_render_cache_enabled``. Idempotent and
        cheap (a single bool set on the Rust side). Called on every
        ``_rust_view`` (re)initialization — including cache HITs, where the
        flag is not part of the serialized view state. A no-op if the Rust
        build predates the setter (defensive ``hasattr`` guard). A view that
        the adaptive bypass (#3071) switched off stays off.
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
        if getattr(self, "_loop_cache_bypassed", False):
            enabled = False
        rust_view.set_loop_render_cache_enabled(enabled)

    def _observe_loop_render_cache(self) -> None:
        """Count low-hit renders and switch the loop cache off after a streak (#3071).

        Reads the Rust cache's per-render counters after a full render. A
        render with no cacheable loop work (no hits, no misses) neither
        extends nor breaks the streak; a render at or above
        ``_LOOP_CACHE_MIN_HIT_RATIO`` resets it. Turning the cache off is safe
        at any render: this view renders the same output with the cache on or
        off (the cache only reuses fragments it would otherwise re-render), so
        the next diff is unaffected. The switch lasts for this instance.
        """
        rust_view = getattr(self, "_rust_view", None)
        limit = self._LOOP_CACHE_BYPASS_AFTER
        if (
            rust_view is None
            or limit <= 0
            or getattr(self, "_loop_cache_bypassed", False)
            or not hasattr(rust_view, "loop_render_cache_hits")
            or not rust_view.loop_render_cache_enabled()
        ):
            return
        hits = rust_view.loop_render_cache_hits()
        misses = rust_view.loop_render_cache_misses()
        if hits + misses == 0:
            return
        if hits / (hits + misses) >= self._LOOP_CACHE_MIN_HIT_RATIO:
            self._loop_cache_miss_streak = 0
            return
        streak = getattr(self, "_loop_cache_miss_streak", 0) + 1
        self._loop_cache_miss_streak = streak
        if streak >= limit:
            self._loop_cache_bypassed = True
            rust_view.set_loop_render_cache_enabled(False)
            logger.debug(
                "[LiveView] %s: loop render cache off after %d low-hit renders",
                type(self).__name__,
                streak,
            )

    def _initialize_rust_view(self, request: Any = None) -> None:
        """Initialize the Rust LiveView backend"""

        from .._exposure import ExposureError

        try:
            policy = getattr(self, "exposure_policy", None)
        except Exception:
            raise ExposureError("Invalid render exposure policy") from None
        if type(policy) is not str or policy != "explicit":
            raise ExposureError("Invalid render exposure policy")
        # A Rust view contains render context and a mutable VDOM baseline,
        # not the declared server-persistence projection. Keep it owned by
        # this instance; reconnect rebuilds it from authorized context and
        # the separately restored declared state.
        if self._rust_view is None or not getattr(self, "_rust_view_explicit", False):
            _ensure_custom_filters_bridged()
            explicit_template_source = self.get_template()
            if getattr(self, "_authenticated_partial_root", False):
                explicit_template_source = (
                    self._authenticated_template_source or explicit_template_source
                )
            self._rust_view = RustLiveView(explicit_template_source, get_template_dirs())
            if getattr(self, "_authenticated_partial_root", False):
                self._rust_view.set_authenticated_partial_root(True)
                self._rust_view.set_authenticated_view_path(
                    f"{getattr(type(self), '__module__', '<unknown>')}.{type(self).__name__}"
                )
                self._rust_view.set_template_origin(
                    getattr(self, "_authenticated_partial_root_origin", None)
                )
            self._apply_loop_render_cache_flag()
            self._rust_view_explicit = True

    def set_changed_keys(self, keys: Union[str, Iterable[str], None] = None) -> None:
        """Mark one or more public attrs as changed, forcing a re-render.

        djust's auto change-detection snapshots public state with a structural
        fingerprint (``djust.change_detection.deep_fingerprint``, #2664): an
        in-place mutation anywhere inside a dict/list/set of plain data —
        ``self.rows[0]["done"] = True``, ``self.columns["done"].append(card)``
        — IS detected and re-rendered without any call here. What the
        fingerprint cannot see is an attribute write on an OPAQUE object held
        in state (a model instance, a form, any non-container object), which is
        compared by identity, and a container so large it exceeds the
        fingerprint budget (a one-shot warning names the attribute). See the
        "Nested state" note in ``docs/website/guides/state-primitives.md``.

        Call this from an event handler AFTER such a mutation to force a
        re-render::

            def rename(self, i: int, name: str = ""):
                self.rows[i].name = name  # in-place write on a model instance
                self.set_changed_keys("rows")

        This forces the view to re-render this turn. The render is still
        diffed and sent as a ``patch`` (#4286), so DOM state the server does
        not render (an open native ``<dialog>``, focus, a caret) survives it;
        use :meth:`request_full_render` for a full-HTML frame. Context is
        resynced to the renderer in full, so for a large view on a hot path
        prefer building a NEW value immutably instead — djust tracks that
        change precisely::

            self.rows = [
                {**r, "done": not r["done"]} if j == i else r
                for j, r in enumerate(self.rows)
            ]

        Zero-arg form (#1992): call ``set_changed_keys()`` with NO arguments
        when a handler changed only EXTERNAL state (a DB row, a cache) and
        touched no public ``self.*`` attribute, but ``get_context_data()`` will
        return different HTML on the next render (it re-queries the DB). Auto
        change-detection sees no changed attr and would auto-skip the event; the
        zero-arg form forces the re-render without naming a key::

            def switch_branch(self, message_id, child_id):
                msg = Message.objects.get(id=message_id)
                msg.active_child_id = child_id
                msg.save(update_fields=["active_child_id"])  # DB only, no self.*
                self.set_changed_keys()                       # force the re-render

        Args:
            keys: an attr name, or an iterable of attr names, to mark changed.
                Omit entirely (``None``) for the zero-arg force-render form above
                — a DB/external-only change with no changed public attr.

        This is also the invalidation API for ``exposure_policy = "explicit"``
        views (ADR-038 D3), with the same behaviour: explicit context derived
        from declared ``state()`` fields or plain attributes re-renders on its
        own, and this call covers what no snapshot can see (an attribute write
        on an opaque object, a DB/external change). A named key may be the
        public field name; either form forces the render (sent as a diff).

        Note:
            Distinct from the Rust-side ``RustLiveView.set_changed_keys`` (the
            PyO3 partial-sync primitive `_sync_state_to_rust` drives internally)
            — this Python-level method is the user-facing "force a re-render"
            hatch and does not talk to the Rust view directly.
        """
        # Remember whether a full render was already pending before this
        # turn's first public request, so ``cancel_full_render()`` can undo
        # exactly what the request added (#4129) and never a full render the
        # framework itself required (a restore, a dropped save).
        self._record_full_render_prior()
        if keys is None:
            # Zero-arg form (#1992): the handler changed only EXTERNAL state (a
            # DB row, a cache) with no public ``self.*`` change, but
            # ``get_context_data()`` will render different HTML next time. Force
            # the re-render without naming a key — ``_request_render()`` below
            # is the actual bypass; there is no key to add to ``_changed_keys``.
            self._request_render()
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
        self._request_render()

    def _record_full_render_prior(self) -> None:
        # Remember whether a render was already pending before this turn's
        # first public request, so ``cancel_full_render()`` can undo exactly
        # what the request added (#4129). The raw value: a framework ``True``
        # restores as ``True`` (#4286).
        if "_djust_full_render_prior" not in self.__dict__:
            self._djust_full_render_prior = getattr(self, "_force_full_html", False) or False

    def _request_render(self) -> None:
        # set_changed_keys() forces the RENDER, not a full page (#4286): the
        # sentinel bypasses every skip but keeps the diff. Upgrade only — a
        # full-HTML request already pending (a restore, a dropped save,
        # request_full_render()) stays a full page.
        if getattr(self, "_force_full_html", False) is not True:
            self._force_full_html = RENDER_REQUESTED

    # --- public render controls (#4129) ------------------------------------

    def skip_render(self) -> None:
        """Do not render this turn.

        Call from an event handler, a ``@push_handler`` / ``handle_info``, a
        ``handle_tick()`` or any other hook that ends in a render: the turn
        sends its push events and frames but no HTML or patches. The request
        lasts one turn — the framework consumes it whether or not the turn
        would have rendered.

        A render requested in the same turn (``set_changed_keys()``,
        :meth:`request_full_render`) wins over this: the forced render is
        never dropped silently (#2834).

        Example::

            @event_handler
            def ping(self, **kwargs):
                self.push_event("pong", {})
                self.skip_render()   # nothing on the page changed

        Replaces assigning ``self._skip_render = True``, which keeps working.
        """
        self._skip_render = True

    def rows_changed(self, *tables: Any) -> None:
        """Read the rows this view holds again at its next render (#4112).

        djust does this by itself after a step of the view writes to the
        database. Call it for a write it cannot see: one made in a thread or an
        ``on_commit`` callback the handler started, by another process (call it
        from the ``handle_info`` / push handler that tells the view), through
        ``cursor.callproc()``, or by a ``SELECT`` with side effects
        (``nextval()``, a function that writes). The QuerySets and cached
        properties the view holds are emptied before its next render, and a turn
        that calls it renders even if nothing else on the view changed.

        ``tables`` narrows it, as djust narrows its own writes: model classes or
        table names. Only what reads one of them is read again. With none, it
        is everything the view holds.

        Example::

            @push_handler
            def order_shipped(self, **payload):
                self.rows_changed(Order)   # another process wrote the orders
        """
        from .._held_rows import mark_tree, named_tables

        mark_tree(self, named_tables(tables))

    def request_full_render(self) -> None:
        """Send this turn's render as full HTML rather than as a diff.

        Like the zero-argument ``set_changed_keys()`` it forces the render
        after a change djust cannot see, but the page is replaced rather than
        patched. ``set_changed_keys()`` sends a diff (#4286); use this only
        when a diff against the browser's DOM is not wanted. Undo it in the
        same turn with :meth:`cancel_full_render`.

        Replaces assigning ``self._force_full_html = True``, which keeps working.
        """
        self._record_full_render_prior()
        self._force_full_html = True

    def cancel_full_render(self) -> None:
        """Undo a full render requested earlier in this turn.

        Reverts what ``set_changed_keys()`` / :meth:`request_full_render`
        asked for since the turn began, and nothing else: a full render the
        framework itself requires (after a restore whose state differs from
        the page, or after a save that failed) stays, because the page and
        the server's diff baseline would otherwise disagree. Keys named in
        ``set_changed_keys(keys)`` stay marked changed; only the render
        request (full HTML from ``request_full_render()``, or the diffed
        render from ``set_changed_keys()``) is withdrawn. A no-op when nothing was requested.

        Replaces assigning ``self._force_full_html = False``, which keeps
        working (and, unlike this method, also clears a framework request).
        """
        prior = self.__dict__.pop("_djust_full_render_prior", None)
        if prior is not None:
            self._force_full_html = prior

    def reset_submitted_form(self) -> None:
        """Clear the submitted form in the browser after this turn.

        The client resets the form whose submit started the event, so its
        inputs show their initial values instead of what was typed. For a
        view that does not use ``FormMixin`` (whose ``reset_form()`` also
        resets its server-side form state and calls this flag for you).

        Example::

            @event_handler
            def add_comment(self, text: str = "", **kwargs):
                Comment.objects.create(text=text)
                self.reset_submitted_form()

        Replaces assigning ``self._should_reset_form = True``, which keeps
        working.
        """
        self._should_reset_form = True

    def _apply_render_env(self) -> None:
        """Wire per-render Django settings → Rust for THIS render (#2209, #2221).

        Delegates to :func:`djust.render_env.apply_render_env`, which is shared
        with ``simple_live_view`` — that module renders through
        ``render_template_with_dirs`` and never touches this mixin, so a second
        copy here would have been the #1646 twin this fix exists to retire.

        ADR-029 (#2741): the push above sets THIS thread's cells; the capture
        below snapshots them onto the view as per-view config, so a render
        entry running on a thread that never pushed installs the configured
        values rather than reading that thread's compiled defaults.
        """
        apply_render_env()
        rust_view = self._rust_view
        if rust_view is not None and hasattr(rust_view, "capture_render_env"):
            rust_view.capture_render_env()

    def _csrf_token_for_render(self, request: Optional[Any] = None) -> Optional[str]:
        """The CSRF token ``{% csrf_token %}`` renders, or None without a request.

        Shared by the dj-root render (``_sync_state_to_rust``, #696) and the
        page-shell render (``render_full_template``, #3294): both feed the Rust
        engine, which has no ``RequestContext`` to supply Django's built-in
        ``csrf`` processor. Cached so the change tracker sees one stable string.
        """
        cached: Optional[str] = getattr(self, "_cached_csrf_token", None)
        if cached is not None:
            return cached
        request = request if request is not None else getattr(self, "request", None)
        if request is None:
            return None
        try:
            from django.middleware.csrf import get_token

            token: str = get_token(request)
        except Exception:
            logging.getLogger("djust.rust_bridge").warning(
                "Failed to inject csrf_token into Rust context",
                exc_info=True,
            )
            return None
        self._cached_csrf_token = token
        return token

    def _explicit_jit_serialize(self, context: Any, keys: Set[str], nested_keys: List[str]) -> None:
        """Run the legacy JIT over an explicit context's walked names (#4069 review S1).

        ``keys``: top-level QuerySets / model lists whose rows the template walks past
        their columns. ``nested_keys``: plain dicts / lists holding a QuerySet / Model
        inside a container (the #3788 walks). A JIT
        failure is logged and leaves the value to the normalize pass (the rows captured
        for the sidecar still answer the loop alias), as legacy's JIT failure does; a
        recursive partial re-raises, as on legacy.
        """
        from .context import _jit_serialization_enabled
        from .jit import _jit_template_content, drop_nested_evaluated, nested_raw_twin

        if not _jit_serialization_enabled():
            return
        template_content: Optional[str] = None
        try:
            template_content = _jit_template_content(self)
            if not template_content:
                return
            self._nested_template_source = [template_content]
            # One name at a time (#4290 review): a name whose serializer raises leaves only
            # itself to the normalize pass, not every name after it.
            for key in sorted(keys):
                try:
                    self._jit_serialize_context(
                        context,
                        template_content,
                        keys={key},
                        into=self._jit_serialized_keys,
                    )
                except Exception as exc:  # noqa: BLE001 - the normalize pass serializes it
                    if "djust:recursive-partial" in template_content:
                        raise
                    from .._exposure_diagnostics import log_failure_for

                    log_failure_for(
                        logger,
                        (self,),
                        exc,
                        "JIT auto-serialization failed",
                        level="warning",
                        traceback=True,
                    )
            for key in nested_keys:
                value = context[key]
                walk = (
                    self._deep_serialize_dict
                    if type(value) is dict
                    else self._deep_serialize_sequence
                )
                dict.__setitem__(context, key, walk(value, template_content, key))
                # #4326: a nested QuerySet whose own ``all`` / methods the template reads.
                nested_raw_twin(self, key, value)
            drop_nested_evaluated(self)
        except Exception as exc:  # noqa: BLE001 - the normalize pass serializes what is left
            if template_content and "djust:recursive-partial" in template_content:
                raise
            from .._exposure_diagnostics import log_failure_for

            # Value-free on an explicit owner: the exception text can carry row data.
            log_failure_for(
                logger,
                (self,),
                exc,
                "JIT auto-serialization failed",
                level="warning",
                traceback=True,
            )

    @recorded_render
    def _sync_state_to_rust(self, preloaded_context: Optional[Dict[str, Any]] = None) -> bool:
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

        Runs inside ``LIVEVIEW_CONFIG["sync_turn_wrapper"]``'s ``"render"`` step
        as a whole (#4276): not only ``get_context_data()`` but the queries the
        sync makes after it (the explicit JIT serialization, the rows listed for
        the template's sidecar, the normalize pass). Inside a render step
        already (a ``render*`` method), it nests and enters nothing again.

        Returns:
            Whether the next ``render_with_diff()`` has anything to render
            (#4123). ``False`` only when the sync found no changed and no
            removed key since the previous sync, no render was requested
            (``_force_full_html`` / ``set_changed_keys()``) and no context
            value is an object compared only by ``id()`` (one an in-place
            write can change unseen; it always renders): Rust then has no
            changed keys and would render the whole template to send an empty
            patch list. ``True`` on a first render and without a Rust view.
            A server-originated render (an async result) may skip its render
            on ``False``; an event turn already decided with its own snapshot.
        """
        from ..sync_turn import run_sync_step

        return bool(run_sync_step(self, "render", self._sync_state_to_rust_body, preloaded_context))

    def _sync_state_to_rust_body(self, preloaded_context: Optional[Dict[str, Any]] = None) -> bool:
        """:meth:`_sync_state_to_rust`, inside its ``"render"`` step."""
        render_needed = True
        if self._rust_view:
            # Per-render, before anything is formatted (#2209, #2221).
            self._apply_render_env()

            # The post-event snapshot's walks, taken before the context is
            # built (#3999). None unless no application code runs from the
            # snapshot to here (``_snapshot_walks_for_sync``).
            snapshot_walks = _snapshot_walks_for_sync(
                self, getattr(self, "_changed_keys", None), preloaded_context
            )

            # One place decides: preloaded, the GET's cached context, or a
            # fresh evaluation (#3725). Calling ``self.get_context_data`` here
            # ran an overriding view's body a second time on every GET.
            full_context = self._context_for_render(preloaded_context)

            # Context keys that are a temporary attribute under another name (#3647);
            # before the normalize pass below, which copies what it converts.
            _context_aliases = _temporary_key_aliases(self, full_context)

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
            # #4241: a top-level QuerySet is captured too, but only when the
            # template reads ``{{ qs.count }}`` / ``.exists`` / ``.first`` /
            # ``.last`` on its own name -- the same condition the JIT uses. A
            # loop row's ``{{ o.first }}`` does not count (#4258). The answer
            # is computed once per sync, and only when a QuerySet is present. Holding every
            # QuerySet would make its other attributes (``{{ qs.query }}``)
            # reachable through the sidecar walk. The normalize loop below
            # iterates this same object, filling its result cache, so
            # ``count`` / ``exists`` cost no extra query; ``first`` / ``last``
            # are Django's own methods and query as Django does (``last``
            # always, ``first`` on an unordered QuerySet).
            from django.db.models import Model as _DjModel
            from django.db.models import QuerySet as _DjQuerySet

            from .jit import rows_need_live_walk

            # #3936: the raw model values behind state keys, for the render-time
            # handle overlay (flag ``state_models_as_handles``). An explicit context
            # still holds them here, before the normalize loop below. Empty with the
            # flag off.
            _overlay_raw: Dict[str, Any] = (
                _state_model_values(full_context)
                if type(full_context) is ExplicitRenderContext
                else {}
            )

            _raw_models_for_sidecar = {}
            _explicit_jit_keys: Dict[str, Any] = {}
            # #4290: the DB values of an explicit context whose rows the template does NOT
            # walk (a Model, a model list / QuerySet read only by its columns). They go
            # through the JIT too, reduced to the paths the template reads, as 1.x's did;
            # the normalize pass below copied every field of them into the renderer's state.
            # Kept apart from ``_explicit_jit_keys``: the overlay still hands these a handle,
            # and the sidecar keeps no evaluated rows for them.
            _explicit_plain_keys: Set[str] = set()
            _explicit = type(full_context) is ExplicitRenderContext
            # Names whose DB values the JIT or the normalize pass rewrites: the
            # snapshot's walk of them is never reused (#3999).
            _rewritten_keys: Set[str] = set()
            _qs_roots: Optional[FrozenSet[str]] = None
            _walk_paths: Optional[Dict[str, FrozenSet[str]]] = None
            for _key, _val in full_context.items():
                if isinstance(_val, _DjModel):
                    _raw_models_for_sidecar[_key] = _val
                    if _explicit:
                        if _walk_paths is None:
                            _walk_paths = _queryset_row_walk_paths_for(self)
                        _m_paths = _walk_paths.get(_key) or frozenset()
                        # #4290: a Model the template reads only columns / properties off is
                        # reduced to them. One whose relations or methods it walks keeps the
                        # normalized dict, which the sidecar and the overlay handle answer
                        # as before (a forward FK read whole prints its ``__str__``).
                        if not (
                            rows_need_live_walk(type(_val), _m_paths, full_context)
                            or _paths_read_a_relation(type(_val), _m_paths)
                        ) and _plain_rows_take_the_jit(_val, type(_val), _m_paths, full_context):
                            _explicit_plain_keys.add(_key)
                    continue
                _is_qs = isinstance(_val, _DjQuerySet)
                if _is_qs:
                    if _qs_roots is None:
                        _qs_roots = _queryset_method_roots_for(self)
                    if _key in _qs_roots:
                        _raw_models_for_sidecar[_key] = _val
                        # Review S1: an explicit view's method-read QuerySet whose rows
                        # the template ALSO walks goes on to the walk check below, which
                        # hands it to the JIT as legacy's does, so each row dict carries
                        # the relation values and a
                        # filtered rebinding of a row (``{% with r=x|default:x %}``)
                        # reads them. Legacy's JIT already ran (context.py).
                        if not _method_read_rows_take_the_jit(full_context):
                            continue
                # #4069: a loop row's relation walk (``{% for u in users %}
                # {{ u.groups.all }}``) is answered through the sidecar alias
                # ``u -> users.<N>``; the normalized column dicts cannot answer
                # it. Keep the EVALUATED ROWS (never the QuerySet, so
                # ``{{ users.query }}`` stays unreachable, #4241) when the
                # template walks past a row's columns. Such a name first goes
                # through the JIT (#4069 review S1, below); only a name the JIT
                # did not serialize keeps its rows, floored with every other
                # sidecar value below.
                # Explicit only: legacy's JIT serialized these rows with their
                # relation paths, and reaches here raw only when the JIT failed
                # (logged there; a re-walk would raise the same error again).
                if type(full_context) is not ExplicitRenderContext:
                    continue
                if _is_qs or (
                    isinstance(_val, (list, tuple))
                    and _val
                    and any(isinstance(_r, _DjModel) for _r in _val)
                ):
                    if _walk_paths is None:
                        _walk_paths = _queryset_row_walk_paths_for(self)
                    _paths = _walk_paths.get(_key)
                    _model_cls = (
                        _val.model
                        if _is_qs
                        else next(type(_r) for _r in _val if isinstance(_r, _DjModel))
                    )
                    if not _paths:
                        # No field path read off the rows (``{% regroup %}``, ``{{ rows.0 }}``,
                        # ``|length``): whole rows, as the JIT's own no-path list gives
                        # (#2736), so the normalize pass below serializes them.
                        continue
                    if rows_need_live_walk(_model_cls, _paths, full_context) or (
                        # Review N4: a forward FK read as one segment (``{{ p.content_type }}``)
                        # was walked per row through the sidecar alias (2N+1 queries); the JIT
                        # selects it with the rows, as legacy's does.
                        _is_qs and _explicit_fk_rows_take_the_jit(_model_cls, _paths)
                    ):
                        _explicit_jit_keys[_key] = _val
                    elif _plain_rows_take_the_jit(_val, _model_cls, _paths, full_context):
                        _explicit_plain_keys.add(_key)

            # #4069 review S1: an explicit view's walked rows also go through the
            # JIT, as legacy's do, so each row dict CARRIES the relation values the
            # template reads. A filtered operand (``users|slice``, ``dictsort``,
            # ``{% with %}``, ``|first``) has no sidecar alias and answers from those
            # dicts; the row cap applies; and the value is part of the loop render
            # cache's item hash. A dict holding a QuerySet / Model goes through the
            # template-aware container walk (#3788). The captured rows above stay for
            # the method / alias shapes the JIT does not serialize.
            # A container twin (#4326) from an earlier render: this render's walk
            # holds its own again. Not reset with the rest on a preloaded context.
            from .jit import drop_nested_twins

            drop_nested_twins(self)
            if type(full_context) is ExplicitRenderContext:
                # ``_all_method_rows`` is NOT reset here: ``serialize_for_template``
                # in the view's own ``get_context_data`` already wrote it this render.
                self._jit_serialized_keys = set()
                _nested_db_keys = [
                    _key
                    for _key, _val in full_context.items()
                    if _key not in _explicit_jit_keys
                    and (type(_val) is dict or type(_val) is list)
                    and not any(isinstance(_r, _DjModel) for _r in _val)
                    and _holds_db_value(_val)
                ]
                _rewritten_keys.update(_explicit_jit_keys, _explicit_plain_keys, _nested_db_keys)
                if _explicit_jit_keys or _explicit_plain_keys or _nested_db_keys:
                    self._explicit_jit_serialize(
                        full_context,
                        set(_explicit_jit_keys) | _explicit_plain_keys,
                        _nested_db_keys,
                    )
                # A name the JIT did not serialize keeps its evaluated rows for the
                # sidecar alias walk. A serialized one needs none (as on legacy), and
                # evaluating it here would cost the outer query a second time.
                for _key, _val in _explicit_jit_keys.items():
                    if _key not in self._jit_serialized_keys:
                        _raw_models_for_sidecar[_key] = list(_val)
                    elif _key in _raw_models_for_sidecar:
                        # A method-read QuerySet the JIT serialized (review S1): the
                        # method answers come from the JIT's EVALUATED QuerySet
                        # (``_all_method_rows``), not the caller's unevaluated one,
                        # so ``{{ qs.count }}`` costs no second query.
                        _evaluated = (getattr(self, "_all_method_rows", None) or {}).get(_key)
                        if _evaluated is not None:
                            _raw_models_for_sidecar[_key] = _evaluated
                # #4290: the same for a method-read QuerySet whose rows the template reads
                # only columns off; the JIT's evaluated rows answer ``{{ qs.count }}``.
                for _key in _explicit_plain_keys:
                    if _key in self._jit_serialized_keys and isinstance(
                        _raw_models_for_sidecar.get(_key), _DjQuerySet
                    ):
                        _evaluated = (getattr(self, "_all_method_rows", None) or {}).get(_key)
                        if _evaluated is not None:
                            _raw_models_for_sidecar[_key] = _evaluated

            for _key, _val in list(full_context.items()):
                _normalized = _normalize_db_values(_val)
                if _normalized is not _val:
                    if type(full_context) is ExplicitRenderContext:
                        # A framework rewrite of the same value, which the
                        # explicit context refuses for a provider key (E2-1).
                        dict.__setitem__(full_context, _key, _normalized)
                    else:
                        full_context[_key] = _normalized

            # ADR-038 E2-1: csrf_token / DATE_FORMAT / TIME_FORMAT below are
            # framework-provided. An explicit view's own context may not supply
            # them, since the "if not in" injection would silently defer to it.
            require_no_provider_keys(self, full_context, RUST_RENDER_PROVIDER)

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
            # stay in ``update_state`` and are unaffected, except plain data a
            # processor marked ``RequestScopedList`` (``theme_presets``, #3631),
            # which rides the sidecar like the non-serializable ones.
            _request_scoped_keys = request_scoped_keys(self)

            # Ensure csrf_token is available for {% csrf_token %} tag (#696).
            # Cache it to avoid creating a new string object each call,
            # which would cause the change tracker to see it as "changed".
            if "csrf_token" not in full_context:
                token = self._csrf_token_for_render()
                if token is not None:
                    full_context["csrf_token"] = token

            # Inject DATE_FORMAT / TIME_FORMAT from Django settings so the
            # Rust |date and |time filters honour the project's configured
            # formats when no explicit format argument is given (#713).
            from django.conf import settings as _dj_settings

            for _fmt_key in ("DATE_FORMAT", "TIME_FORMAT"):
                if _fmt_key not in full_context:
                    _fmt_val = getattr(_dj_settings, _fmt_key, None)
                    if _fmt_val is not None:
                        full_context[_fmt_key] = _fmt_val

            # No context narrowing here (#2739). A filter that dropped
            # ``_component_descriptors`` names the template never referenced
            # used to sit at this point; it read the template source from an
            # attribute nothing ever assigned, so it never fired once. It was
            # deleted rather than wired up: #2737 established that narrowing
            # the context by a template read-set is unsound while
            # ``build_py_context`` (crates/djust_templates/src/registry.rs)
            # hands every bridged tag handler the WHOLE context, and the
            # dependency analysis still has open holes (#2738). The JIT's
            # ``variable_paths_map`` (mixins/jit.py) is the one sanctioned
            # narrowing, and it falls back to "everything" on any doubt.

            changed_keys = getattr(self, "_changed_keys", None)
            prev_refs = getattr(self, "_prev_context_refs", {})
            # Cache of previous VALUES for immutable context keys. Needed
            # because `id()` comparison is unreliable for small ints/strings
            # (Python interns them), so we fall back to value equality for
            # these types. Catches derived immutables like
            # `completed_count = sum(...)` that change without their source
            # name appearing in `_changed_keys`.
            prev_immutables = getattr(self, "_prev_context_immutables", {})
            # What the previous render kept of its containers' structural
            # fingerprints (#2664): a 16-byte digest each (#3998). Computed at
            # most once per key per sync (compare + store).
            prev_fps = getattr(self, "_prev_context_fingerprints", {})
            new_fps: Dict[str, Any] = {}
            # Safe-key paths + rendered value of a key whose fingerprint was
            # taken in the same walk (``state_pass``): the key is being sent,
            # so the walk below that finds its SafeStrings and converts it for
            # Rust need not run again.
            converted_by_fp: Dict[str, Tuple[List[str], Any]] = {}

            # ADR-031 / #2900: a class-level component is an opaque wrapper
            # whose State is the thing that changes. ``fingerprints_by_content``
            # / ``deep_fingerprint`` walk it as its State — the ONE rule every
            # snapshot shares — so a nested in-place mutation such as
            # ``self.state["rows"].append(x)`` (no dirty flag) is seen here
            # exactly as ``_snapshot_assigns`` sees it.
            def _is_structural(value: Any) -> bool:
                return isinstance(value, (dict, list, tuple)) or fingerprints_by_content(value)

            def _fp_differs(key: str, value: Any) -> bool:
                """Did *key*'s fingerprint change since the last sync?"""
                return bool(prev_fps.get(key, _MISSING) != _fp_of(key, value))

            def _snapshot_walk(key: str, value: Any) -> Optional[SnapshotEntry]:
                """The post-event snapshot's walk of this very object, if reusable."""
                if snapshot_walks is None or key in _rewritten_keys:
                    return None
                entry = snapshot_walks.get(id(value))
                if entry is None or entry[0] is not value:
                    return None
                return entry

            def _fp_of(key: str, value: Any, convert: bool = False) -> Any:
                fp = new_fps.get(key, _MISSING)
                if fp is _MISSING:
                    entry = _snapshot_walk(key, value)
                    if entry is not None:
                        # The snapshot walked this object after the handler and
                        # nothing ran since (#3999): its tree is this value's
                        # fingerprint. Converting it for Rust, when it is sent,
                        # is a walk that builds no fingerprint.
                        fp = retained_fingerprint(entry[1])
                        new_fps[key] = fp
                        if convert:
                            converted_walk = convert_for_render(value, key)
                            if converted_walk is not None:
                                converted_by_fp[key] = converted_walk
                        return fp
                    if convert:
                        fp, truncated, converted = fingerprint_and_convert(value, key)
                        if converted is not None:
                            converted_by_fp[key] = converted
                    else:
                        fp, truncated = deep_fingerprint(value)
                    if truncated:
                        # An incomplete fingerprint cannot prove equality.
                        fp = object()
                        warn_fingerprint_truncated(type(self), key, value)
                    else:
                        # The tree is dropped here; the digest is what is
                        # compared and kept (#3998).
                        fp = retained_fingerprint(fp)
                    new_fps[key] = fp
                return fp

            # _force_full_html: bypass ALL change tracking and send everything.
            # The websocket code skips _changed_keys computation when this is
            # set (line 1991), so we must treat it as a first render (#783).
            if getattr(self, "_force_full_html", False):
                prev_refs = {}

            # Request-scoped PLAIN DATA takes part in change detection (#3996).
            #
            # Until now every request-scoped key was left out of the previous
            # render's bookkeeping (#1786), so each one looked new on every
            # sync: its serialisable values were converted and handed to
            # ``update_state`` again on every turn, and because the same keys
            # were also left out of the changed keys, a value that DID change
            # was stored but the node reading it was not rendered again.
            #
            # Now a value that ``update_state`` stores and that is made of
            # exact plain types is fingerprinted like any other key: unchanged,
            # it is not sent; changed (or new), it is sent AND reported as a
            # changed key. What #1786 keeps out stays out: a value that rides
            # the sidecar (the request, ``user``, ``perms``, messages, a lazy
            # chunk) is never fingerprinted, kept or reported, and the
            # fingerprints live in their own attribute, never in
            # ``_prev_context_refs``. See ``_request_scoped_fingerprint``.
            #
            # ``rs_keeps_out`` holds the keeps-out verdict of every
            # request-scoped key in this context, so it is asked once per sync.
            rs_on = _per_turn.on("request_scoped_changes")
            rs_keeps_out: Dict[str, bool] = {}
            rs_fps: Dict[str, Any] = {}
            rs_unchanged: Set[str] = set()
            if rs_on:
                rs_prev = _trusted_request_scoped_fingerprints(self, prev_refs)
                for key in _request_scoped_keys:
                    if key not in full_context:
                        continue
                    keeps_out, rs_fp, rs_converted = _request_scoped_fingerprint(
                        key, full_context[key]
                    )
                    rs_keeps_out[key] = keeps_out
                    if rs_fp is _NO_FINGERPRINT:
                        continue
                    rs_fps[key] = rs_fp
                    if rs_prev is not None and rs_prev.get(key, _MISSING) == rs_fp:
                        rs_unchanged.add(key)
                    elif rs_converted is not None:
                        converted_by_fp[key] = rs_converted

            # A value compared only by ``id()`` (an object the detector cannot
            # fingerprint) may have changed in place without the sync seeing
            # it. Such a value makes this sync report a render as needed, so
            # a skippable render never drops it (#4123).
            opaque_seen = False

            # Determine which context to send to Rust
            if prev_refs:
                if changed_keys:
                    # Explicit change tracking from handle_event's snapshot
                    # detection. Trust it exclusively — don't also run id()
                    # comparison which produces false positives due to Python
                    # int cache misses on the double-sync path.
                    changed_sub_ids: Set[int] = set()
                    # A changed attribute that is itself a context value, and is
                    # plain data, needs no walk for the ids under it (#3999).
                    # The ids find a context value that IS an object inside the
                    # attribute. Inside plain data such an object is either a
                    # dict / list / tuple / set, which the loop below compares by
                    # structural fingerprint anyway, or an immutable leaf, which
                    # cannot have changed while it is the same object. "Plain"
                    # is decided by the walk that converts the value for Rust
                    # (``fingerprint_and_convert`` converts only exact builtin
                    # containers and exact immutable leaf types), taken now, on
                    # this very object, and reused below as the key's
                    # fingerprint and rendered value: the key is being sent
                    # (its own id is still added), so that walk ran regardless.
                    # When the post-event snapshot's walk of the attribute may
                    # be reused (``snapshot_walks``), that walk already says
                    # whether it is plain, for any changed attribute, whether
                    # or not it is itself a context value, and nothing is
                    # walked here at all.
                    plain_on = _per_turn.on("plain_sub_ids")
                    context_key_of: Optional[Dict[int, str]] = None
                    for key in changed_keys:
                        val = getattr(self, key, None) or full_context.get(key)
                        if val is None:
                            continue
                        # Plain data, by the post-event snapshot's own walk of
                        # this object (#3999): nothing under it can be a context
                        # value that only ``id()`` would find. Covers a changed
                        # attribute that is not itself a context value too.
                        walked = (
                            snapshot_walks.get(id(val))
                            if plain_on and snapshot_walks is not None
                            else None
                        )
                        if walked is not None and walked[0] is val and walked[2]:
                            changed_sub_ids.add(id(val))
                            continue
                        if plain_on and type(val) in (dict, list, tuple):
                            if context_key_of is None:
                                context_key_of = {
                                    id(v): k
                                    for k, v in full_context.items()
                                    if type(v) in (dict, list, tuple)
                                    and k not in _request_scoped_keys
                                }
                            ctx_key = context_key_of.get(id(val))
                            if ctx_key is not None and full_context[ctx_key] is val:
                                _fp_of(ctx_key, val, convert=True)
                                if ctx_key in converted_by_fp:
                                    changed_sub_ids.add(id(val))
                                    continue
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
                    # ADR-038 E2-8: for explicit views this loop is the whole
                    # bridge. Their changed keys are storage attributes
                    # (`_state_count`, `wizard_step_index`) that never match
                    # a context key (`doubled`, a provider's `current_step`),
                    # so every rendered key reaches Rust through this
                    # comparison, not through the name match above.
                    #
                    # Containers (dict, list, tuple) compare by STRUCTURAL
                    # fingerprint (#2664), not id() and not ``==`` against a
                    # kept reference: id() is unreliable for derived containers
                    # (CPython address reuse, #774) and a kept reference IS the
                    # mutated object after an in-place edit, so it compares
                    # equal to itself (#1039 aliasing) — the ``patches: []``
                    # frame of #2664. Unchanged containers are still skipped.
                    #
                    # Immutables use value equality (Python interns them).
                    # Other types use id() comparison as a last resort.
                    for key, value in full_context.items():
                        if key in context:
                            continue
                        if key in rs_keeps_out:
                            if key not in rs_unchanged:
                                context[key] = value  # request-scoped (#3996)
                        elif key not in prev_refs:
                            context[key] = value  # new key
                        elif getattr(value, "_dirty", False):
                            context[key] = value  # TypedState dirty flag
                        elif changed_sub_ids and id(value) in changed_sub_ids:
                            context[key] = value  # sub-object of changed (#703)
                        elif _is_structural(value):
                            if _fp_differs(key, value):
                                context[key] = value
                        elif isinstance(value, _IMMUTABLE_TYPES_FOR_SYNC):
                            # Immutable: compare by value to catch derived
                            # int/str changes (prev_immutables may miss the
                            # key if it wasn't immutable last time — treat
                            # that as "changed" since the type flipped).
                            if prev_immutables.get(key, _MISSING) != value:
                                context[key] = value
                        else:
                            opaque_seen = True
                            if id(value) != prev_refs.get(key):
                                context[key] = value  # derived value with new id()
                else:
                    # No explicit changed_keys — use id() comparison as fallback.
                    # This path runs on the internal sync from render_with_diff
                    # and for in-place mutations without snapshot detection.
                    context = {}
                    for key, value in full_context.items():
                        if key in rs_keeps_out:
                            if key not in rs_unchanged:
                                context[key] = value  # request-scoped (#3996)
                        elif key not in prev_refs:
                            context[key] = value  # new key
                        elif getattr(value, "_dirty", False):
                            context[key] = value  # TypedState dirty flag
                        elif _is_structural(value):
                            if _fp_differs(key, value):
                                context[key] = value
                        elif isinstance(value, _IMMUTABLE_TYPES_FOR_SYNC):
                            if prev_immutables.get(key, _MISSING) != value:
                                context[key] = value
                        else:
                            opaque_seen = True
                            if id(value) != prev_refs.get(key):
                                context[key] = value  # different object reference
            else:
                # First render: send everything
                context = full_context

            # Store refs for next comparison (full context, not filtered).
            # We also cache previous VALUES for types where id() is unreliable:
            # - Immutables (int/str/etc.) — Python interns them, so id() gives
            #   false negatives. Compared by value equality.
            # - Containers (dict/list/tuple) — CPython address reuse after GC
            #   can cause id() to match even when the value changed (#774),
            #   and a kept REFERENCE aliases an in-place mutation (#2664).
            #   Compared by structural fingerprint, never by reference.
            # Exclude request-scoped, framework-provided keys (#1786) from the
            # change-detection fingerprint — see the _request_scoped_keys
            # comment above. Keeping ``request`` / ``user`` / ``perms`` /
            # ``messages`` out of these dicts is what fixes the
            # ``_prev_context_refs has N keys — fingerprint truncated`` warning
            # and the per-event "changed" false positives for those values.
            self._prev_context_refs = {
                k: id(v) for k, v in full_context.items() if k not in _request_scoped_keys
            }
            # Fingerprints of the request-scoped plain data just synced (#3996),
            # tied to the refs dict above: see
            # ``_trusted_request_scoped_fingerprints``. Fingerprints only, and
            # only of values the Rust state holds a copy of.
            if rs_on:
                self._prev_request_scoped = (self._prev_context_refs, rs_fps)
            else:
                self.__dict__.pop("_prev_request_scoped", None)
            self._prev_context_immutables = {
                k: v
                for k, v in full_context.items()
                if k not in _request_scoped_keys and isinstance(v, _IMMUTABLE_TYPES_FOR_SYNC)
            }
            # A key already known to be sent (first render, a new key, a
            # changed name, ...) has not been fingerprinted yet: take the
            # fingerprint, the SafeString paths and the Rust value in one walk.
            self._prev_context_fingerprints = {
                k: _fp_of(k, v, convert=context.get(k, _MISSING) is v)
                for k, v in full_context.items()
                if k not in _request_scoped_keys and _is_structural(v)
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
            sidecar_only_keys: Set[str] = set()
            # Names whose value is a form, or holds one (#3767): sidecar only, never
            # state. Re-decided for every name sent this sync; a name that was not
            # sent is unchanged, so it keeps the answer it had (``_form_held_keys``,
            # stored below), which is what lets the sidecar below keep a list of
            # forms that did not change this event.
            form_held_keys: Set[str] = set()
            rendered_context: Dict[str, Any] = {}
            # Rust values already produced by ``state_pass``; every other key
            # goes through ``normalize_django_value`` below as before.
            converted_values: Dict[str, Any] = {}
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
                if (
                    rs_keeps_out[key]
                    if key in rs_keeps_out
                    else key in _request_scoped_keys and _keeps_out_of_state(value)
                ):
                    # A lazily rendered trusted-HTML value (theming's
                    # ``theme_head`` & co., #3028) is SafeData that is not a
                    # ``str``. It must NOT keep a stale state entry under the
                    # same name (the rolling-deploy case: state persisted by a
                    # build whose processor stored the rendered string; the
                    # state entry would win over the sidecar and was never
                    # re-marked safe), so it is left out of the retained keys
                    # below. That does not render it. The same holds for a
                    # processor's ``RequestScopedList`` (theming's
                    # ``theme_presets``, #3631): a snapshot stored by a build that
                    # copied the list into the state must not keep that copy.
                    if isinstance(value, SafeData) or is_request_scoped_data(value):
                        sidecar_only_keys.add(key)
                    continue
                if isinstance(value, _JSON_PRIMITIVES):
                    # Fast path: primitives are JSON-native and can't be SafeString
                    rendered_context[key] = value
                elif isinstance(value, str):
                    # Strings: check SafeString directly (no recursion needed)
                    from django.utils.safestring import SafeString

                    if isinstance(value, SafeString):
                        safe_keys.append(key)
                    rendered_context[key] = value
                else:
                    # Complex types (dict, list, etc.): full scan. One walk
                    # finds the SafeStrings and converts the value
                    # (``state_pass``); a shape it does not handle returns
                    # None and takes the original two passes.
                    converted = converted_by_fp.get(key)
                    if converted is None:
                        converted = convert_for_render(value, key)
                    if converted is None:
                        # A form, or a container holding one (#3767), is not state:
                        # it reaches the template from the raw-Python sidecar. The
                        # walk above gives up on exactly the values that can hold
                        # one, so a plain-data container never pays this scan.
                        if holds_form(value):
                            form_held_keys.add(key)
                            continue
                        safe_keys.extend(_collect_safe_keys(value, key))
                    else:
                        safe_keys.extend(converted[0])
                        converted_values[key] = converted[1]
                    rendered_context[key] = value
                    needs_normalize = True

            # The names that are a form (or hold one) now: the ones decided above and
            # the ones this sync did not send, which are unchanged (#3767).
            form_held_now = form_held_keys | {
                k
                for k in getattr(self, "_form_held_keys", ())
                if k in full_context and k not in context
            }
            self._form_held_keys = frozenset(form_held_now)

            # Skip normalize_django_value when context only has JSON-native types
            if needs_normalize:
                # ``normalize_django_value`` of a plain dict is its values
                # normalized one by one; the keys ``state_pass`` already
                # converted are taken as they are.
                json_compatible_context = {
                    k: (converted_values[k] if k in converted_values else normalize_django_value(v))
                    for k, v in rendered_context.items()
                }
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
                    # A `QueryDict` / `MultiValueDict` is a `dict` subclass,
                    # but its Value is the last-value-per-key rebuild
                    # (`normalize_django_value`); the raw object is what
                    # `{% querystring my_qd a=2 %}` needs, so it rides the
                    # sidecar the way `build_render_sidecar` already carries
                    # it on the non-LiveView paths (#2556, #1646).
                    #
                    # A ``RequestScopedList`` is a ``list`` but is NOT in the
                    # state (#3631), so the sidecar is the only way it reaches
                    # the renderer: it must not be skipped as "JSON friendly".
                    #
                    # A dict / list / tuple that holds a form (#3767) is the same
                    # case in the other direction: it is not in the state, so the
                    # sidecar is the only way it reaches the renderer.
                    if (
                        isinstance(_raw_val, _JSON_FRIENDLY)
                        and not isinstance(_raw_val, MultiValueDict)
                        and not (
                            _raw_key in _request_scoped_keys and is_request_scoped_data(_raw_val)
                        )
                        and _raw_key not in form_held_now
                    ):
                        continue
                    # A `Component`/`LiveComponent` used to be excluded here
                    # (#802, no recorded rationale). Un-excluded in #2501 so a
                    # component's OTHER names — a class attribute, a property,
                    # a nullary method — resolve here as they now do on the
                    # three non-LiveView paths.
                    #
                    # #2503 (fixed): the render loop above used to special-case
                    # Component/LiveComponent, eagerly replacing one with
                    # `{"render": <html>}` in the context and marking the key
                    # safe. `{{ c.render }}` hit that dict and worked; `{{ c }}`
                    # hit the SAME dict with no remaining path segments and
                    # rendered ITS Python repr — measured, not assumed, and the
                    # actual cause was the wrapper shape, not this sidecar
                    # inclusion. Removing the special-case (so Component/
                    # LiveComponent fall through to the generic "complex type"
                    # branch, matching every other object) fixed it: both
                    # spellings now converge on the SAME mechanism as the three
                    # non-LiveView paths — `normalize_django_value`'s Component
                    # arm answers the bare case directly; a MISS on `.render`
                    # (a plain string has no such attribute) falls through to
                    # THIS sidecar's raw object and the #2501 attribute walk
                    # answers it there, calling `.render()` a second time. That
                    # second call is the cost of one mechanism instead of two —
                    # bounded to render cycles where the component was already
                    # dirty (this loop iterates `context`, the change-detection
                    # OUTPUT, not `full_context`), and only when a template
                    # still uses the `.render` spelling the docs no longer
                    # recommend.
                    sidecar[_raw_key] = _raw_val
                # Authenticated partial-root mode renders the page shell with
                # the same Rust view. Its bridged Django tags need the actual
                # request just as the separate shell renderer does (#2589).
                # Keep it in the transient sidecar, never in persisted state.
                if (
                    request is not None
                    and getattr(
                        self._rust_view, "potential_authenticated_partial_root", lambda: False
                    )()
                ):
                    sidecar["request"] = request
                # ADR-024: models captured before the normalize loop replaced
                # them with dicts. The eager dict wins every direct hit; the
                # raw model serves only nested paths the dict lacks (reverse
                # relations / managers / non-get_ methods) via the sidecar
                # getattr walk. Explicit sidecar entries above take precedence.
                for _raw_key, _raw_val in _raw_models_for_sidecar.items():
                    sidecar.setdefault(_raw_key, _raw_val)
                # #3801: a relation the template reads as rows AND through a
                # QuerySet method (``{% for g in u.groups.all %}`` beside
                # ``{{ u.groups.all.count }}``) ships as rows, which cannot
                # answer the method; the engine's guarded getattr walk answers it
                # from these evaluated rows. Only such a name is kept (a proxy
                # per row is built for it); a plain loop pays nothing.
                for _raw_key, _raw_val in (getattr(self, "_all_method_rows", None) or {}).items():
                    if _raw_key in full_context:
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
                # the walk is a no-op for non-model values (request/view/etc.),
                # and the proxy protects transitively so ONE floor governs the
                # eager and sidecar channels (#1646).
                #
                # `_protect_sidecar_tree`, not `_protect_sidecar_value`: the
                # LEAF wrapper cannot see inside a container, so a model held
                # in a `MultiValueDict` reached a tag handler live here while
                # the plain-backend entry (`build_render_sidecar`, which uses
                # the tree) gave it a proxy — the same floor, two answers
                # (#2556 re-review, #1646). One shared `memo` across the loop,
                # for the reason `build_render_sidecar` shares one: two names
                # commonly point at the same object, and a per-key memo re-walks
                # it and hands out a different proxy for each.
                from ..serialization import _protect_sidecar_tree

                _sidecar_memo: Dict[Any, Any] = {}
                for _sk in list(sidecar.keys()):
                    sidecar[_sk] = _protect_sidecar_tree(sidecar[_sk], 0, _sidecar_memo, set())

            # Tell Rust which context keys changed for partial rendering.
            # Only call when there are actual changes — avoids overriding a
            # previous set_changed_keys call with meaningful keys.
            # Also exclude temporary_assigns keys — they're reset to new
            # objects after each render, always getting new id().
            _temp_assigns = _unassigned_temporary_keys(self)
            # #1786: request-scoped keys are excluded from the change-detection
            # fingerprint above, so on every render they look "new" and would
            # otherwise be reported as changed keys — triggering spurious
            # partial re-renders. They never change the user-visible output
            # (their values are re-applied fresh each cycle), so skip them.
            #
            # #3996: that holds for the values that ride the sidecar. Plain data
            # a processor supplies is fingerprinted now (``rs_fps``), so it is
            # in ``context`` only when it changed, and then it IS a changed
            # key: a template reading it renders the new value.
            _skip_keys = _FRAMEWORK_KEYS | _temp_assigns | (_request_scoped_keys - rs_fps.keys())
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

            # Values the view supplies lazily and keeps out of state (ThemeMixin's
            # rendered HTML, #3626) are request-scoped, so the detector above never
            # reports them. After a theme switch the view names them here, once, so
            # a template that reads them re-renders that node. (Measured: on a view
            # that has not rendered yet, naming changed keys still renders in full.)
            forced = self.__dict__.pop("_djust_forced_changed_keys", None)
            if forced:
                user_changed = sorted(
                    {*(user_changed or ()), *(k for k in forced if k in full_context)}
                )

            # Full-context truth (#2564): drop every Rust state key the context
            # no longer carries, BEFORE the merge. ``update_state`` only ever
            # extends, and a key that is simply absent is not a change the
            # detector above can see — so ``del self.secret`` (or a
            # ``if self.show: ctx["secret"] = …`` gate flipping closed) kept
            # rendering the last value. On EVERY sync, including partial ones,
            # because the removal is a delta ``context`` never contains; and
            # from ``full_context`` as it stands here (post-filtering), not
            # ``prev_refs`` — that fingerprint is emptied by
            # ``_force_full_html`` on every restore, so a tombstone diff would
            # be blind to a key that vanished across one while the restored
            # clone still carries it. ``hasattr`` guard mirrors
            # ``set_raw_py_values`` for a build that predates the method.
            #
            # One deliberate exemption: ``static_assigns``. Those are sent
            # once and then SKIPPED by ``get_context_data`` (context.py,
            # ``_static_assigns_sent``) precisely because Rust retains them —
            # the user declared them constant, so they are truth without
            # being in this render's context.
            removed_keys: List[str] = []
            if hasattr(self._rust_view, "retain_state_keys"):
                keep_keys = [
                    k for k in full_context if k not in sidecar_only_keys and k not in form_held_now
                ]
                if getattr(self, "_static_assigns_sent", False):
                    keep_keys.extend(getattr(self, "static_assigns", None) or [])
                removed_keys = self._rust_view.retain_state_keys(keep_keys)

            self._rust_view.update_state(json_compatible_context)
            if safe_keys:
                self._rust_view.mark_safe_keys(safe_keys)

            # The removed set must JOIN the changed keys (#2564, mechanism 2).
            # A removed key is not in ``context``, so the partial render would
            # skip its region and serve the OLD text from the node cache.
            if removed_keys:
                user_changed = [*(user_changed or []), *removed_keys]

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

            # #3936: the handle overlay, set on EVERY sync (empty with the flag
            # off, or when no state key holds a model) so a key that left state
            # leaves no stale handle.
            # An empty overlay is not sent to a view that has none (review N3): the
            # flag-off sync then costs one ``has_render_handles`` read, and a view
            # that held handles is still cleared on the sync they leave.
            if hasattr(self._rust_view, "set_render_handles"):
                # B-H1/B-H2 (train/2.0-ai): a key the flag-off path already answers
                # (QuerySet methods, JIT-walked rows) keeps that path, so the overlay
                # issues no query the flag-off render does not.
                _skip = set(sidecar_only_keys)
                if _overlay_raw:
                    _skip.update(
                        _overlay_skip_keys(
                            self,
                            _overlay_raw,
                            full_context,
                            _explicit_jit_keys,
                            _qs_roots,
                            _explicit_plain_keys,
                        )
                    )
                _handles = _render_handles(_overlay_raw, full_context, _skip)
                if _handles or getattr(self._rust_view, "has_render_handles", lambda: True)():
                    self._rust_view.set_render_handles(_handles)

            if user_changed is not None:
                self._rust_view.set_changed_keys(user_changed)
            # #4123: nothing to render when this was not a first (or forced)
            # sync, no key changed, was removed or was named, and every value
            # was compared by content (none only by ``id()``).
            render_needed = not prev_refs or user_changed is not None or opaque_seen

            # The ``dj-update="append"|"prepend"`` containers whose rows come from
            # a ``temporary_assigns`` key must ACCUMULATE in the differ, as the
            # client does for a full html frame; otherwise the rows a render lists
            # (only the ones just assigned) replace the stored ones (#3639). Set
            # on every sync: a restored Rust view starts with none, and
            # ``hasattr`` covers an extension built before the method existed.
            if hasattr(self._rust_view, "set_accumulate_containers"):
                _warn_rows_without_id(self)
                self._rust_view.set_accumulate_containers(
                    list(view_accumulating_container_ids(self, _context_aliases))
                )

            # Mark static assigns as sent — subsequent syncs will skip them
            if getattr(self, "static_assigns", None) and not getattr(
                self, "_static_assigns_sent", False
            ):
                self._static_assigns_sent = True
        return render_needed
