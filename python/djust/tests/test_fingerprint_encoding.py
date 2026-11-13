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
* ``share_structure`` returns an equal fingerprint built from the other's rows.

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
from djust.change_detection import deep_fingerprint, share_structure
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


# --- one tree where there were two ----------------------------------------------------


def _rows(n: int) -> List[Any]:
    return [
        {"id": i, "name": f"item {i}", "qty": i % 7, "tags": ["alpha", f"t{i}"]} for i in range(n)
    ]


def test_share_structure_returns_an_equal_fingerprint_made_of_the_held_rows():
    held_rows = _rows(50)
    held = deep_fingerprint(held_rows)[0]
    rows = copy.deepcopy(held_rows)
    rows[7]["name"] += "!"
    fresh = deep_fingerprint(rows)[0]
    shared = share_structure(fresh, held)
    assert shared == fresh and shared != held
    same_object = [a is b for a, b in zip(shared[1], held[1])]
    assert same_object.count(False) == 1 and not same_object[7]
    assert all(a is not b for a, b in zip(fresh[1], held[1]))


def test_share_structure_reaches_the_lists_inside_a_dict():
    board = {"todo": _rows(10), "done": _rows(10)}
    held = deep_fingerprint(board)[0]
    board["done"][3]["qty"] += 1
    fresh = deep_fingerprint(board)[0]
    shared = share_structure(fresh, held)
    assert shared == fresh
    # flat dict items: key, value, key, value
    assert shared[1][1] is held[1][1], "the untouched column is the held object"
    assert shared[1][3] is not held[1][3]
    assert sum(a is b for a, b in zip(shared[1][3][1], held[1][3][1])) == 9


@pytest.mark.parametrize(
    "change",
    [
        lambda rows: rows.insert(0, {"id": -1}),
        lambda rows: rows.pop(),
        lambda rows: rows.clear(),
        lambda rows: rows.reverse(),
        lambda rows: rows.__setitem__(0, "a row that became a string"),
        lambda rows: rows.__setitem__(0, [1, 2]),
        lambda rows: None,
    ],
)
def test_share_structure_never_changes_what_a_fingerprint_is_equal_to(change):
    held_rows = _rows(12)
    held = deep_fingerprint(held_rows)[0]
    rows = copy.deepcopy(held_rows)
    change(rows)
    fresh = deep_fingerprint(rows)[0]
    frozen = copy.deepcopy(fresh)
    shared = share_structure(fresh, held)
    assert shared == frozen
    assert fresh == frozen, "the fingerprint passed in was changed"
    assert held == deep_fingerprint(held_rows)[0], "the held fingerprint was changed"


@pytest.mark.parametrize("other", [None, object(), "text", 5, ("x", 1), ("L", "not a tuple")])
def test_share_structure_leaves_a_fingerprint_alone_beside_anything_else(other):
    fresh = deep_fingerprint(_rows(3))[0]
    assert share_structure(fresh, other) is fresh
    assert share_structure(other, fresh) is other


def test_a_list_against_a_tuple_of_the_same_rows_shares_nothing():
    rows = _rows(4)
    as_list = deep_fingerprint(rows)[0]
    as_tuple = deep_fingerprint(tuple(rows))[0]
    assert as_list != as_tuple
    assert share_structure(as_list, as_tuple) is as_list


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
