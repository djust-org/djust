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

__all__ = ["deep_fingerprint", "warn_fingerprint_truncated", "DEFAULT_BUDGET", "FINGERPRINT_FIELDS"]

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
    counter: List[int] = [budget]
    fp = _walk(value, counter, 0, ())
    return fp, counter[0] < 0


#: Exact builtin scalar types: no subclass, so no ``STATE_MARKER`` either. The
#: fast path in :func:`_walk` handles them inline; a subclass of one (Django's
#: ``SafeString``, an ``IntEnum``) takes the general path, which fingerprints
#: it the same way.
_EXACT_SCALARS = frozenset(_IMMUTABLE_LEAVES)


def _walk(value: Any, counter: List[int], depth: int, path_ids: Tuple[int, ...]) -> Hashable:
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
    if t in _EXACT_SCALARS:
        counter[0] -= 1
        if counter[0] < 0:
            return (_TAG_TRUNCATED, id(value))
        return (_TAG_VALUE, t, value)
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
                kf: Hashable = (_TAG_VALUE, tk, k)
            else:
                kf = _walk(k, counter, child_depth, inner)
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
            tv = type(v)
            if tv in _EXACT_SCALARS:
                counter[0] -= 1
                vf: Hashable = (_TAG_VALUE, tv, v)
            else:
                vf = _walk(v, counter, child_depth, inner)
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
            append((kf, vf))
        return (_TAG_DICT, tuple(out))
    for v in value:
        tv = type(v)
        if tv in _EXACT_SCALARS:
            counter[0] -= 1
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
            append((_TAG_VALUE, tv, v))
        else:
            append(_walk(v, counter, child_depth, inner))
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
    return (_TAG_LIST if t is list else _TAG_TUPLE, tuple(out))


def _walk_general(
    value: Any, counter: List[int], depth: int, path_ids: Tuple[int, ...]
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
