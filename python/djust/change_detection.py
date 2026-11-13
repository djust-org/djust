"""One structural fingerprint for every "did this value change?" decision (#2664).

Four places decide whether a piece of LiveView state changed between two
points in time: the pre/post event snapshot (``websocket._snapshot_assigns``),
the Rust state sync (``rust_bridge._sync_state_to_rust``), dirty tracking
(``LiveView._dirty_fingerprint``) and the memoised ``@computed`` cache. Until
#2664 each carried its own shallow fingerprint (``id`` + length + top-level
keys) and the Rust sync kept a REFERENCE to the previous container and
compared it by ``==`` — which is the mutation-after-capture aliasing of #1039:
after ``self.columns["done"].append(card)`` the "previous" value IS the
mutated object, so it compares equal to itself and the browser never hears
about the change. The two seams disagreed with each other too (one skipped
the event, the other rendered and sent ``patches: []``), which is the #1646
parallel-path shape.

:func:`deep_fingerprint` walks plain containers (``dict`` / ``list`` /
``tuple`` / ``set`` / ``frozenset``) structurally down to their leaves and
returns a hashable, comparable value that shares NO reference with the state,
so a later in-place mutation cannot retroactively change it. Leaves:

* immutables (``str`` / ``int`` / ``float`` / ``bool`` / ``bytes`` / ``None``)
  by value, tagged with their type so ``1`` / ``True`` / ``1.0`` differ;
* everything else by ``id()`` — a model instance, a form, a queryset. A
  reassignment is seen, an in-place attribute write on such an object is not
  (the Phoenix LiveView contract; ``set_changed_keys`` remains the hatch).

Cost is bounded by ``budget`` visited nodes (default 20 000). On exhaustion
the fingerprint collapses to the root identity and callers must conservatively
invalidate it; the walk
is also depth-capped and cycle-safe. Measured on the model-backed WS
benchmark (50 rows × 6 columns, ``tests/benchmarks/``) the deep walk is
within noise of the shallow one because the rows are model instances — leaves.
"""

from __future__ import annotations

from typing import Any, Hashable, List, Tuple

from . import _per_turn

__all__ = [
    "deep_fingerprint",
    "share_structure",
    "warn_fingerprint_truncated",
    "DEFAULT_BUDGET",
    "FINGERPRINT_FIELDS",
    "CHANGE_DETECTION_IGNORED",
]

#: Maximum container nodes + leaves visited per top-level value before the
#: walk degrades to identity for whatever remains.
DEFAULT_BUDGET = 20_000

_MAX_DEPTH = 32

_IMMUTABLE_LEAVES = (str, int, float, bool, bytes, type(None))

# Tags keep the shape unambiguous: ("L", ...) for a list can never equal
# ("T", ...) for the same items as a tuple, and a leaf ("v", int, 1) never
# equals ("v", bool, True).
_TAG_VALUE = "v"
_TAG_ID = "i"
_TAG_LIST = "L"
_TAG_TUPLE = "T"
_TAG_DICT = "D"
_TAG_SET = "S"
_TAG_TRUNCATED = "x"

#: The container tags whose second element is a tuple of children.
_SHARED_TAGS = frozenset({_TAG_LIST, _TAG_TUPLE, _TAG_DICT})


def warn_fingerprint_truncated(cls: type, name: str, value: Any) -> None:
    """Warn ONCE per (view class, attribute) that *name* exceeded the budget.

    Both change-detection sites that can hit the budget — the pre/post event
    snapshot (``websocket._snapshot_assigns``) and the Rust state sync
    (``rust_bridge._sync_state_to_rust``) — call this with the same key, so
    an attribute that trips both is reported once, and a SECOND oversized
    attribute on the same view is still reported (per-attribute sentinel,
    not per-class).
    """
    from .utils import emit_one_shot_class_warning

    emit_one_shot_class_warning(
        cls,
        "fingerprint_truncated_%s" % name,
        "[djust] %s: %s '%s' has %d items and exceeds the change-detection "
        "budget (%d nodes) — content fingerprint truncated; change detection will "
        "conservatively treat it as changed. Narrow the state or use "
        "self.set_changed_keys({'%s'}) or assign a new %s reference.",
        cls.__qualname__,
        type(value).__name__,
        name,
        len(value) if hasattr(value, "__len__") else 0,
        DEFAULT_BUDGET,
        name,
        type(value).__name__,
    )


