"""The native plain-data digest agrees with the fingerprint tree (#4325).

``change_detection.native_digest`` replaces the Python fingerprint walk and its
BLAKE2b digest with one Rust walk, for exactly the values the Python walk calls
plain data. A wrong encoding there is a missed change, so this file pins:

* equivalence: over seeded random plain values and random single mutations,
  two values have equal native digests exactly when their trees are equal;
* acceptance: the native walk accepts a value exactly when the Python walk
  finds it plain and within the budget, node for node at the budget boundary;
* the edge cases a byte encoding gets wrong (``True`` / ``1`` / ``1.0``,
  ``-0.0``, big ints, surrogates, ``str`` / ``bytes``, ``list`` / ``tuple``,
  dict order and key/value boundaries, the depth cap, cycles, subclasses);
* the per-row digests the list delta compares;
* the kind invariant: every producer of a retained fingerprint gives one
  value the same kind of digest;
* mutate-before-and-after on the snapshot that decides whether a turn renders.

The frames themselves are compared, switch on and off, by
``test_per_turn_oracle.py`` (``native_fingerprints``, ``big_late_writer``).
"""

from __future__ import annotations

import random
from typing import Any, List

import pytest

from djust import _per_turn, _rust, change_detection
from djust.change_detection import (
    DEFAULT_BUDGET,
    content_digest,
    deep_digest,
    deep_fingerprint,
    entry_retained,
    native_digest,
    retained_fingerprint,
)


@pytest.fixture(autouse=True)
def _all_on(monkeypatch):
    monkeypatch.setattr(_per_turn, "disabled", frozenset())


def tree(value: Any) -> Any:
    fp, truncated = deep_fingerprint(value)
    assert not truncated
    return fp


def is_plain(value: Any, budget: int = DEFAULT_BUDGET) -> bool:
    plain: List[bool] = []
    _, truncated = deep_fingerprint(value, budget, plain=plain)
    return plain[0] and not truncated


def digest(value: Any) -> bytes:
    out = native_digest(value)
    assert out is not None, value
    return out[0]


# --- equivalence over random values ---------------------------------------------------

SCALARS = [
    0,
    1,
    -1,
    True,
    False,
    1.0,
    0.0,
    -0.0,
    2.5,
    None,
    "",
    "1",
    "a",
    "é",
    "\U0001f600",
    "\ud800",
    b"",
    b"1",
    2**63 - 1,
    -(2**63),
    2**63,
    -(2**63) - 1,
    2**200,
]


def random_value(rng: random.Random, depth: int = 0) -> Any:
    roll = rng.random()
    if depth > 3 or roll < 0.45:
        return rng.choice(SCALARS)
    size = rng.randrange(0, 4)
    if roll < 0.65:
        return [random_value(rng, depth + 1) for _ in range(size)]
    if roll < 0.8:
        return tuple(random_value(rng, depth + 1) for _ in range(size))
    out = {}
    for _ in range(size):
        key = rng.choice([s for s in SCALARS if s is not None] + [(1, "a")])
        out[key] = random_value(rng, depth + 1)
    return out


def random_root(rng: random.Random) -> Any:
    """A list, tuple or dict holding one to four random values."""
    root = rng.choice([list, tuple, dict])
    size = rng.randrange(1, 5)
    if root is dict:
        return {rng.choice(SCALARS): random_value(rng, 1) for _ in range(size)}
    return root(random_value(rng, 1) for _ in range(size))


def mutate(rng: random.Random, value: Any) -> Any:
    """A copy of *value* with one random change somewhere (maybe a no-op)."""
    if isinstance(value, (list, tuple)) and value and rng.random() < 0.7:
        items = list(value)
        i = rng.randrange(len(items))
        items[i] = mutate(rng, items[i])
        return type(value)(items)
    if isinstance(value, dict) and value and rng.random() < 0.7:
        keys = list(value)
        k = rng.choice(keys)
        out = dict(value)
        if rng.random() < 0.2:
            # Same items, another order.
            return dict(reversed(list(out.items())))
        out[k] = mutate(rng, out[k])
        return out
    return random_value(rng)


@pytest.mark.parametrize("seed", range(8))
def test_equal_digests_exactly_when_the_trees_are_equal(seed):
    rng = random.Random(4325 + seed)
    seen = {True: 0, False: 0}
    for _ in range(400):
        a = random_root(rng)
        b = mutate(rng, a) if rng.random() < 0.9 else a
        if type(b) not in (list, tuple, dict):
            continue
        assert (digest(a) == digest(b)) == (tree(a) == tree(b)), (a, b)
        seen[tree(a) == tree(b)] += 1
    # Both outcomes happen often: the fuzz is not all "changed".
    assert min(seen.values()) > 30, seen


