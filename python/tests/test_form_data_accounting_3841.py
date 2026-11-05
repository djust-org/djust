"""The form-data bounds weigh what an event adds, not everything the form holds, and say exactly what
they weigh (#3841, follow-up to #3834 / #3837).

Three things the independent review of #3837 left open, each pinned here by a count or an exact
number, never a duration:

* ``_weigh`` over the stored data was a Python-level step per member, so an event with the form filled
  to the cap cost 174-279 ms. It now classifies a big container a chunk at a time in C and builds no
  merged copy. The speed is asserted as a COUNT of interpreter-level type checks (the module's own
  ``isinstance`` is swapped for a counting one), not as a time, and the answer is asserted to be the
  answer the old member-by-member walk gave, on generated values and generated event sequences.
* the admin form view's declared-field exemption had no test (mutation: ``_declared_field_names``
  returning nothing survived all 112 tests).
* the accounting is exact: no character for the mapping itself, a size equal to the limit is within it.

``reference_weigh`` below is the PRE-#3841 walker, member by member, with ONE deliberate change: an
empty string / ``bytes`` / ``bytearray`` weighs 1, not 0 (the review of #3842 found that a list of
twenty million empty strings cost one character, so 2,250,000 of them were stored through the real
socket at the default limits). The old walker is therefore no longer the oracle for zero-weight
leaves; this one is, and the zero-weight rule has its own tests (``TestNoMemberIsFree``).

Refs #3841, #3834, #3837, #1468, #1646.
"""

from __future__ import annotations

import builtins
import collections
import collections.abc
import itertools
import random
import types
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest
from django import forms

from djust import _form_data_bounds as bounds

pytestmark = [pytest.mark.pr_tier]


# ---------------------------------------------------------------------------------------------
# The reference: the walker as it was before #3841, member by member (except that an empty
# string / bytes weighs 1), and the verdict computed the slow obvious way (build the merged mapping,
# weigh all of it).
# ---------------------------------------------------------------------------------------------
_END = object()


def reference_weigh(value: Any, budget: int) -> int:
    from collections.abc import Mapping

    total = 0
    stack = [iter((value,))]
    while stack:
        item = next(stack[-1], _END)
        if item is _END:
            stack.pop()
            continue
        if isinstance(item, (str, bytes, bytearray)):
            total += len(item) or 1
        elif isinstance(item, Mapping):
            total += 1
            stack.append(itertools.chain.from_iterable(item.items()))
        elif isinstance(item, (list, tuple, set, frozenset)):
            total += 1
            stack.append(iter(item))
        else:
            total += 8
        if total > budget or len(stack) > bounds._MAX_WEIGH_NESTING:
            return max(total, budget + 1)
    return total


def reference_members(mapping: Dict[Any, Any], budget: int) -> int:
    """The exact characters of a mapping's keys and values: the old walker, less its one for the mapping."""
    weight = reference_weigh(mapping, budget + 1)
    return weight - 1 if weight <= budget + 1 else budget + 1


def reference_verdict(current, incoming, declared, max_fields, max_size) -> Optional[str]:
    merged = {**(current or {}), **incoming}
    if max_fields and sum(1 for key in merged if key not in declared) > max_fields:
        return "fields"
    if max_size and reference_members(merged, max_size) > max_size:
        return "size"
    return None