#: The plain containers every snapshot walks structurally (#2664).
CONTAINER_TYPES = (dict, list, tuple, set, frozenset)

#: Marker a wrapper class sets so the snapshots fingerprint the object it
#: wraps rather than the wrapper's ``id()``. ``BoundComponent`` (ADR-031) sets
#: it: a class-level component's slot holds the wrapper, whose identity never
#: changes, while the ``State`` inside it is what a handler mutates (#2900).
STATE_MARKER = "_djust_fingerprint_state"


#: Name of the optional class attribute that narrows the walk of a
#: state-marked object (ADR-033 D3). A tuple of state keys: those are walked
#: structurally; every OTHER key is a leaf — compared by value when it is a
#: scalar, by ``id()`` otherwise — so a data table's ``rows`` cost one node
#: instead of a ten-thousand-row walk, and reassigning them is still seen.
#: ``None`` (the default) walks the whole state under the budget.
FINGERPRINT_FIELDS = "fingerprint_fields"


def _leaf(value: Any) -> Hashable:
    """The one-node fingerprint of *value*: scalars by value, else identity."""
    if value is None or isinstance(value, _IMMUTABLE_LEAVES):
        return (_TAG_VALUE, type(value), value)
    return (_TAG_ID, id(value))


def fingerprints_by_content(value: Any) -> bool:
    """Is *value* compared by STRUCTURE (walked) rather than by ``id()``?

    ONE statement of the rule for every snapshot — ``_snapshot_assigns``, the
    dirty baseline, ``@computed``'s dependency key and ``_sync_state_to_rust``
    — so a shape one of them walks cannot be a leaf to another (#1646, #2900).
    """
    return isinstance(value, CONTAINER_TYPES) or bool(getattr(type(value), STATE_MARKER, False))


def deep_fingerprint(value: Any, budget: int = DEFAULT_BUDGET) -> Tuple[Hashable, bool]:
    """Return ``(fingerprint, truncated)`` for *value*.

    ``fingerprint`` is hashable and shares no reference with *value*;
    ``truncated`` is True when *budget* ran out and part of the structure was
    reduced to root identity. Callers must conservatively invalidate it.
    """
    t = type(value)
    if t in _EXACT_SCALARS:
        # A scalar ROOT keeps its tagged form in both encodings: a caller may
        # store the fingerprint beside a "nothing stored" default, and a bare
        # ``None`` or ``0`` must never be mistaken for one.
        if budget < 1:
            return (_TAG_TRUNCATED, id(value)), True
        return (_TAG_VALUE, t, value), False
    counter: List[Any] = [budget, _BARE if _per_turn.on("compact_fingerprints") else _NO_BARE]
    fp = _walk(value, counter, 0, ())
    return fp, counter[0] < 0


_SHARE_DEPTH = 3


def share_structure(fp: Any, held: Any, depth: int = _SHARE_DEPTH) -> Any:
    """*fp*, built from *held*'s own subtrees wherever the two are equal (#3998).

    The result is EQUAL to *fp*; only which objects it is made of differs. A
    list of a thousand rows of which one changed since *held* was taken is
    then one new row and a tuple of references, not a second tree. Children
    are matched by position, down to ``depth`` levels, so a row inserted at
    the head shares nothing (it costs what it cost before, never more).

    Fingerprints are immutable and hold no reference into the state, so one
    subtree can stand in two fingerprints without either seeing the other
    change.
    """
    if fp is held:
        return fp
    if (
        type(fp) is not tuple
        or type(held) is not tuple
        or len(fp) != 2
        or len(held) != 2
        or fp[0] != held[0]
        or fp[0] not in _SHARED_TAGS
        or type(fp[1]) is not tuple
        or type(held[1]) is not tuple
    ):
        return fp
    old = held[1]
    limit = len(old)
    out: List[Any] = []
    append = out.append
    reused = False
    for index, child in enumerate(fp[1]):
        if index < limit and type(child) is tuple:
            other = old[index]
            if other is child:
                pass
            elif other == child:
                child = other
                reused = True
            elif depth > 1:
                shared = share_structure(child, other, depth - 1)
                if shared is not child:
                    child = shared
                    reused = True
        append(child)
    return (fp[0], tuple(out)) if reused else fp