def test_a_copy_has_the_same_digest_and_rows():
    rng = random.Random(7)
    value = [random_value(rng) for _ in range(50)]
    copy = [v for v in value]
    a, b = native_digest(value, rows=True), native_digest(copy, rows=True)
    assert a == b and a is not None and len(a[1]) == 16 * 50


# --- acceptance: exactly the plain values, within the budget --------------------------


class Str(str):
    pass


class Marked:
    _djust_fingerprint_state = True
    state = {"a": 1}


def _cycle() -> Any:
    value: List[Any] = [1]
    value.append(value)
    return value


def _nested(levels: int) -> Any:
    value: Any = []
    for _ in range(levels - 1):
        value = [value]
    return value


@pytest.mark.parametrize(
    "value",
    [
        [{1, 2}],
        [frozenset()],
        [object()],
        [Str("x")],
        {"k": Str("x")},
        [Marked()],
        {(1, frozenset()): 1},
        _cycle(),
        _nested(33),
        {"a": _nested(32)},
        [1.5, bytearray(b"x")],
    ],
    ids=[
        "set",
        "frozenset",
        "object",
        "str_subclass",
        "str_subclass_value",
        "state_marked",
        "frozenset_in_key",
        "cycle",
        "depth_33",
        "depth_33_under_dict",
        "bytearray",
    ],
)
def test_what_python_does_not_call_plain_is_refused(value):
    assert not is_plain(value)
    assert native_digest(value) is None


def test_the_depth_cap_matches_python_to_the_level():
    assert is_plain(_nested(32)) and native_digest(_nested(32)) is not None
    assert not is_plain(_nested(33)) and native_digest(_nested(33)) is None


def test_scalar_roots_and_subclass_roots_stay_python():
    for value in (1, "x", None, 1.5, Str("x"), {1}, Marked()):
        assert native_digest(value) is None
    assert content_digest(1) == deep_digest(1)


def test_the_budget_boundary_is_pythons_node_for_node():
    rng = random.Random(11)
    for _ in range(60):
        value = [random_value(rng) for _ in range(rng.randrange(1, 12))]
        out = _rust.plain_digest(value, DEFAULT_BUDGET, False)
        assert out is not None
        used = out[1]
        assert is_plain(value, used) and not is_plain(value, used - 1)
        assert native_digest(value, used) is not None
        assert native_digest(value, used - 1) is None


def test_an_over_budget_value_still_truncates_and_warns_in_python():
    value = list(range(DEFAULT_BUDGET + 1))
    assert native_digest(value) is None
    retained, truncated = content_digest(value)
    assert truncated and retained != content_digest(value)[0]


# --- the encoding's edge cases ---------------------------------------------------------

DISTINCT = [
    [[1], [True], [1.0], ["1"], [b"1"], [1.5]],
    [[0], [False], [0.0], [-0.0], [None], [""], [b""]],
    [[[1]], [(1,)], [{1: 1}], [[[1]]]],
    [{"a": 1, "b": 2}, {"b": 2, "a": 1}, {"a": 2, "b": 1}, {"ab": 1}],
    [{1: "a"}, {True: "a"}, {1.0: "a"}, {"1": "a"}],
    [[2**63 - 1], [2**63], [-(2**63)], [-(2**63) - 1], [2**200], [-(2**200)]],
    [["ab", "c"], ["a", "bc"], ["abc"], ["a", "b", "c"]],
    [["\ud800"], ["\ud801"], ["\udc00"], ["\U0001f600"], ["é"], ["e"]],
    [[b"ab", b"c"], [b"a", b"bc"], [b"abc"]],
    [[float("inf")], [float("-inf")], [1e308], [5e-324]],
    [[], (), {}],
]


@pytest.mark.parametrize("group", DISTINCT, ids=[str(i) for i in range(len(DISTINCT))])
def test_values_python_tells_apart_have_different_digests(group):
    for i, a in enumerate(group):
        for b in group[i + 1 :]:
            assert tree(a) != tree(b), (a, b)
            assert digest(a) != digest(b), (a, b)


