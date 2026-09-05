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

Cost is bounded by ``budget`` visited nodes (default 20 000). Past it the
remaining subtrees collapse to ``id()`` and the caller is told, so it can emit
the one-shot "fingerprint truncated" warning naming the attribute; the walk
is also depth-capped and cycle-safe. Measured on the model-backed WS
benchmark (50 rows × 6 columns, ``tests/benchmarks/``) the deep walk is
within noise of the shallow one because the rows are model instances — leaves.
"""

from __future__ import annotations

from typing import Any, Hashable, List, Tuple

__all__ = ["deep_fingerprint", "warn_fingerprint_truncated", "DEFAULT_BUDGET"]

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
        "budget (%d nodes) — content fingerprint truncated. In-place mutations "
        "inside it will NOT be detected by auto-diff. Use "
        "self.set_changed_keys({'%s'}) or assign a new %s reference.",
        cls.__qualname__,
        type(value).__name__,
        name,
        len(value) if hasattr(value, "__len__") else 0,
        DEFAULT_BUDGET,
        name,
        type(value).__name__,
    )


def deep_fingerprint(value: Any, budget: int = DEFAULT_BUDGET) -> Tuple[Hashable, bool]:
    """Return ``(fingerprint, truncated)`` for *value*.

    ``fingerprint`` is hashable and shares no reference with *value*;
    ``truncated`` is True when *budget* ran out and part of the structure was
    reduced to ``id()`` (in-place mutations inside that part are invisible).
    """
    counter: List[int] = [budget]
    fp = _walk(value, counter, 0, ())
    return fp, counter[0] < 0


def _walk(value: Any, counter: List[int], depth: int, path_ids: Tuple[int, ...]) -> Hashable:
    counter[0] -= 1
    if counter[0] < 0:
        return (_TAG_TRUNCATED, id(value))
    if value is None or isinstance(value, _IMMUTABLE_LEAVES):
        return (_TAG_VALUE, type(value), value)
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        vid = id(value)
        if depth >= _MAX_DEPTH or vid in path_ids:
            # Too deep, or a cycle back onto an ancestor: identity only.
            return (_TAG_ID, vid)
        inner = path_ids + (vid,)
        if isinstance(value, dict):
            return (
                _TAG_DICT,
                tuple(
                    (_walk(k, counter, depth + 1, inner), _walk(v, counter, depth + 1, inner))
                    for k, v in value.items()
                ),
            )
        if isinstance(value, (set, frozenset)):
            try:
                return (_TAG_SET, frozenset(_walk(v, counter, depth + 1, inner) for v in value))
            except TypeError:  # pragma: no cover — every branch returns hashables
                return (_TAG_ID, vid)
        tag = _TAG_LIST if isinstance(value, list) else _TAG_TUPLE
        return (tag, tuple(_walk(v, counter, depth + 1, inner) for v in value))
    return (_TAG_ID, id(value))