#: Exact builtin scalar types: no subclass, so no ``STATE_MARKER`` either. The
#: fast path in :func:`_walk` handles them inline; a subclass of one (Django's
#: ``SafeString``, an ``IntEnum``) takes the general path, which fingerprints
#: it the same way.
_EXACT_SCALARS = frozenset(_IMMUTABLE_LEAVES)

#: The compact encoding (#3998). A fingerprint tree is retained per view for as
#: long as its socket is open (the previous render's, and the dirty baseline),
#: so its size is per-socket memory. In the wide encoding every leaf was a
#: 3-tuple ``("v", type, value)`` and every dict item a 2-tuple of two such
#: leaves: 184 bytes of tuples for one ``"name": "x"``. In the compact encoding
#:
#: * a leaf INSIDE a container whose exact type is ``str``, ``int``, ``bytes``
#:   or ``NoneType`` is the value itself. Values of those four types are never
#:   equal across types and never equal to a tuple, so nothing that differed
#:   before compares equal now. ``bool`` and ``float`` keep the tagged tuple:
#:   ``True == 1 == 1.0``, and the tag is what tells them apart;
#: * a dict's items are one flat tuple ``(k1, v1, k2, v2, ...)``.
#:
#: Two values have equal compact fingerprints exactly when their wide ones are
#: equal (``test_fingerprint_encoding.py`` runs both over the same values).
#: The wide encoding stays reachable through ``_per_turn`` for that comparison
#: and for the frame oracle; nothing else selects it.
_BARE = frozenset({str, int, bytes, type(None)})
_NO_BARE: frozenset = frozenset()


def _walk(value: Any, counter: List[Any], depth: int, path_ids: Tuple[int, ...]) -> Hashable:
    """Fingerprint *value*; same result as :func:`_walk_general`, faster.

    Every snapshot walks the whole view state several times per event (the
    pre/post assigns snapshot, the Rust sync, dirty tracking, the context
    fingerprints), so this walk is most of an event's CPU when the state
    holds a few hundred plain rows, and it is serialised on the process's
    one sync thread. Exact ``dict`` / ``list`` / ``tuple`` nodes and
    exact scalar leaves are handled here without a call per leaf; anything
    else goes to :func:`_walk_general`. The output is identical, node for
    node and budget unit for budget unit, in the same order.

    ``state_pass._fused`` repeats this walk while converting the value for
    the Rust sync; a change here must be made there too
    (``test_state_single_pass.py`` compares the two).
    """
    t = type(value)
    bare = counter[1]
    if t in _EXACT_SCALARS:
        # Reached for a scalar inside a set or a component's state (the
        # general walk calls back here per child); a scalar root never gets
        # this far (``deep_fingerprint``).
        counter[0] -= 1
        if counter[0] < 0:
            return (_TAG_TRUNCATED, id(value))
        return value if t in bare else (_TAG_VALUE, t, value)
    if t is not dict and t is not list and t is not tuple:
        return _walk_general(value, counter, depth, path_ids)
    counter[0] -= 1
    if counter[0] < 0:
        return (_TAG_TRUNCATED, id(value))
    vid = id(value)
    if depth >= _MAX_DEPTH or vid in path_ids:
        return (_TAG_ID, vid)
    inner = path_ids + (vid,)
    child_depth = depth + 1
    out: List[Hashable] = []
    append = out.append
    if t is dict:
        for k, v in value.items():
            tk = type(k)
            if tk in _EXACT_SCALARS:
                counter[0] -= 1
                kf: Hashable = k if tk in bare else (_TAG_VALUE, tk, k)
            else:
                kf = _walk(k, counter, child_depth, inner)
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
            tv = type(v)
            if tv in _EXACT_SCALARS:
                counter[0] -= 1
                vf: Hashable = v if tv in bare else (_TAG_VALUE, tv, v)
            else:
                vf = _walk(v, counter, child_depth, inner)
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
            if bare:
                append(kf)
                append(vf)
            else:
                append((kf, vf))
        return (_TAG_DICT, tuple(out))
    for v in value:
        tv = type(v)
        if tv in _EXACT_SCALARS:
            counter[0] -= 1
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
            append(v if tv in bare else (_TAG_VALUE, tv, v))
        else:
            append(_walk(v, counter, child_depth, inner))
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
    return (_TAG_LIST if t is list else _TAG_TUPLE, tuple(out))


