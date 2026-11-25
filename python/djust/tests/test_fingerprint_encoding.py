"""The compact fingerprint decides exactly what the wide one decided (#3998).

A view holds a structural fingerprint of its containers for as long as its
socket is open, so the size of that fingerprint is memory per socket. The
compact encoding keeps a ``str`` / ``int`` / ``bytes`` / ``None`` leaf as the
value itself and a dict's items as one flat tuple. It is only safe if no two
values that had different fingerprints now have equal ones, and no two that
had equal ones now differ: change detection would otherwise skip a render, or
render for nothing.

The wide encoding is still in the code, behind ``_per_turn``, so both are run
here over the same values, in one process, and compared:

* equality of two values' fingerprints is the same in both encodings, over a
  corpus built to collide (``1`` / ``True`` / ``1.0`` / ``"1"`` / ``b"1"``, a
  dict against the list of its keys and values, a ``str`` subclass);
* the budget runs out at the same node;
* a fingerprint taken before an in-place change does not change with it, per
  shape, and is not equal to one taken after;
* the 16-byte digest a view keeps in place of a fingerprint (``fingerprint_digest``)
  is equal for two values exactly when their fingerprints are, over the same
  corpus and sweep (the one finer case, ``0.0`` against ``-0.0``, is named).

Nothing here reads a clock or a resident size: sizes are ``sys.getsizeof``
sums over the tuples.
"""

from __future__ import annotations

import copy
import enum
import itertools
import random
import sys
from typing import Any, List

import pytest

from djust import _per_turn, change_detection
from djust.change_detection import deep_digest, deep_fingerprint, fingerprint_digest
from djust.state_pass import fingerprint_and_convert
from djust.tests import _per_turn_harness as harness
from djust.tests import _view_memory


class _SafeStr(str):
    pass


class _Color(enum.IntEnum):
    RED = 1


class _DictSub(dict):
    pass


class _Marked:
    _djust_fingerprint_state = True

    def __init__(self, state: Any) -> None:
        self.state = state


#: Scalars that are equal across types, or look alike: the collisions a bare
#: leaf could introduce.
LOOKALIKES: List[Any] = [
    1,
    True,
    1.0,
    "1",
    b"1",
    0,
    False,
    0.0,
    -0.0,
    "",
    b"",
    None,
    "None",
    -1,
    -2,
    2**80,
    float(2**80),
    _SafeStr("1"),
    _Color.RED,
    "v",
    "i",
    "L",
    "D",
]


def corpus() -> List[Any]:
    """Values built to have equal fingerprints only when they are the same shape."""
    out: List[Any] = []
    for value in LOOKALIKES:
        out += [[value], (value,), {"k": value}, {value: "k"} if value == value else [value]]
        out += [[[value]], {"k": [value]}, {"k": {"k": value}}, [value, value], {value}]
    # A dict against the flat sequence of its own keys and values.
    out += [
        {"a": "b"},
        ["a", "b"],
        ("a", "b"),
        {"a": "b", "c": "d"},
        ["a", "b", "c", "d"],
        {"a": ("b", "c", "d")},
        {"a": ["b", "c", "d"]},
        {"a": {"b": "c"}},
        {"a": ["b", "c"]},
        {"a": "b", "c": {"d": "e"}},
        {("a", "b"): "c"},
        {"a": None},
        {None: "a"},
        ["a", None],
        [],
        (),
        {},
        [[]],
        [()],
        [{}],
        _DictSub(a="b"),
        _Marked({"a": "b"}),
        _Marked(["a", "b"]),
        [_Marked({"a": 1})],
        [_Marked({"a": True})],
        {"s": {1, "x"}},
        {"s": frozenset({1, "x"})},
        {"s": {True, "x"}},
        # What a wide leaf or tag looks like, held as DATA.
        ("v", int, 1),
        [("v", int, 1)],
        ("i", 12345),
        [("L", ())],
        {"k": ("D", ())},
    ]
    return out


def _fingerprints(values: List[Any], monkeypatch: Any, *off: str) -> List[Any]:
    with harness.shortcuts(monkeypatch, *off):
        return [deep_fingerprint(value)[0] for value in values]


