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

What a view KEEPS between turns is not the fingerprint but its digest
(:func:`retained_fingerprint`, #3998): a 16-byte BLAKE2b hash of the
fingerprint's canonical encoding. The fingerprint tree is built, compared and dropped
within a turn; the dirty baseline and the last render's fingerprints hold only
digests, so what a socket keeps for change detection no longer grows with
the state. Two fingerprints have equal digests exactly when they are equal
(see :func:`fingerprint_digest` for the two deliberate differences, both of
which only ever detect MORE).

For PLAIN DATA (exact ``dict`` / ``list`` / ``tuple`` nodes and exact scalar
leaves, within the depth cap and the budget) the digest is computed natively,
in one Rust walk that builds no tree (:func:`native_digest`, #4325). The rule,
stated once: **a container's retained fingerprint is its native digest when
``djust._rust.plain_digest`` accepts the value, and the BLAKE2b digest of its
tree otherwise.** That is a function of the value alone, so a pre/post, sync or
cross-turn comparison never compares a native digest with a tree digest of the
same value. The two kinds are domain-separated (the native hash starts with
its own tag byte, under a per-process key), so a comparison across kinds, were
one ever made, could only read as "changed": an extra sync, never a missed one.
Every producer of a retained fingerprint for a value goes through
:func:`content_digest` (or takes the native digest a snapshot already made of
the very same object); :func:`retained_fingerprint` alone is only for a tree
of a value :func:`native_digest` refused.
"""

from __future__ import annotations

import hashlib
import marshal
import weakref
from math import copysign as _copysign
from typing import Any, Dict, Hashable, List, Optional, Tuple

from . import _per_turn
from .turns import current_turn

__all__ = [
    "AssignsSnapshot",
    "ChangedKeys",
    "changed_keys_after",
    "deep_fingerprint",
    "fingerprint_digest",
    "retained_fingerprint",
    "deep_digest",
    "content_digest",
    "native_digest",
    "DIGEST_SIZE",
    "warn_fingerprint_truncated",
    "DEFAULT_BUDGET",
    "FINGERPRINT_FIELDS",
    "CHANGE_DETECTION_IGNORED",
]

#: Maximum container nodes + leaves visited per top-level value before the
#: walk degrades to identity for whatever remains.
DEFAULT_BUDGET = 20_000

#: Mirrored by ``MAX_DEPTH`` in ``crates/djust_live/src/fingerprint.rs`` (#4325).
_MAX_DEPTH = 32

_IMMUTABLE_LEAVES = (str, int, float, bool, bytes, type(None))

# Tags keep the shape unambiguous: ("L", ...) for a list can never equal
# ("T", ...) for the same items as a tuple, and a leaf ("v", "n", 1) never
# equals ("v", "b", True) (``_TYPE_CODE``).
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
        return _tagged_scalar(value)
    return (_TAG_ID, id(value))


def _tagged_scalar(value: Any) -> Hashable:
    """``("v", type, value)``, with a subclass's value as its builtin base (#3998).

    A ``str`` / ``int`` / ``float`` / ``bytes`` subclass (Django's
    ``SafeString``, an ``IntEnum``) keeps its own type in the tag, so it never
    equals the plain value, but the value beside it is the exact builtin copy.
    A subclass may define ``__eq__`` (or ``__hash__ = None``) any way it
    likes; with the base copy, comparing two fingerprints runs no application
    code, and the fingerprint is plain data :func:`fingerprint_digest` can
    encode. The type is spelled ``"module.qualname"`` for the same reason.
    """
    t = type(value)
    if t in _EXACT_SCALARS:
        return (
            _TAG_VALUE,
            _TYPE_CODE[t] if value or t is not float or _copysign(1.0, value) > 0 else _NEG_ZERO,
            value,
        )
    base: Any
    if issubclass(t, str):
        base = str.__str__(value)
    elif issubclass(t, bytes):
        base = bytes.__getitem__(value, slice(None))
    elif issubclass(t, float):
        base = float.__float__(value)
        if not base and _copysign(1.0, base) < 0:
            # Distinct from the same subclass's 0.0 (see ``_NEG_ZERO``).
            return (_TAG_VALUE, "%s.%s-" % (t.__module__, t.__qualname__), base)
    elif issubclass(t, int):
        base = int.__int__(value)
    else:
        # A proxy that passes ``isinstance`` through ``__class__`` (Django's
        # ``SimpleLazyObject`` over a string): the builtin constructor returns
        # the exact type, through the proxy, as a template would read it.
        for builtin in (str, bytes, float, int):
            if isinstance(value, builtin):
                base = builtin(value)
                break
        else:  # pragma: no cover - only scalars reach here
            return (_TAG_ID, id(value))
    return (_TAG_VALUE, "%s.%s" % (t.__module__, t.__qualname__), base)


def fingerprints_by_content(value: Any) -> bool:
    """Is *value* compared by STRUCTURE (walked) rather than by ``id()``?

    ONE statement of the rule for every snapshot — ``_snapshot_assigns``, the
    dirty baseline, ``@computed``'s dependency key and ``_sync_state_to_rust``
    — so a shape one of them walks cannot be a leaf to another (#1646, #2900).
    """
    return isinstance(value, CONTAINER_TYPES) or bool(getattr(type(value), STATE_MARKER, False))


def deep_fingerprint(
    value: Any, budget: int = DEFAULT_BUDGET, *, plain: Optional[List[bool]] = None
) -> Tuple[Hashable, bool]:
    """Return ``(fingerprint, truncated)`` for *value*.

    ``fingerprint`` is hashable and shares no reference with *value*;
    ``truncated`` is True when *budget* ran out and part of the structure was
    reduced to root identity. Callers must conservatively invalidate it.

    With *plain* (a list), the walk also appends whether *value* is PLAIN
    DATA: only exact ``dict`` / ``list`` / ``tuple`` nodes and exact builtin
    scalar leaves, within the depth cap and without a cycle (#3999). Anything
    the fast walk hands to :func:`_walk_general` (an object, a set, a
    subclass, a component's state) makes it ``False``. Plain data cannot hold
    a component, a form or a model, which is what lets the Rust sync skip
    ``_collect_sub_ids`` over it.
    """
    t = type(value)
    if t in _EXACT_SCALARS:
        # A scalar ROOT keeps its tagged form in both encodings: a caller may
        # store the fingerprint beside a "nothing stored" default, and a bare
        # ``None`` or ``0`` must never be mistaken for one.
        if plain is not None:
            plain.append(True)
        if budget < 1:
            return (_TAG_TRUNCATED, id(value)), True
        return (
            _TAG_VALUE,
            _TYPE_CODE[t] if value or t is not float or _copysign(1.0, value) > 0 else _NEG_ZERO,
            value,
        ), False
    counter: List[Any] = [budget, _BARE if _per_turn.on("compact_fingerprints") else _NO_BARE]
    if plain is not None:
        counter.append(True)
    fp = _walk(value, counter, 0, ())
    if plain is not None:
        plain.append(bool(counter[2]))
    return fp, counter[0] < 0


#: Bytes in one retained digest. 128 bits: a chance collision between two
#: states of one attribute is out of reach, and even a deliberate one (two
#: values chosen to share a BLAKE2b-128 digest) is a 2**64 search.
DIGEST_SIZE = 16


def _encode(fp: Hashable) -> bytes:
    """The canonical byte string of a fingerprint: ``marshal`` format 0.

    A fingerprint is made of tuples, exact ``str`` / ``int`` / ``float`` /
    ``bool`` / ``bytes`` / ``None`` and nothing else (a type is a short code
    string, a subclass's value its exact base: ``_tagged_scalar``), and
    ``marshal`` writes each of those with its type and length, so the encoding
    is unambiguous. Format 0 has no back-references and no "interned" flag:
    the bytes depend on the values only, never on which objects are shared.
    """
    return marshal.dumps(fp, 0)


def fingerprint_digest(fp: Hashable) -> bytes:
    """The bounded stand-in a view keeps for *fp* between turns (#3998).

    A BLAKE2b-128 hash of :func:`_encode`. Unequal fingerprints have unequal
    encodings, so equal digests mean equal fingerprints, but for a hash
    collision.

    A fingerprint comparison and a digest comparison can still disagree on
    two distinct NaN objects (a tuple comparison calls them unequal, the
    digest equal); NaN renders the same either way, so that costs at most one
    extra sync, never a missed change. ``-0.0`` has its own leaf code
    (``_NEG_ZERO``), so it differs from ``0.0`` in both.
    """
    return hashlib.blake2b(_encode(fp), digest_size=DIGEST_SIZE).digest()


def retained_fingerprint(fp: Hashable) -> Hashable:
    """What a view keeps of *fp* until the next comparison (#3998).

    The digest, so a socket holds 16 bytes per attribute however large the
    attribute is. With the ``digest_fingerprints`` shortcut off (tests only),
    the fingerprint tree itself, as before. Compare a fresh fingerprint with a
    kept one only through this function, so both are of the same kind.
    """
    if _per_turn.on("digest_fingerprints"):
        try:
            return fingerprint_digest(fp)
        except ValueError:  # pragma: no cover - every node kind is encodable
            # Unencodable: keep something equal to nothing, so the value is
            # treated as changed (re-sent), never as unchanged.
            return object()
    return fp


try:
    from ._rust import plain_digest as _plain_digest
except ImportError:  # pragma: no cover - an extension built before #4325
    _plain_digest = None

_NATIVE_TYPES = (dict, list, tuple)


def native_digest(
    value: Any, budget: int = DEFAULT_BUDGET, rows: bool = False
) -> Optional[Tuple[bytes, Optional[bytes]]]:
    """``(digest, row_digests)`` of a plain-data container, natively, or None (#4325).

    The 16-byte retained fingerprint of *value* when it is PLAIN DATA (what
    ``deep_fingerprint(plain=...)`` calls plain) and fits *budget*; computed
    by ``djust._rust.plain_digest`` in one walk, with no fingerprint tree.
    With *rows* and a ``list`` root, ``row_digests`` is one ``bytes`` holding
    a 16-byte digest per item, in order (``digest_rows_changed`` compares two).

    None, and the caller walks in Python exactly as before, for anything else:
    a scalar root, a set, a subclass, an object, a component's state, a value
    past the depth cap, a cycle, a budget overrun (so the truncation warning
    stays Python's), and whenever the ``native_fingerprints`` or
    ``digest_fingerprints`` shortcut is off (with digests off, what is kept is
    the tree, and a native digest would be the other kind).
    """
    if (
        type(value) not in _NATIVE_TYPES
        or _plain_digest is None
        or not _per_turn.on("native_fingerprints")
        or not _per_turn.on("digest_fingerprints")
    ):
        return None
    out = _plain_digest(value, budget, rows)
    if out is None:
        return None
    return out[0], out[2]


def content_digest(value: Any, budget: int = DEFAULT_BUDGET) -> Tuple[Any, bool]:
    """``(retained fingerprint, truncated)`` of *value*: THE producer (#4325).

    The native digest when :func:`native_digest` accepts *value*, else the
    digest of its tree (:func:`retained_fingerprint`). On a truncated walk
    the first item is a fresh ``object()``: an incomplete fingerprint proves
    nothing, so it is equal to nothing.
    """
    native = native_digest(value, budget)
    if native is not None:
        return native[0], False
    fp, truncated = deep_fingerprint(value, budget)
    if truncated:
        return object(), True
    return retained_fingerprint(fp), False


def deep_digest(value: Any, budget: int = DEFAULT_BUDGET) -> Tuple[Any, bool]:
    """``(retained fingerprint, truncated)`` of *value*: :func:`content_digest`."""
    return content_digest(value, budget)


#: One container kept by an event snapshot for the sync of the same turn
#: (#3999, #4325): ``(value, fingerprint tree, plain, digest, row digests)``.
#:
#: * ``[0]`` is the object itself, held so its ``id()`` cannot be reused while
#:   the entry lives;
#: * ``[1]`` is its fingerprint tree, or None when the native digest was taken
#:   instead (then ``[3]`` is set);
#: * ``[2]`` is True when the walk found it plain data (always True with a
#:   native digest, which only plain data has);
#: * ``[3]`` is the native digest (:func:`native_digest`), which IS the
#:   retained fingerprint of the value, or None;
#: * ``[4]`` is the row digests of a list with a native digest, or None.
SnapshotEntry = Tuple[Any, Optional[Hashable], bool, Optional[bytes], Optional[bytes]]


def entry_retained(entry: SnapshotEntry) -> Any:
    """The retained fingerprint of the object a :data:`SnapshotEntry` walked."""
    native = entry[3]
    return native if native is not None else retained_fingerprint(entry[1])


class AssignsSnapshot(dict):  # type: ignore[type-arg]
    """What ``websocket._snapshot_assigns`` returns: a plain dict, plus the walks.

    It compares, iterates and copies as the dict it is. ``fingerprints`` maps
    the ``id()`` of every exact ``dict`` / ``list`` / ``tuple`` attribute the
    snapshot walked completely to a :data:`SnapshotEntry`, so the Rust sync of
    the same turn can take the post-event snapshot's walk instead of walking
    the container again (#3999). See :class:`ChangedKeys` for when the sync may.
    """

    __slots__ = ("fingerprints",)

    def __init__(self) -> None:
        super().__init__()
        self.fingerprints: Dict[int, SnapshotEntry] = {}

    def __eq__(self, other: object) -> bool:
        # Value semantics are the dict's: ``fingerprints`` is a side channel
        # (the walks) and never takes part in equality.
        return dict.__eq__(self, other)  # type: ignore[no-any-return]

    def __ne__(self, other: object) -> bool:
        return dict.__ne__(self, other)  # type: ignore[no-any-return]

    __hash__ = None  # type: ignore[assignment]  # mutable, like dict


class ChangedKeys(set):  # type: ignore[type-arg]
    """``_compute_changed_keys(pre, post)``: the changed names, plus the post walks.

    The set a handler's turn assigns to ``view._changed_keys``. It carries the
    post-event snapshot's :attr:`AssignsSnapshot.fingerprints` and the turn it
    was computed in. ``_sync_state_to_rust`` reuses those walks only when the
    view's ``_changed_keys`` is still this very object, in the same turn, and
    no application code can have run between the snapshot and the sync
    (``rust_bridge._snapshot_walks_for_sync``). Anything that replaces the set
    (``set_changed_keys`` builds a new one) or ends the turn drops the reuse,
    and the sync walks as before.
    """

    __slots__ = ("fingerprints", "pre_fingerprints", "turn")

    def __init__(self, keys: Any = ()) -> None:
        super().__init__(keys)
        self.fingerprints: Optional[Dict[int, SnapshotEntry]] = None
        #: The PRE-event snapshot's walks, for the item-level list delta
        #: (#4325): which rows of a list the handler changed in place.
        self.pre_fingerprints: Optional[Dict[int, SnapshotEntry]] = None
        self.turn: Any = None

    def __eq__(self, other: object) -> bool:
        # Value semantics are the set's: the walks and the turn are a side
        # channel and never take part in equality.
        return set.__eq__(self, other)  # type: ignore[no-any-return]

    def __ne__(self, other: object) -> bool:
        return set.__ne__(self, other)  # type: ignore[no-any-return]

    __hash__ = None  # type: ignore[assignment]  # mutable, like set


def changed_keys_after(changed: Any, post: Any, pre: Any = None) -> ChangedKeys:
    """*changed* as a :class:`ChangedKeys` carrying the snapshots' walks and the turn."""
    out = ChangedKeys(changed)
    if isinstance(post, AssignsSnapshot):
        turn = current_turn()
        if turn is not None:
            out.fingerprints = post.fingerprints
            if isinstance(pre, AssignsSnapshot):
                out.pre_fingerprints = pre.fingerprints
            out.turn = weakref.ref(turn)
    return out


#: Exact builtin scalar types: no subclass, so no ``STATE_MARKER`` either. The
#: fast path in :func:`_walk` handles them inline; a subclass of one (Django's
#: ``SafeString``, an ``IntEnum``) takes the general path, which fingerprints
#: it the same way.
_EXACT_SCALARS = frozenset(_IMMUTABLE_LEAVES)

#: The type in a tagged leaf ``("v", code, value)`` is a short code, not the
#: type object, so a fingerprint is plain data :func:`_encode` can write. A
#: subclass is spelled ``"module.qualname"``, which always holds a dot and so
#: never equals a code.
_TYPE_CODE = {str: "s", int: "n", float: "f", bool: "b", bytes: "y", type(None): "0"}

#: The code of a ``-0.0`` leaf (#4325). ``0.0 == -0.0``, but the two render
#: differently, so a tree comparison must not call them equal: the per-row
#: pick of a list delta compares row trees, and with one code for both it
#: kept Rust's old ``0.0`` after a handler stored ``-0.0``.
_NEG_ZERO = "f-"

#: The compact encoding (#3998). A fingerprint tree is built several times per
#: turn (the event snapshot, the Rust sync, dirty tracking), so its size is
#: allocation per turn; with ``digest_fingerprints`` off it is also what a
#: socket keeps between turns. In the wide encoding every leaf was a
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
        return (
            value
            if t in bare
            else (
                _TAG_VALUE,
                _TYPE_CODE[t]
                if value or t is not float or _copysign(1.0, value) > 0
                else _NEG_ZERO,
                value,
            )
        )
    if t is not dict and t is not list and t is not tuple:
        return _walk_general(value, counter, depth, path_ids)
    counter[0] -= 1
    if counter[0] < 0:
        return (_TAG_TRUNCATED, id(value))
    vid = id(value)
    if depth >= _MAX_DEPTH or vid in path_ids:
        if len(counter) > 2:
            counter[2] = False  # not walked: not known to be plain
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
                kf: Hashable = (
                    k
                    if tk in bare
                    else (
                        _TAG_VALUE,
                        _TYPE_CODE[tk]
                        if k or tk is not float or _copysign(1.0, k) > 0
                        else _NEG_ZERO,
                        k,
                    )
                )
            else:
                kf = _walk(k, counter, child_depth, inner)
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
            tv = type(v)
            if tv in _EXACT_SCALARS:
                counter[0] -= 1
                vf: Hashable = (
                    v
                    if tv in bare
                    else (
                        _TAG_VALUE,
                        _TYPE_CODE[tv]
                        if v or tv is not float or _copysign(1.0, v) > 0
                        else _NEG_ZERO,
                        v,
                    )
                )
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
            append(
                v
                if tv in bare
                else (
                    _TAG_VALUE,
                    _TYPE_CODE[tv] if v or tv is not float or _copysign(1.0, v) > 0 else _NEG_ZERO,
                    v,
                )
            )
        else:
            append(_walk(v, counter, child_depth, inner))
            if counter[0] < 0:
                return (_TAG_TRUNCATED, vid)
    return (_TAG_LIST if t is list else _TAG_TUPLE, tuple(out))


def _walk_general(
    value: Any, counter: List[Any], depth: int, path_ids: Tuple[int, ...]
) -> Hashable:
    if len(counter) > 2:
        # The plain-data flag ``deep_fingerprint(plain=...)`` asked for: every
        # shape that is not an exact dict / list / tuple or an exact scalar
        # reaches this function.
        counter[2] = False
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
        return _tagged_scalar(value)
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
                # A set's iteration order follows its history, not its
                # contents, so its members are put in one canonical order:
                # equal sets give equal tuples, and so equal digests.
                # Members' fingerprints are distinct values with distinct
                # encodings, so the order is total.
                return (_TAG_SET, tuple(sorted(out, key=_encode)))
            except (TypeError, ValueError):
                # Identity fallback for a member that cannot be encoded.
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
        "_explicit_child_carry_identity",
        "_explicit_child_render_regions",
        "_explicit_child_vdom_bindings",
        "_explicit_child_vdom_tracking",
        "_explicit_child_render_scope",
        "_explicit_child_rendered_full",
        "_explicit_child_state_tracked",
        # ADR-038 E2-1: the keys registered providers rendered this pass.
        "_explicit_context_provider_keys",
        "_djust_child_disposed",
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
        # Save frequency (design: save frequency): the view option, and the
        # per-turn request ``save_state()`` / ``@event_handler(save=True)``
        # leaves for the commit (a framework signal, not state).
        "state_save_interval",
        "_djust_save_requested",
        "_djust_save_requested_async",
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
        # Snapshot reuse bookkeeping (``_snapshot_reuse``): the kept snapshot
        # and the turn counter ``run_turn`` bumps.
        "_djust_kept_snapshot",
        "_djust_turn_seq",
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
        "_cached_csrf_token",
        "_djust_id_counter",  # Render-cycle ID allocation is not reactive state.
        "_sync_done_this_cycle",
        "_force_full_html",
        # Whether a full render was pending before this turn's first public
        # request (``set_changed_keys`` / ``request_full_render``), so
        # ``cancel_full_render`` undoes only that request (#4129). Consumed per
        # turn by ``_resolve_skip_render``.
        "_djust_full_render_prior",
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
        "_render_assigned_ids",
        # The SSE transport's stream id (``on_view_mounted``), the twin of
        # ``_websocket_session_id`` (#3248 review).
        "_sse_session_id",
    }
)