def _walk_general(
    value: Any, counter: List[Any], depth: int, path_ids: Tuple[int, ...]
) -> Hashable:
    fields: Any = None
    if getattr(type(value), STATE_MARKER, False):
        fields = getattr(type(value), FINGERPRINT_FIELDS, None)
        if isinstance(fields, str):
            fields = (fields,)
        value = getattr(value, "state", value)
    counter[0] -= 1
    if counter[0] < 0:
        return (_TAG_TRUNCATED, id(value))
    if value is None or isinstance(value, _IMMUTABLE_LEAVES):
        return (_TAG_VALUE, type(value), value)
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        vid = id(value)
        if depth >= _MAX_DEPTH or vid in path_ids:
            return (_TAG_ID, vid)
        inner = path_ids + (vid,)
        out: List[Hashable] = []
        if isinstance(value, dict):
            for k, v in dict.items(value):
                if counter[0] <= 0:
                    counter[0] = -1
                    return (_TAG_TRUNCATED, vid)
                kf = _walk(k, counter, depth + 1, inner)
                if counter[0] < 0:
                    return (_TAG_TRUNCATED, vid)
                if fields is not None and k not in fields:
                    vf = _leaf(v)
                else:
                    if counter[0] <= 0:
                        counter[0] = -1
                        return (_TAG_TRUNCATED, vid)
                    vf = _walk(v, counter, depth + 1, inner)
                if counter[0] < 0:
                    return (_TAG_TRUNCATED, vid)
                if counter[1]:
                    out.append(kf)
                    out.append(vf)
                else:
                    out.append((kf, vf))
            return (_TAG_DICT, tuple(out))
        if isinstance(value, (set, frozenset)):
            try:
                for v in value:
                    if counter[0] <= 0:
                        counter[0] = -1
                        return (_TAG_TRUNCATED, vid)
                    out.append(_walk(v, counter, depth + 1, inner))
                    if counter[0] < 0:
                        return (_TAG_TRUNCATED, vid)
                return (_TAG_SET, frozenset(out))
            except TypeError:
                # Preserve identity fallback for unhashable child fingerprints.
                return (_TAG_ID, vid)
        for v in value:
            if counter[0] <= 0:
                counter[0] = -1
                return (_TAG_TRUNCATED, vid)
            out.append(_walk(v, counter, depth + 1, inner))
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
        return (_TAG_LIST if isinstance(value, list) else _TAG_TUPLE, tuple(out))
    return (_TAG_ID, id(value))