def test_two_values_are_equal_in_one_encoding_exactly_when_in_the_other(monkeypatch):
    values = corpus()
    compact = _fingerprints(values, monkeypatch)
    wide = _fingerprints(values, monkeypatch, "compact_fingerprints")
    assert compact != wide  # the two encodings are different trees
    disagreements = [
        (values[i], values[j])
        for i, j in itertools.combinations(range(len(values)), 2)
        if (compact[i] == compact[j]) != (wide[i] == wide[j])
    ]
    assert disagreements == []
    # Not vacuous in either direction: some pairs are equal, most are not.
    equal = sum(wide[i] == wide[j] for i, j in itertools.combinations(range(len(values)), 2))
    assert 0 < equal < len(values)


def _random_value(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.randrange(10 if depth < 3 else 6)
    if kind < 6:
        return rng.choice(LOOKALIKES[:16])
    if kind == 6:
        return [_random_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    if kind == 7:
        return tuple(_random_value(rng, depth + 1) for _ in range(rng.randrange(4)))
    if kind == 8:
        return {
            rng.choice(["a", "b", 1, True, None, 1.0]): _random_value(rng, depth + 1)
            for _ in range(rng.randrange(4))
        }
    return _Marked({"k": _random_value(rng, depth + 1)})


def test_generated_values_agree_too(monkeypatch):
    """A seeded sweep: 400 small nested values, every pair compared in both."""
    rng = random.Random(3998)
    values = [_random_value(rng) for _ in range(400)]
    compact = _fingerprints(values, monkeypatch)
    wide = _fingerprints(values, monkeypatch, "compact_fingerprints")
    pairs = list(itertools.combinations(range(len(values)), 2))
    assert all((compact[i] == compact[j]) == (wide[i] == wide[j]) for i, j in pairs)
    assert any(compact[i] == compact[j] for i, j in pairs)


@pytest.mark.parametrize("budget", [0, 1, 2, 3, 5, 8, 17, 60, change_detection.DEFAULT_BUDGET])
def test_the_budget_runs_out_at_the_same_node(monkeypatch, budget):
    rows = [{"id": i, "name": f"row {i}", "tags": ["a", i], "on": i % 2 == 0} for i in range(6)]
    values = corpus() + [rows, {"rows": rows}, "plain", None, 7]
    with harness.shortcuts(monkeypatch):
        compact = [deep_fingerprint(value, budget)[1] for value in values]
        fused = [fingerprint_and_convert(value, "k", budget)[1] for value in values]
    with harness.shortcuts(monkeypatch, "compact_fingerprints"):
        wide = [deep_fingerprint(value, budget)[1] for value in values]
    assert compact == wide
    assert fused == wide


def test_the_single_pass_walk_returns_the_same_compact_fingerprint():
    rows = [{"id": i, "name": f"row {i}", "tags": ["a", i], "on": i % 2 == 0} for i in range(40)]
    for value in corpus() + [rows, {"rows": rows}]:
        assert fingerprint_and_convert(value, "k")[0] == deep_fingerprint(value)[0], value


@pytest.mark.parametrize("value", [None, 0, "", False, 0.0, b"", 1, "x"])
def test_a_scalar_root_is_never_a_bare_or_falsy_fingerprint(value):
    """A caller stores a fingerprint beside a "nothing stored" default; a bare
    ``None`` or ``0`` for a root would read as that default."""
    fingerprint, truncated = deep_fingerprint(value)
    assert not truncated
    assert type(fingerprint) is tuple and fingerprint
    assert fingerprint_and_convert(value, "k")[0] == fingerprint


# --- mutation after capture, per shape ------------------------------------------------


def _append(value: Any) -> None:
    value.append("new")


def _set_item(value: Any) -> None:
    value["k"] = "changed"


def _nested_row(value: Any) -> None:
    value[1]["name"] += "!"


def _nested_list(value: Any) -> None:
    value["rows"][0]["tags"].append("t")


def _swap_type(value: Any) -> None:
    value[0] = True  # was 1


def _add_to_set(value: Any) -> None:
    value["s"].add("new")


def _component_state(value: Any) -> None:
    value.state["rows"].append(3)


def _delete(value: Any) -> None:
    del value[0]


def _reorder(value: Any) -> None:
    value.reverse()


SHAPES = [
    ("append", lambda: ["a", "b"], _append),
    ("dict_item", lambda: {"k": "v", "n": 1}, _set_item),
    ("row_in_list", lambda: [{"name": "a"}, {"name": "b"}], _nested_row),
    ("list_in_row_in_dict", lambda: {"rows": [{"tags": ["x"]}]}, _nested_list),
    ("int_to_bool", lambda: [1, 2], _swap_type),
    ("set_in_dict", lambda: {"s": {"a"}}, _add_to_set),
    ("component_state", lambda: _Marked({"rows": [1, 2]}), _component_state),
    ("delete", lambda: ["a", "b", "c"], _delete),
    ("reorder", lambda: [{"id": 1}, {"id": 2}], _reorder),
]


@pytest.mark.parametrize("label,make,mutate", SHAPES, ids=[shape[0] for shape in SHAPES])
@pytest.mark.parametrize("encoding", ["compact", "wide"])
def test_a_captured_fingerprint_does_not_follow_a_later_change(
    monkeypatch, encoding, label, make, mutate
):
    """#1039, for every shape: a fingerprint is a value, not a view of the state."""
    off = () if encoding == "compact" else ("compact_fingerprints",)
    with harness.shortcuts(monkeypatch, *off):
        value = make()
        captured = deep_fingerprint(value)[0]
        frozen = copy.deepcopy(captured)
        fused = fingerprint_and_convert(value, "k")[0]
        mutate(value)
        after = deep_fingerprint(value)[0]
    assert captured == frozen, "the captured fingerprint changed with the state"
    assert fused == frozen
    assert after != captured, "the change is invisible to the fingerprint"


# --- a digest where there was a tree (#3998) ------------------------------------------


def _rows(n: int) -> List[Any]:
    return [
        {"id": i, "name": f"item {i}", "qty": i % 7, "tags": ["alpha", f"t{i}"]} for i in range(n)
    ]


def _signed_zero_only(a: Any, b: Any) -> bool:
    """Do fingerprints *a* and *b* differ only in the sign of a zero?"""
    return repr(a).replace("-0.0", "0.0") == repr(b).replace("-0.0", "0.0") and repr(a) != repr(b)


def _digest_disagreements(values: List[Any]) -> List[Any]:
    fingerprints = [deep_fingerprint(value)[0] for value in values]
    digests = [fingerprint_digest(fp) for fp in fingerprints]
    out = []
    for i, j in itertools.combinations(range(len(values)), 2):
        same_fp = fingerprints[i] == fingerprints[j]
        same_digest = digests[i] == digests[j]
        if same_digest and not same_fp:
            out.append(("MISSED CHANGE", values[i], values[j]))
        elif (
            same_fp and not same_digest and not _signed_zero_only(fingerprints[i], fingerprints[j])
        ):
            out.append(("extra render", values[i], values[j]))
    return out


@pytest.mark.parametrize("encoding", ["compact", "wide"])
def test_the_digest_is_equal_exactly_when_the_fingerprint_is(monkeypatch, encoding):
    """The corpus built to collide: ``1`` / ``True`` / ``1.0`` / ``"1"`` /
    ``b"1"``, a dict against the flat list of its items, a ``str`` subclass,
    an ``IntEnum``, sets, values spelled like the tags. A digest that is equal
    where the fingerprints differ would skip a render; that never happens. The
    other way round only ``0.0`` / ``-0.0`` (equal floats that render
    differently), where the digest is the more exact of the two."""
    off = () if encoding == "compact" else ("compact_fingerprints",)
    with harness.shortcuts(monkeypatch, *off):
        values = corpus()
        assert _digest_disagreements(values) == []
        digests = {fingerprint_digest(deep_fingerprint(value)[0]) for value in values}
    # Not vacuous: the corpus has equal pairs, and most are different.
    assert 1 < len(digests) < len(values)
    assert fingerprint_digest(deep_fingerprint([0.0])[0]) != fingerprint_digest(
        deep_fingerprint([-0.0])[0]
    )


def test_generated_values_have_equal_digests_exactly_when_equal_fingerprints():
    rng = random.Random(39980)
    values = [_random_value(rng) for _ in range(400)]
    assert _digest_disagreements(values) == []


def test_a_digest_is_sixteen_bytes_whatever_the_size_of_the_value():
    for value in ([], _rows(1), _rows(1000), {"rows": _rows(200), "title": "x" * 10_000}):
        digest, truncated = deep_digest(value)
        assert not truncated
        assert type(digest) is bytes and len(digest) == change_detection.DIGEST_SIZE == 16


def test_a_truncated_value_has_no_digest_and_equals_nothing():
    rows = _rows(50)
    first, truncated = deep_digest(rows, budget=10)
    second, _ = deep_digest(rows, budget=10)
    assert truncated
    assert type(first) is not bytes and first != second


@pytest.mark.parametrize("label,make,mutate", SHAPES, ids=[shape[0] for shape in SHAPES])
def test_a_kept_digest_sees_every_in_place_change(label, make, mutate):
    """#1039 for the digest: what the socket keeps is bytes, so a later
    in-place edit cannot reach it, and the next digest differs."""
    value = make()
    kept, _ = deep_digest(value)
    frozen = bytes(kept)
    mutate(value)
    after, _ = deep_digest(value)
    assert kept == frozen
    assert after != kept, label


def test_a_kept_digest_sees_a_change_deep_in_nested_containers():
    board = {"columns": {"todo": _rows(20), "done": _rows(20)}, "meta": ({"n": [1, [2, [3]]]},)}
    kept, _ = deep_digest(board)
    board["meta"][0]["n"][1][1].append(4)
    assert deep_digest(board)[0] != kept
    kept, _ = deep_digest(board)
    board["columns"]["done"][19]["tags"][1] = "moved"
    assert deep_digest(board)[0] != kept
    kept, _ = deep_digest(board)
    board["columns"]["todo"], board["columns"]["done"] = (
        board["columns"]["done"],
        board["columns"]["todo"],
    )
    assert deep_digest(board)[0] != kept
    # Equal content built again from scratch: the same digest.
    assert deep_digest(copy.deepcopy(board))[0] == deep_digest(board)[0]


def test_two_equal_sets_have_one_digest_whatever_their_history():
    """A set's iteration order follows its insertions and deletions; its
    fingerprint (and so its digest) must follow only its members."""
    grown = set()
    for i in range(200):
        grown.add(f"k{i}")
    for i in range(0, 200, 3):
        grown.discard(f"k{i}")
    fresh = {f"k{i}" for i in reversed(range(200)) if i % 3}
    assert grown == fresh
    assert deep_fingerprint(grown)[0] == deep_fingerprint(fresh)[0]
    assert deep_digest(grown)[0] == deep_digest(fresh)[0]


def test_a_subclass_with_its_own_repr_cannot_hide_a_change():
    """The digest hashes ``repr``; a ``str`` subclass whose ``repr`` is a
    constant must still be told apart by value."""

    class Opaque(str):
        def __repr__(self) -> str:
            return "Opaque()"

    class Same(int):
        def __repr__(self) -> str:
            return "0"

        def __eq__(self, other: object) -> bool:
            return True

        __hash__ = int.__hash__

    assert deep_digest([Opaque("a")])[0] != deep_digest([Opaque("b")])[0]
    assert deep_digest({"n": Same(1)})[0] != deep_digest({"n": Same(2)})[0]
    assert deep_digest([Opaque("a")])[0] != deep_digest(["a"])[0]


def test_a_model_instance_and_a_queryset_are_kept_by_identity():
    """What the comparison did for a model or a queryset (``id()``, the
    Phoenix contract) is what the digest does: reassigning one is a change,
    and the same object is not."""
    from django.contrib.auth.models import User

    first = User(username="a")
    second = User(username="a")
    kept, _ = deep_digest([first])
    assert deep_digest([first])[0] == kept
    assert deep_digest([second])[0] != kept
    queryset = User.objects.none()
    kept, _ = deep_digest({"qs": queryset})
    assert deep_digest({"qs": queryset})[0] == kept
    assert deep_digest({"qs": User.objects.none()})[0] != kept


# --- what it saves --------------------------------------------------------------------


def _tree_bytes(value: Any) -> int:
    """Bytes of the fingerprint's own tuples (the leaves are the state's objects)."""
    seen: set = set()
    _view_memory.deep_bytes(value, seen)
    return _view_memory.deep_bytes(deep_fingerprint(value)[0], seen)


def test_the_compact_tree_is_under_a_third_of_the_wide_one(monkeypatch):
    rows = _rows(500)
    with harness.shortcuts(monkeypatch):
        compact = _tree_bytes(rows)
    with harness.shortcuts(monkeypatch, "compact_fingerprints"):
        assert not _per_turn.on("compact_fingerprints")
        wide = _tree_bytes(rows)
    assert compact * 3 < wide
    assert compact > 0


def test_the_tree_grows_with_the_rows_and_no_faster(monkeypatch):
    with harness.shortcuts(monkeypatch):
        sizes = [_tree_bytes(_rows(n)) for n in (100, 200, 400, 800)]
    intercept, slope, worst = _view_memory.fit([100, 200, 400, 800], sizes)
    assert worst < 0.02, (sizes, worst)
    assert slope > 0 and sys.getsizeof(()) <= slope