def verdict_of(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    return "fields" if "more fields" in message else "size"


@pytest.fixture
def limits(monkeypatch):
    """Set the two limits without touching settings. The member limit (#3844) is switched off: these
    tests are about characters and fields, and several store more than 100,000 members on purpose;
    ``test_form_data_members_3844.py`` owns the member limit."""

    def set_limits(fields: int, size: int) -> None:
        monkeypatch.setattr(bounds, "limits", lambda: (fields, size))
        monkeypatch.setattr(bounds, "member_limit", lambda: 0)

    return set_limits


# ---------------------------------------------------------------------------------------------
# Generated values: every shape the weigher meets, including the ones that take a different branch
# of the chunked path (a container of at least _VECTOR_MIN members; more than one _CHUNK of them;
# mixed leaves; leaves that are not exact types).
# ---------------------------------------------------------------------------------------------
class Shouting(str):
    """A str subclass: not an exact leaf, so it is walked member by member."""


class Flag(int):
    pass


def leaf(rng: random.Random) -> Any:
    kind = rng.randrange(14)
    if kind == 0:
        return ""
    if kind == 1:
        return "x" * rng.randrange(1, 30)
    if kind == 2:
        return rng.randrange(-5, 10**6)
    if kind == 3:
        return rng.random()
    if kind == 4:
        return rng.random() < 0.5
    if kind == 5:
        return None
    if kind == 6:
        return b"b" * rng.randrange(0, 20)
    if kind == 7:
        return bytearray(rng.randrange(0, 20))
    if kind == 8:
        return Shouting("s" * rng.randrange(0, 10))
    if kind == 9:
        return Flag(3)
    if kind == 10:
        return Decimal("1.5")
    if kind == 11:
        return memoryview(b"abc")
    if kind == 12:
        return {1: 2}.keys()
    return object()


def container(rng: random.Random, depth: int, size: int) -> Any:
    members = [value(rng, depth - 1) for _ in range(size)]
    kind = rng.randrange(5)
    if kind == 0:
        return members
    if kind == 1:
        return tuple(members)
    if kind == 2:
        return {("k%d" % i): m for i, m in enumerate(members)}
    hashable = [m for m in members if isinstance(m, (str, int, float, bytes, type(None)))]
    return frozenset(hashable) if kind == 3 else set(hashable)


def value(rng: random.Random, depth: int = 3, chunks: bool = True) -> Any:
    """A generated value. ``chunks=False`` keeps containers under one ``_CHUNK`` (cheap enough for a
    session of dozens of events); the weigher tests above it use the full range."""
    if depth <= 0 or rng.random() < 0.45:
        return leaf(rng)
    roll = rng.random()
    if roll < 0.55:
        size = rng.randrange(0, 6)
    elif roll < 0.8 or not chunks:
        size = rng.randrange(60, 70)  # either side of _VECTOR_MIN
    elif roll < 0.95:
        size = rng.randrange(4090, 4100)  # either side of _CHUNK
    else:
        size = rng.randrange(8190, 8200)
    if size >= 60:
        depth = 1  # a big container of small things, not of big containers
    return container(rng, depth, size)


def uniform_list(rng: random.Random, kind: str, size: int) -> List[Any]:
    make = {
        "str": lambda: "x" * rng.randrange(0, 4),
        "int": lambda: rng.randrange(100),
        "mixed": lambda: rng.choice(["ab", 7, None, True, 2.5, b"xyz"]),
    }[kind]
    return [make() for _ in range(size)]


class TestTheWeigherAgreesWithTheOldOne:
    @pytest.mark.parametrize("seed", range(40))
    def test_generated_values(self, seed):
        rng = random.Random(seed)
        for _ in range(12):
            payload = value(rng)
            for budget in (0, 7, 100, 5000, 10**7):
                got = bounds._weigh(payload, budget)
                want = reference_weigh(payload, budget)
                # Past the budget only "past" is promised; within it the number is exact.
                assert (got > budget) == (want > budget), (payload, budget, got, want)
                if want <= budget:
                    assert got == want, (payload, budget, got, want)

    @pytest.mark.parametrize("kind", ["str", "int", "mixed"])
    @pytest.mark.parametrize("size", [0, 1, 63, 64, 65, 4095, 4096, 4097, 8192, 9000])
    def test_flat_lists_at_every_chunk_boundary(self, kind, size):
        rng = random.Random(size)
        payload = {"k": uniform_list(rng, kind, size), "j": tuple(uniform_list(rng, kind, size))}
        assert bounds._weigh(payload, 10**9) == reference_weigh(payload, 10**9)
        assert bounds._weigh_members(payload, 10**9) == reference_members(payload, 10**9)

    @pytest.mark.parametrize("size", [63, 64, 4096, 4097, 9000])
    @pytest.mark.parametrize("mapping_type", [dict, collections.OrderedDict, "multivalue", "proxy"])
    def test_a_big_mapping_at_every_chunk_boundary(self, size, mapping_type):
        """Every kind of mapping: a plain ``dict`` takes the keys-then-values path, any other the
        (key, value) pairs one."""
        from django.utils.datastructures import MultiValueDict

        rng = random.Random(size)
        for kind in ("str", "int", "mixed"):
            items = {"k%d" % i: v for i, v in enumerate(uniform_list(rng, kind, size))}
            if mapping_type == "multivalue":
                payload: Any = MultiValueDict({k: [v] for k, v in items.items()})
            elif mapping_type == "proxy":
                payload = types.MappingProxyType(items)
            else:
                payload = mapping_type(items)
            reference = reference_members(dict(payload.items()), 10**9)
            assert bounds._weigh_members(payload, 10**9) == reference
            assert bounds._weigh(payload, 10**9) == reference + 1
            for budget in (reference - 1, reference):
                assert (bounds._weigh_members(payload, budget) > budget) == (reference > budget)

    @pytest.mark.parametrize("budget", [0, 1, 99, 100, 101, 4096, 10**5])
    def test_the_budget_decides_exactly_where_the_total_crosses_it(self, budget):
        payload = {"k%d" % i: "xy" for i in range(3000)}  # 3000 * (2 + 2) = 12,000 + 1
        full = reference_weigh(payload, 10**9)
        assert (bounds._weigh(payload, budget) > budget) == (full > budget)

    @pytest.mark.parametrize(
        "depth", [1, 2, 62, 63, 64, 65, 500, 995, 996, 997, 998, 999, 1000, 1001, 1002, 5000]
    )
    @pytest.mark.parametrize("kind", ["list", "mapping"])
    @pytest.mark.parametrize("fill", [1, 70])  # one member per level, and a big container per level
    def test_nesting_is_refused_at_the_same_depth(self, depth, kind, fill):
        deep: Any = "x"
        for _ in range(depth):
            if kind == "list":
                deep = [deep] + ["y"] * (fill - 1)
            else:
                deep = {"d": deep, **{"f%d" % i: "y" for i in range(fill - 1)}}
        budget = 10**9
        want = reference_weigh(deep, budget)
        got = bounds._weigh(deep, budget)
        assert (got > budget) == (want > budget), (depth, kind, fill)
        if want <= budget:
            assert got == want
        # The same value as a member of the data a form holds: the mapping is one more level.
        want_in = reference_members({"k": deep}, budget)
        got_in = bounds._weigh_members({"k": deep}, budget)
        assert (got_in > budget) == (want_in > budget), (depth, kind, fill)
        if want_in <= budget:
            assert got_in == want_in

    def test_a_cycle_through_a_big_container_runs_out_of_budget(self):
        cycle: List[Any] = ["x"] * 100
        cycle.append(cycle)
        assert bounds._weigh(cycle, 5000) > 5000
        mapping: Dict[str, Any] = {"k%d" % i: "v" for i in range(100)}
        mapping["self"] = mapping
        assert bounds._weigh_members(mapping, 5000) > 5000


# ---------------------------------------------------------------------------------------------
# The verdict: ``refusal`` against "merge, then weigh all of it", after every step of a generated
# sequence of the events a form really gets.
# ---------------------------------------------------------------------------------------------
class TestTheVerdictEqualsTheFullRecount:
    EVENTS = 40

    def _key(self, rng: random.Random, existing) -> str:
        if existing and rng.random() < 0.4:
            return rng.choice(list(existing))
        return "f%d" % rng.randrange(400)

    def _incoming(self, rng: random.Random, current) -> Dict[str, Any]:
        count = rng.choice([1, 1, 1, 2, 5, 12])
        return {self._key(rng, current): value(rng, 2, chunks=False) for _ in range(count)}

    @pytest.mark.parametrize("seed", range(40))
    def test_every_step_of_a_generated_session(self, seed, limits):
        rng = random.Random(seed)
        declared = {"name", "email"}
        initial = {"name": "", "email": ""}
        current: Dict[str, Any] = dict(initial)
        steps = {"accepted": 0, "refused": 0}
        max_fields = rng.choice([3, 10, 40])
        max_size = rng.choice([40, 300, 3000, 40_000])
        for step in range(self.EVENTS):
            roll = rng.random()
            if roll < 0.08:  # reset_form
                current = dict(initial)
                continue
            if roll < 0.12:  # a wizard step change: another mapping, other declared fields
                current, declared = {"a": "", "b": ""}, {"a", "b"}
                continue
            if roll < 0.2 and current:  # other code edits the stored mapping in place
                edit = rng.randrange(4)
                key = rng.choice(list(current))
                if edit == 0:
                    current[key] = value(rng, 2, chunks=False)
                elif edit == 1 and key not in declared:
                    del current[key]
                elif edit == 2:
                    current["z%d" % rng.randrange(5)] = "w" * rng.randrange(50)
                elif isinstance(current[key], list):
                    current[key].append("appended" * 5)
                continue
            if roll < 0.24:  # the limit is lowered (or raised) under data already stored
                max_size = rng.choice([40, 300, 3000, 40_000])
            incoming = self._incoming(rng, current)
            limits(max_fields, max_size)
            got = verdict_of(bounds.refusal(current, incoming, declared))
            want = reference_verdict(current, incoming, declared, max_fields, max_size)
            assert got == want, (seed, step, got, want, current, incoming)
            if got is None:
                current.update(incoming)
                steps["accepted"] += 1
            else:
                steps["refused"] += 1
        # The session must have exercised both outcomes, or it proves nothing about either.
        assert steps["accepted"] and steps["refused"], steps

    def test_both_outcomes_and_both_limits_are_reached(self, limits):
        """The sessions above would pass vacuously if every event was refused for one reason."""
        seen = set()
        for seed in range(20):
            rng = random.Random(seed)
            limits(rng.choice([3, 10]), rng.choice([40, 300]))
            current = {"name": "", "email": ""}
            for _ in range(20):
                incoming = {
                    "f%d" % rng.randrange(400): value(rng, 2, chunks=False)
                    for _ in range(rng.choice([1, 5, 12]))
                }
                got = verdict_of(bounds.refusal(current, incoming, {"name", "email"}))
                seen.add(got)
                if got is None:
                    current.update(incoming)
        assert seen == {None, "fields", "size"}

    @pytest.mark.parametrize("shape", ["str", "int", "mixed"])
    def test_a_form_filled_to_the_cap_over_a_big_stored_mapping(self, shape, limits):
        rng = random.Random(1)
        current = {"k%d" % i: v for i, v in enumerate(uniform_list(rng, shape, 5000))}
        size = reference_members(current, 10**9)
        for max_size in (size - 1, size, size + 1, size + 10, size * 2):
            limits(0, max_size)
            for incoming in (
                {"new": "ab"},
                {"k7": "q" * 50},
                {"k7": ""},
                {"k%d" % i: "z" for i in range(0, 5000, 7)},
            ):
                got = verdict_of(bounds.refusal(current, incoming, ()))
                assert got == reference_verdict(current, incoming, (), 0, max_size), (
                    max_size,
                    incoming,
                )

    def test_replacing_what_is_already_past_the_limit_with_less_is_accepted(self, limits):
        # Data stored under a larger limit (or by the project's own code) must still be replaceable.
        limits(0, 50)
        current = {"k": "x" * 100, "j": "y"}
        assert bounds.refusal(current, {"k": "z"}, ()) is None  # 1+1 + 1+1
        assert bounds.refusal(current, {"j": "z"}, ()) is not None  # k stays: 101 > 50
        assert verdict_of(bounds.refusal(current, {"k": "z" * 49}, ())) == reference_verdict(
            current, {"k": "z" * 49}, (), 0, 50
        )

    def test_a_replaced_entry_nested_to_the_stack_bound_is_replaceable(self, limits):
        deep: Any = "x"
        for _ in range(bounds._MAX_WEIGH_NESTING + 5):
            deep = [deep]
        limits(0, 100)
        current = {"deep": deep, "ok": "v"}
        # The entry cannot be weighed (nested past the bound), and the merged mapping does not hold it.
        assert bounds.refusal(current, {"deep": "x"}, ()) is None
        assert bounds.refusal(current, {"ok": "w"}, ()) is not None
        assert reference_verdict(current, {"deep": "x"}, (), 0, 100) is None

    @pytest.mark.parametrize("depth", range(994, 1003))
    def test_a_replaced_entry_at_the_exact_nesting_bound_is_judged_as_the_full_recount_does(
        self, depth, limits
    ):
        """An entry is weighed as a member of the data (one level deeper than alone): at the depth where
        it is refused as a member but not alone, replacing it must still be judged by the merged data,
        which no longer holds it."""
        deep: Any = "x"
        for _ in range(depth):
            deep = [deep]
        limits(0, 5000)
        current = {"deep": deep, "ok": "v"}
        for incoming in ({"deep": "x"}, {"ok": "w"}, {"deep": "x", "ok": "w"}, {"new": "z"}):
            assert verdict_of(bounds.refusal(current, incoming, ())) == reference_verdict(
                current, incoming, (), 0, 5000
            ), (depth, incoming)

    def test_a_stored_mapping_that_is_not_a_dict_is_judged_the_same(self, limits):
        from django.http import QueryDict

        limits(5, 40)
        current = QueryDict("a=1&a=2&b=three", mutable=True)
        for incoming in ({"c": "x"}, {"b": "y" * 60}, {"a": "z"}):
            assert verdict_of(bounds.refusal(current, incoming, ())) == reference_verdict(
                dict(current.items()), incoming, (), 5, 40
            )


# ---------------------------------------------------------------------------------------------
# The accounting is exact (#3841 item 3), and what it does not count is said and pinned.
# ---------------------------------------------------------------------------------------------
class TestTheAccountingIsExact:
    def test_the_mapping_itself_costs_nothing(self):
        assert bounds._weigh_members({}, 100) == 0
        assert bounds._weigh_members({"ab": "cd"}, 100) == 4
        assert bounds._weigh({"ab": "cd"}, 100) == 5  # a CONTAINER a client sent is one character

    def test_a_size_equal_to_the_limit_is_within_it(self, limits):
        limits(0, 4)
        assert bounds.refusal({}, {"ab": "cd"}, ()) is None  # exactly 4 characters, nothing kept
        assert bounds.refusal({"ab": "c"}, {"ab": "cd"}, ()) is None  # a replacement, 4 again
        assert bounds.refusal({"ab": "cd"}, {"e": "f"}, ()) is not None  # 4 + 2
        limits(0, 3)
        assert bounds.refusal({}, {"ab": "cd"}, ()) is not None  # 4 > 3

    def test_two_events_cost_the_sum_of_their_characters_not_two_more(self, limits):
        limits(0, 8)
        assert bounds.refusal({"ab": "cd"}, {"ef": "gh"}, ()) is None  # 4 + 4
        limits(0, 7)
        assert bounds.refusal({"ab": "cd"}, {"ef": "gh"}, ()) is not None

    def test_the_exact_limit_with_an_empty_kept_mapping_over_a_real_form(self, limits):
        # Every kept key is sent again: nothing survives, and the incoming size alone is judged.
        limits(0, 6)
        assert (
            bounds.refusal({"a": "xxxxxxxxxx", "b": "yyyyyyyyyy"}, {"a": "11", "b": "22"}, ())
            is None
        )

    @pytest.mark.parametrize(
        "odd",
        [
            memoryview(b"abcdefghij"),
            {"a": 1}.keys(),
            {"a": 1}.values(),
            {"a": 1}.items(),
            Decimal("1"),
            object(),
        ],
    )
    def test_what_json_cannot_produce_costs_a_constant_not_its_length(self, odd):
        """A ``memoryview`` or a dict view is not something a JSON frame can carry, so it can only be
        put there by the project's own code. It is charged 8 like any other object the weigher does
        not look into; this is the documented behaviour, pinned so a change to it is a decision."""
        assert bounds._weigh_members({"k": odd}, 100) == 1 + 8
        assert bounds._weigh_members({"k": [odd, odd]}, 100) == 1 + 1 + 16

    def test_keys_that_are_not_strings_cost_their_own_weight(self):
        assert bounds._weigh_members({1: "ab", b"xyz": None}, 100) == 8 + 2 + 3 + 8


# ---------------------------------------------------------------------------------------------
# The cost (#3841 item 1), as a count of interpreter-level steps. The module's own ``isinstance`` is
# replaced by one that counts, so the number is deterministic and has nothing to do with the clock.
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def isinstance_calls(monkeypatch):
    calls = {"n": 0}

    def counting(obj, classes):
        calls["n"] += 1
        return builtins.isinstance(obj, classes)

    monkeypatch.setattr(bounds, "isinstance", counting, raising=False)
    return calls


class TestAnEventWeighsInBulk:
    N = 60_000

    def _stored(self, shape: str) -> Dict[str, Any]:
        if shape == "one big list":
            return {"a": ["x"] * self.N}
        if shape == "one big list of ints":
            return {"a": list(range(self.N))}
        if shape == "many fields, strings":
            return {"k%d" % i: "v" for i in range(self.N)}
        if shape == "many fields, mixed":
            return {"k%d" % i: ("v" if i % 3 else 7) for i in range(self.N)}
        raise AssertionError(shape)

    SHAPES = ["one big list", "one big list of ints", "many fields, strings", "many fields, mixed"]

    @pytest.mark.parametrize("shape", SHAPES)
    def test_an_event_makes_a_constant_number_of_type_checks_however_much_is_stored(
        self, shape, limits, isinstance_calls
    ):
        stored = self._stored(shape)
        limits(0, 10**9)
        isinstance_calls["n"] = 0
        assert bounds.refusal(stored, {"k5": "replaced", "new": "field"}, ()) is None
        checks = isinstance_calls["n"]
        # Per chunk of 4096 and a few for the event itself; the old walk made one per member (60,000+).
        assert checks < 200, (shape, checks)

    @pytest.mark.parametrize("shape", SHAPES)
    def test_the_work_does_not_grow_with_what_is_stored(self, shape, limits, isinstance_calls):
        """4x the stored members: the type checks do not grow by anything like 4x."""
        limits(0, 10**9)
        costs = []
        for scale in (1, 4):
            stored = {
                key: (value * scale if isinstance(value, list) else value)
                for key, value in self._stored(shape).items()
            }
            if shape.startswith("many"):
                stored = {
                    "k%d" % i: stored.get("k%d" % (i % self.N), "v") for i in range(self.N * scale)
                }
            isinstance_calls["n"] = 0
            bounds.refusal(stored, {"new": "field"}, ())
            costs.append(isinstance_calls["n"])
        assert costs[1] < costs[0] + 100, costs

    @pytest.mark.parametrize("shape", SHAPES)
    def test_the_gate_off_walks_member_by_member(
        self, shape, limits, isinstance_calls, monkeypatch
    ):
        """The same event with the chunked path switched off IS the O(stored) walk: the assertion above
        can fail, so it proves something."""
        monkeypatch.setattr(bounds, "_VECTOR_MIN", 10**12)
        stored = self._stored(shape)
        limits(0, 10**9)
        isinstance_calls["n"] = 0
        assert bounds.refusal(stored, {"new": "field"}, ()) is None
        assert isinstance_calls["n"] >= self.N

    @pytest.mark.parametrize("big", ["list", "mapping", "non-dict mapping"])
    def test_the_work_is_still_bounded_by_the_budget(self, big):
        """A container with a million members is weighed to the budget and no further: the members
        pulled are counted (a chunk or two), not timed."""
        pulled = []

        def members():
            for i in range(10**6):
                pulled.append(i)
                yield "ab"

        class EndlessList(list):
            def __len__(self):
                return 10**6

            def __iter__(self):
                return members()

        class EndlessMapping(dict):
            def __len__(self):
                return 10**6

            def keys(self):
                return members()

            def values(self):
                return members()

            def items(self):
                return ((m, m) for m in members())

        class EndlessProxy(collections.abc.Mapping):
            def __len__(self):
                return 10**6

            def __iter__(self):
                return members()

            def __getitem__(self, key):
                return key

            def items(self):
                return ((m, m) for m in members())

        stored = {
            "list": {"a": EndlessList()},
            "mapping": {"a": EndlessMapping()},
            "non-dict mapping": {"a": EndlessProxy()},
        }[big]
        assert bounds._weigh_members(stored, 1000) > 1000
        # An absolute ceiling, not a multiple of _CHUNK: with the chunk set to 10**9 a multiple of it
        # admits the whole million (mutation J4 survived 436 tests while this said ``2 * _CHUNK``).
        assert len(pulled) <= 16_384, len(pulled)
        assert bounds._CHUNK <= 8192

    def test_no_merged_copy_is_made_and_the_stored_keys_are_not_iterated_one_by_one(self, limits):
        class Counting(dict):
            iterated = 0

            def __iter__(self):
                type(self).iterated += 1
                return super().__iter__()

        stored = Counting({"k%d" % i: "v" for i in range(5000)})
        limits(10**9, 10**9)
        Counting.iterated = 0
        assert bounds.refusal(stored, {"new": "x"}, {"k1", "k2"}) is None
        # The field count asks about the declared names, not about every stored key.
        assert Counting.iterated == 0


# ---------------------------------------------------------------------------------------------
# The admin form view's declared-field exemption (#3841 item 2). Mutation H15 (``_declared_field_names``
# returning nothing there) survived every test in #3837.
# ---------------------------------------------------------------------------------------------
Wide = type(
    "Wide", (forms.Form,), {"f%d" % i: forms.CharField(required=False) for i in range(1200)}
)


class _FormAdmin:
    """The part of a ``ModelAdmin`` the admin form view uses: a form class, built for an object."""

    def get_form(self, request, obj=None):
        class WideAdminForm(Wide):
            def __init__(self, data=None, instance=None, **kwargs):
                super().__init__(data=data, **kwargs)

        return WideAdminForm


def _admin_view():
    from djust.admin_ext.forms import AdminFormMixin

    class WideAdminView(AdminFormMixin):
        _model = None
        _model_admin = _FormAdmin()

    view = WideAdminView()
    view.mount(request=None)
    return view


class TestTheAdminFormViewsDeclaredFieldsAreNotTheClientsToAdd:
    def test_the_mounted_form_declares_more_fields_than_the_limit(self):
        view = _admin_view()
        assert len(view.form_instance.fields) == 1200
        assert bounds.limits()[0] == 1000
        assert view._declared_field_names() == frozenset(view.form_instance.fields)

    def test_submit_form_takes_every_declared_field_at_the_default_limit(self):
        view = _admin_view()
        view.submit_form(**{"f%d" % i: "v" for i in range(1200)})
        assert getattr(view, "form_errors", {}) in ({}, [])
        assert len(view.form_data) == 1200
        assert view.form_data["f1199"] == "v"

    def test_validate_field_works_on_a_declared_field_of_a_full_form(self):
        view = _admin_view()
        view.submit_form(**{"f%d" % i: "v" for i in range(1200)})
        for name in ("f0", "f600", "f1199"):
            view.validate_field(field=name, value="w")
            assert view.form_data[name] == "w"
        assert len(view.form_data) == 1200
        assert not any("more fields" in str(e) for e in getattr(view, "form_errors", []) or [])

    def test_what_the_client_adds_beyond_the_declared_fields_is_still_limited(self):
        view = _admin_view()
        view.submit_form(
            **{"f%d" % i: "v" for i in range(1200)}, **{"x%d" % i: "v" for i in range(1000)}
        )
        assert "x999" in view.form_data
        view.submit_form(x1000="v")
        assert "x1000" not in view.form_data
        assert "more fields" in view.form_errors[0]

    def test_the_gate_off_a_view_that_declares_nothing_refuses_the_same_submit(self):
        """The mutation, written out: with ``_declared_field_names`` empty the 1,200 fields count
        as the client's and the submit is refused, so the tests above fail on it."""
        view = _admin_view()
        view._declared_field_names = lambda: frozenset()
        view.submit_form(**{"f%d" % i: "v" for i in range(1200)})
        assert view.form_data == {}
        assert "more fields" in view.form_errors[0]


# ---------------------------------------------------------------------------------------------
# No member is free (#3842 review, item 1) and no flat list of containers is pulled whole (item 2).
# ---------------------------------------------------------------------------------------------
class TestNoMemberIsFree:
    def test_an_empty_string_bytes_or_bytearray_costs_one_character(self):
        for empty in ("", b"", bytearray(), Shouting("")):
            assert bounds._weigh(empty, 100) == 1, repr(empty)
            assert bounds._weigh_members({"k": empty}, 100) == 1 + 1, repr(empty)
        assert bounds._weigh_members({"": "v"}, 100) == 1 + 1  # an empty KEY too

    @pytest.mark.parametrize("size", [1, 63, 64, 4095, 4096, 4097, 20_000])
    @pytest.mark.parametrize("shape", ["str", "bytes", "mixed with ints", "mixed with strs"])
    def test_a_list_of_empties_costs_one_each_in_the_bulk_path_too(self, size, shape):
        member = {"str": "", "bytes": b"", "mixed with ints": None, "mixed with strs": "ab"}[shape]
        payload = [member if i % 2 == 0 or shape in ("str", "bytes") else "" for i in range(size)]
        want = reference_weigh(payload, 10**9)
        assert bounds._weigh(payload, 10**9) == want
        assert want >= size  # the reference itself charges every member

    def test_every_leaf_and_empty_container_costs_at_least_one(self):
        for leaf_value in (0, 0.0, False, None, "", b"", (), [], {}, set(), frozenset()):
            assert bounds._weigh([leaf_value] * 100, 10**9) >= 101, repr(leaf_value)
            assert bounds._weigh_members({"k": [leaf_value] * 100}, 10**9) >= 102, repr(leaf_value)

    def test_the_bound_holds_for_a_payload_of_empties(self, limits):
        limits(0, 10_000)
        assert bounds.refusal({}, {"k": [""] * 9_000}, ()) is None  # 1 + 1 + 9,000
        assert bounds.refusal({}, {"k": [""] * 20_000}, ()) is not None
        # and across events: the second does not fit beside the first
        assert bounds.refusal({"a": [""] * 6_000}, {"b": [""] * 6_000}, ()) is not None

    def test_members_stored_through_form_mixin_are_bounded(self, limits):
        from test_form_data_bounds_3834 import WsContact, mounted

        limits(0, 100_000)
        view = mounted(WsContact)
        for i in range(60):
            view.validate_field(field="f%d" % i, value=[""] * 3_000)
        stored = sum(len(v) for v in view.form_data.values() if isinstance(v, list))
        assert 0 < stored <= 100_000, stored
        assert "larger than the server accepts" in view.form_errors[0]

    def test_a_chunk_of_empty_values_is_charged_in_bulk_and_member_by_member_alike(self):
        payload = {"k%d" % i: "" for i in range(5_000)}
        got = bounds._weigh_members(payload, 10**9)
        assert got == reference_members(payload, 10**9)
        assert got == sum(len(k) for k in payload) + 5_000  # each empty value is one character


class TestAFlatListOfContainersIsNotPulledWhole:
    """``[[]] * 20_000_000`` was pulled in full before it was charged, because its members went to
    ``rest`` at weight 0 and cost only when the walk reached them. Counted by what is pulled."""

    @pytest.mark.parametrize("member", [list, dict, tuple, set, lambda: [1], lambda: {"a": 1}])
    @pytest.mark.parametrize("holder", ["list", "dict values", "non-dict values", "dict keys"])
    def test_members_pulled_before_the_budget_is_known_to_be_passed(self, member, holder):
        pulled = []

        def members():
            for i in range(10**6):
                pulled.append(i)
                yield member() if holder != "dict keys" else (i,)

        class Endless(list):
            def __len__(self):
                return 10**6

            def __iter__(self):
                return members()

        class EndlessDict(dict):
            def __len__(self):
                return 10**6

            def values(self):
                return members()

            def items(self):
                # Keys that are containers too, so no leaf key spends the budget before the rest does.
                return (((), m) for m in members())

        class EndlessKeys(dict):
            def __len__(self):
                return 10**6

            def keys(self):
                return members()

            def items(self):
                return ((m, 1) for m in members())

        value = {
            "list": {"k": Endless()},
            "dict values": {"k": EndlessDict()},
            "non-dict values": {"k": EndlessDict()},
            "dict keys": {"k": EndlessKeys()},
        }[holder]
        assert bounds._weigh_members(value, 1_000) > 1_000
        assert len(pulled) <= 16_384, len(pulled)

    def test_a_flat_list_of_containers_under_the_budget_is_still_weighed_exactly(self):
        payload = {"k": [[] for _ in range(5_000)]}
        assert bounds._weigh_members(payload, 10**9) == reference_members(payload, 10**9)
        payload = {"k": [{"a": [1]} for _ in range(5_000)]}
        assert bounds._weigh_members(payload, 10**9) == reference_members(payload, 10**9)