#: Names on a view's instance dict that the pre/post event snapshot
#: (``websocket._snapshot_assigns``) never fingerprints: framework
#: configuration, per-connection identity and render-cycle bookkeeping, written
#: after ``__init__`` so the init-time ``_framework_attrs`` does not hold them.
#: Fingerprinting them would make a turn render because of framework
#: bookkeeping (``_prev_context_fingerprints`` is the previous render's
#: fingerprint, the largest value on the instance, #2664). It runs for explicit
#: and legacy views alike. Until 2.0 this was ``live_view._FRAMEWORK_INTERNAL_ATTRS``
#: (#762), which also served as the reserved ``state()`` name list; that list is
#: now ``_exposure.reserved_state_names()`` (exposure-flip plan row 10, G1).
#:
#: Framework-internal attributes that MUST NOT be surfaced as reactive user state
#: in get_state(), _snapshot_assigns(), or observability debug payloads (#762).
#:
#: Three buckets of leakage:
#:   1. Django ``View``-inherited (set by ``as_view()``):
#:      http_method_names, args, kwargs, response_class, content_type
#:   2. djust ``LiveView``/mixin-set config attrs that happen to live on
#:      ``self.__dict__`` rather than on the class — without this filter they
#:      get mistaken for reactive state.
#:   3. Static config surfaced by mixins (PageMetadataMixin, LayoutMixin, etc.).
#:
#: This is the non-breaking fix: attribute names are unchanged. Renaming these
#: to ``_*`` would break every downstream template / integration that reads
#: e.g. ``template_name`` from the context.
CHANGE_DETECTION_IGNORED: frozenset = frozenset(
    {
        # Django View-inherited (set by as_view())
        "http_method_names",
        "args",
        "kwargs",
        "response_class",
        "content_type",
        # Request handle — reassigned per HTTP/WS event (#1545)
        "request",
        # djust LiveView base config
        "sync_safe",
        "exposure_policy",
        "_explicit_child_mount_inputs",
        "_explicit_child_mount_binding",
        "_explicit_child_schema",
        "_explicit_child_reuse_identity",
        "_explicit_child_render_regions",
        "_explicit_child_vdom_bindings",
        "_explicit_child_vdom_tracking",
        "_explicit_child_render_scope",
        "_explicit_child_rendered_full",
        "_explicit_child_state_tracked",
        # ADR-038 E2-1: the keys registered providers rendered this pass.
        "_explicit_context_provider_keys",
        "_djust_child_disposed",
        "_djust_waiters_closed",
        "_async_task_handles",
        "_tracked_tasks",
        "_async_tasks",
        "_async_task_counter",
        "_async_cancelled",
        "_async_work_generation",
        "view_is_async",
        "tick_interval",
        "room_clock",
        "_room_clock_scope",
        "login_required",
        "login_url",
        "permission_required",
        "allowed_model_fields",
        "on_mount",
        "on_mount_count",
        "static_assigns",
        "static_assigns_count",
        "static_render",
        "query_budget",
        # View options of the page-status / declared-URL-params work (#3688,
        # #3689). Class attributes like ``query_budget``; an instance that
        # assigns one (``self.state_attribute = ...``) must not publish it as
        # reactive state (#3706).
        "http_status_of_state",
        "state_attribute",
        "params_form",
        "params_form_fallback",
        "template",
        "template_name",
        "base_template",
        "page_meta",
        "page_slug",
        "page_title",
        "temporary_assigns",
        "sticky",
        "sticky_id",
        # Per-event render-control flags (#1981 / PR #1982 review). Written by
        # ``set_changed_keys()`` / handlers and read by the event spine; they are
        # framework signals, not user state. ``_snapshot_assigns`` has no
        # underscore filter, so without this exclusion assigning them in a
        # handler perturbs the pre/post fingerprint and triggers a render BY
        # SNAPSHOT LEAK rather than by the sanctioned ``_force_full_html``
        # mechanism (and made the "setting _changed_keys directly is
        # ineffective" doc claim false).
        "_changed_keys",
        # The handler's no-render request (#3246): consumed to False by
        # ``_resolve_skip_render``, so a first ``_skip_render`` left a key the
        # pre-handler snapshot lacked and read as a state change.
        "_skip_render",
        # Lazily-assigned framework bookkeeping (#2664). These are written
        # AFTER ``__init__`` (first render / first event / first dirty
        # baseline), so they are NOT in the ``_framework_attrs`` snapshot
        # taken at init and ``_snapshot_assigns`` would otherwise fingerprint
        # them as user state — including ``_prev_context_fingerprints``,
        # which is the structural fingerprint of the PREVIOUS render and
        # therefore the single largest value on the instance (measured:
        # 12.8k nodes vs 153 for the 50-row ``rows`` list on the
        # model-backed benchmark). The test for membership is "is it ever
        # read by a template?", NOT "is it set after __init__": ``_action_state``
        # (splatted into the context by ``mixins/context.py`` — an ``@action``
        # that only records an error must re-render) is deliberately NOT here.
        # ``_dirty_baseline`` IS here even though the template-readable
        # ``is_dirty`` / ``changed_fields`` derive from it: it is the
        # fingerprint of EVERY public attr, so walking it re-walks the whole
        # state per event (measured 12.2k nodes vs 3.0k for the data, 7.6x
        # slower, and it trips the budget with a warning naming it). The one
        # thing that changes it without touching public state —
        # ``mark_clean()`` — is made visible by ``_dirty_baseline_version``, a
        # small counter ``_capture_dirty_baseline`` bumps that IS snapshotted.
        "_prev_context_refs",
        "_prev_context_immutables",
        "_prev_context_fingerprints",
        # The fingerprints of the context processors' plain-data values the
        # last sync sent (#3996), kept beside ``_prev_context_refs``.
        "_prev_request_scoped",
        # Context processor outputs handed from the scoped component check to
        # the page render of the same event (#3996); gone when it ends.
        "_djust_processor_record",
        "_djust_processor_replay",
        "_djust_computed_cache",
        "_djust_computed_deps",
        "_djust_computed_lock",
        # The context names that are a form or hold one (#3767): the sidecar answers
        # for them, and a name not sent this sync keeps this answer.
        "_form_held_keys",
        "_dirty_baseline",
        "_rust_render_timing",
        # Adaptive loop-cache bypass bookkeeping (#3071), written after a render.
        "_loop_cache_miss_streak",
        "_loop_cache_bypassed",
        "_djust_mount_kwargs",
        "_jit_serialized_keys",
        # The JIT's template source for a value reached through a container,
        # read once per render by ``get_context_data`` (#3788).
        "_nested_template_source",
        # The raw rows a QuerySet-method read is answered from (#3801).
        "_all_method_rows",
        "_context_processor_keys",
        # What the last legacy render built from attributes / as render-only
        # (``mixins/context.py`` ``legacy_unsaved_keys``).
        "_djust_state_context_keys",
        "_djust_render_only_keys",
        "_cached_csrf_token",
        "_djust_id_counter",  # Render-cycle ID allocation is not reactive state.
        "_sync_done_this_cycle",
        "_force_full_html",
        # Names of start_async tasks currently running (#2969). Written by the
        # dispatcher and a task's completion callback, possibly while an
        # unrelated handler's turn is in flight; never read by a template.
        "_async_running",
        # {% live_render sticky=True %} bookkeeping (#2919): the kwargs a
        # sticky child was mounted with, and the last set a warning named.
        "_djust_sticky_mount_kwargs",
        "_djust_sticky_kwargs_warned",
        # Per-connection identity the transports write before ``mount()``
        # (``runtime.py`` ``dispatch_mount`` / ``on_view_mounted``). Not in the
        # init-time ``_framework_attrs``, so the legacy private-state save
        # carried them, and a reconnect's session restore then overwrote the
        # new connection's values with the previous connection's (#3248).
        "_websocket_session_id",
        "_websocket_path",
        "_websocket_query_string",
        "_websocket_host",
        "_websocket_secure",
        "_django_session_key",
        "_djust_mount_view_path",
        # The ``target_id`` of the slot a view mounted beside the page view
        # (``on_view_instantiated``, #3252); keys its cached Rust view.
        "_djust_slot_target",
        # The HTTP fallback's per-render child id counter (``_assign_view_id``).
        "_render_child_ids",
        # The SSE transport's stream id (``on_view_mounted``), the twin of
        # ``_websocket_session_id`` (#3248 review).
        "_sse_session_id",
    }
)