def test_equal_values_built_differently_share_a_digest():
    # Interned or not, small int cache or not, built by concatenation or not.
    a = ["x" * 3, 10**20 // 10**10, float("1.5"), ("a" + "é")[1:]]
    b = ["xxx", 10**10, 1.5, "é"]
    assert digest(a) == digest(b)
    nan = float("nan")
    # One NaN object: equal both ways. (Two NaN objects are unequal trees and
    # equal digests, as with the BLAKE2b digest: at most an extra sync.)
    assert digest([nan]) == digest([nan]) and tree([nan]) == tree([nan])


# --- per-row digests -------------------------------------------------------------------


def test_row_digests_name_exactly_the_changed_rows():
    rows = [{"id": i, "qty": i % 7, "tags": ["a", "t%d" % i]} for i in range(100)]
    before = native_digest(rows, rows=True)
    rows[17]["qty"] += 1
    rows[63]["tags"].append("x")
    after = native_digest(rows, rows=True)
    assert before is not None and after is not None
    assert before[0] != after[0]
    assert _rust.digest_rows_changed(before[1], after[1]) == [17, 63]
    rows[17]["qty"] -= 1
    rows[63]["tags"].pop()
    again = native_digest(rows, rows=True)
    assert again == before
    assert _rust.digest_rows_changed(before[1], before[1]) == []
    assert _rust.digest_rows_changed(before[1], before[1][:-16]) is None


def test_a_lists_digest_does_not_depend_on_asking_for_rows():
    rows = [{"a": i} for i in range(20)]
    with_rows, without = native_digest(rows, rows=True), native_digest(rows)
    assert with_rows is not None and without is not None
    assert with_rows[0] == without[0] and without[1] is None
    assert native_digest({"a": 1}, rows=True)[1] is None


# --- the kind invariant ----------------------------------------------------------------

VALUES = [
    [{"a": 1}],
    {"k": [1, 2]},
    (1, "x"),
    [Str("x")],
    [{1, 2}],
    {"m": Marked()},
    list(range(DEFAULT_BUDGET + 5)),
]


@pytest.mark.parametrize("value", VALUES, ids=[str(i) for i in range(len(VALUES))])
def test_every_producer_gives_one_value_one_kind(value):
    """``content_digest``, ``deep_digest`` and a snapshot entry agree, and the
    kind is native exactly when ``native_digest`` accepts the value."""
    from djust.websocket import _snapshot_assigns

    native = native_digest(value)
    first, truncated = content_digest(value)
    if truncated:
        assert native is None
        return
    assert deep_digest(value)[0] == first
    if native is not None:
        assert first == native[0]
    else:
        assert first == retained_fingerprint(tree(value))

    class View:
        pass

    view = View()
    view.items = value
    snapshot = _snapshot_assigns(view)
    entry = snapshot.fingerprints.get(id(value))
    assert entry is not None and entry[0] is value
    assert entry_retained(entry) == first
    assert (entry[3] is not None) == (native is not None)
    assert entry[2] == is_plain(value)


def test_the_switch_off_gives_the_tree_digest(monkeypatch):
    value = [{"a": 1}]
    on = content_digest(value)[0]
    for name in ("native_fingerprints", "digest_fingerprints"):
        monkeypatch.setattr(_per_turn, "disabled", frozenset({name}))
        assert native_digest(value) is None
    monkeypatch.setattr(_per_turn, "disabled", frozenset({"native_fingerprints"}))
    off = content_digest(value)[0]
    assert off == retained_fingerprint(tree(value)) and off != on


def test_without_the_extension_function_python_walks(monkeypatch):
    monkeypatch.setattr(change_detection, "_plain_digest", None)
    value = [{"a": 1}]
    assert native_digest(value) is None
    assert content_digest(value)[0] == retained_fingerprint(tree(value))


# --- mutate before and after the snapshot ---------------------------------------------


class _View:
    def __init__(self) -> None:
        self.title = "t"
        self.rows = [{"id": i, "qty": i, "on": True} for i in range(40)]


def _changed(view: Any, change: Any) -> set:
    from djust.websocket import _compute_changed_keys, _snapshot_assigns

    pre = _snapshot_assigns(view)
    change(view)
    post = _snapshot_assigns(view)
    return set(_compute_changed_keys(pre, post))


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.rows[3].__setitem__("qty", 99),
        lambda v: v.rows[3].__setitem__("on", 1),
        lambda v: v.rows[39].__setitem__("qty", 39.0),
        lambda v: v.rows.append({}),
        lambda v: v.rows[0].update(extra=None),
        lambda v: v.rows.__setitem__(slice(0, 2), [v.rows[1], v.rows[0]]),
    ],
    ids=["value", "true_to_1", "int_to_float", "append", "new_key", "swap"],
)
def test_an_in_place_change_by_the_handler_is_seen(change):
    assert _changed(_View(), change) == {"rows"}


def test_no_change_is_no_change():
    assert _changed(_View(), lambda v: None) == set()
    assert _changed(_View(), lambda v: v.rows[3].__setitem__("qty", 3)) == set()


def test_a_change_after_the_post_snapshot_is_not_in_its_digest():
    """What the window check (``_snapshot_walks_for_sync``) exists for: the
    post snapshot's digest is of the list as it was, so a later write must
    make the sync walk again (``big_late_writer`` in the oracle)."""
    from djust.websocket import _snapshot_assigns

    view = _View()
    post = _snapshot_assigns(view)
    entry = post.fingerprints[id(view.rows)]
    view.rows[5]["qty"] = -1
    assert entry_retained(entry) != content_digest(view.rows)[0]
    _, rows_now = native_digest(view.rows, rows=True)
    assert _rust.digest_rows_changed(entry[4], rows_now) == [5]
