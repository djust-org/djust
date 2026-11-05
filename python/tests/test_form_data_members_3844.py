"""The form-data bounds count MEMBERS as well as characters (#3844, follow-up to #3842 / #3834).

Three things the independent review of #3842 left open, each pinned by a count or an exact number,
never a duration:

1. ``max_form_data_size`` bounds the stored CHARACTERS, not the time an event takes. ``[""] * 15000``
   is 15,000 characters, so 174 of them (2.61 million empty members) fit under the 2.6 million limit,
   and every later event handed all of them to the template engine: the review measured 0.03 s growing
   to 1.25 s (and ``[[]] * 7500`` to 3.9 s). Time cannot be asserted in the PR tier, so the reproduction
   counts the WORK: how many members the state sync hands to the extension (``update_state``), which is
   the O(stored) pass that costs about 0.7 microseconds a member. ``max_form_members`` bounds it.
2. The ``_charge_rest`` early stop was untested at an exact limit inside a bulk container with pending
   non-leaf members (mutation N08, ``>`` to ``>=``, survived): ``TestTheExactLimitInsideABulkContainer``.
3. The benchmark targets in ``tests/benchmarks/test_lazy_provenance_cost_3835.py`` (loosened separately).

``reference_count`` is an independent, member-by-member oracle for what a member is. Every limit
here has a test at the limit, one below and one above, and ``TestGateOff`` writes the enforcement
mutation out.

Refs #3844, #3842, #3834, #1468, #1646.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from django.test import override_settings

from djust import _form_data_bounds as bounds
from djust.config import DEFAULT_MAX_FORM_MEMBERS, config as djust_config

from test_form_data_accounting_3841 import Shouting, value as generated_value
from test_form_data_bounds_3834 import Wizard, WsContact, limits as livecfg, mounted

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]


def reference_count(item: Any) -> int:
    """What a member is, said the obvious way: every key, every value, and every container inside them
    is one; a mapping contributes its keys and its values; the top-level mapping itself is not one."""
    if isinstance(item, Mapping):
        return 1 + sum(reference_count(k) + reference_count(v) for k, v in item.items())
    if isinstance(item, (list, tuple, set, frozenset)):
        return 1 + sum(reference_count(m) for m in item)
    return 1


def reference_members(mapping: Mapping[Any, Any]) -> int:
    return reference_count(mapping) - 1


def count_of(mapping, budget=10**9):
    return bounds._weigh_members(mapping, budget, True)


@pytest.fixture
def members(monkeypatch):
    """Set the member limit, and switch the other two off, without touching settings."""

    def set_limit(n: int) -> None:
        monkeypatch.setattr(bounds, "limits", lambda: (0, 0))
        monkeypatch.setattr(bounds, "member_limit", lambda: n)

    return set_limit


# ---------------------------------------------------------------------------------------------
# What a member is.
# ---------------------------------------------------------------------------------------------
class TestWhatAMemberIs:
    @pytest.mark.parametrize(
        "mapping, expected",
        [
            ({}, 0),
            ({"k": "v"}, 2),  # a key and a value
            ({"k": ""}, 2),  # an empty string is a member, as it is a character
            ({"k": "x" * 5000}, 2),  # the length of a string is characters, not members
            ({"k": []}, 2),  # the key and the empty list
            ({"k": ["a", "b"]}, 4),  # key, list, two strings
            ({"k": {"a": 1}}, 4),  # key, dict, its key and its value
            ({"k": [[], [[]]]}, 5),  # key, list, [], [[]] and the [] inside it
            ({"k": (1, 2)}, 4),
            ({"k": {1, 2}}, 4),
            ({"k": frozenset({1})}, 3),
            ({"k": b"abc"}, 2),
            ({"k": object()}, 2),
            ({"a": 1, "b": 2}, 4),
        ],
    )
    def test_the_count(self, mapping, expected):
        assert count_of(mapping) == expected == reference_members(mapping)

    @pytest.mark.parametrize("seed", range(40))
    def test_generated_values_agree_with_the_reference(self, seed):
        rng = random.Random(seed)
        mapping = {"k%d" % i: generated_value(rng) for i in range(rng.randrange(1, 5))}
        assert count_of(mapping) == reference_members(mapping)

    @pytest.mark.parametrize("size", [0, 1, 63, 64, 65, 4095, 4096, 4097, 8192, 8193])
    @pytest.mark.parametrize("shape", ["strings", "ints", "mixed", "containers", "subclasses"])
    def test_a_flat_list_at_every_chunk_boundary(self, size, shape):
        make = {
            "strings": lambda i: "x" * (i % 3),
            "ints": lambda i: i,
            "mixed": lambda i: ("a", 1, None, 2.5, b"b")[i % 5],
            "containers": lambda i: [] if i % 2 else {"a": [i]},
            "subclasses": lambda i: Shouting("s") if i % 2 else [i],
        }[shape]
        mapping = {"k": [make(i) for i in range(size)]}
        assert count_of(mapping) == reference_members(mapping)

    @pytest.mark.parametrize("size", [1, 63, 64, 4096, 4097, 9000])
    @pytest.mark.parametrize("mapping_type", ["dict", "other mapping"])
    def test_a_big_mapping(self, size, mapping_type):
        class Other(Mapping):
            def __init__(self, data):
                self._d = data

            def __getitem__(self, key):
                return self._d[key]

            def __iter__(self):
                return iter(self._d)

            def __len__(self):
                return len(self._d)

        inner = {"k%d" % i: ("v" if i % 3 else [i]) for i in range(size)}
        mapping = {"m": inner if mapping_type == "dict" else Other(inner)}
        assert count_of(mapping) == reference_members(mapping)

    def test_the_stored_data_itself_is_not_a_member(self):
        assert count_of({}) == 0
        assert count_of({"a": 1}) == count_of({"a": 1}, 10**9) == 2


# ---------------------------------------------------------------------------------------------
# The exact limit: at it, one below, one above, for every shape of event.
# ---------------------------------------------------------------------------------------------
class TestTheLimitIsExact:
    @pytest.mark.parametrize("limit", [1, 2, 3, 10, 63, 64, 65, 200])
    def test_a_flat_mapping_of_leaves(self, limit, members):
        members(limit)
        # a key and a value are two members, so pairs fit while 2 * pairs <= limit
        pairs = limit // 2
        fits = {"k%d" % i: i for i in range(pairs)}
        assert count_of(fits) == 2 * pairs
        assert bounds.refusal({}, fits, ()) is None
        if limit % 2 == 0:
            # exactly the limit
            assert count_of(fits) == limit
            assert bounds.refusal({}, {**fits, "extra": 1}, ()) is not None  # one pair past it
        else:
            assert bounds.refusal({}, {**fits, "extra": 1}, ()) is not None  # limit + 1

    @pytest.mark.parametrize("limit", [4, 5, 63, 64, 65, 66, 67, 200, 4100, 4101, 9000])
    def test_a_list_of_exactly_limit_minus_two_members(self, limit, members):
        """A key and a list container are two members: the list holds ``limit - 2`` leaves."""
        members(limit)
        at = limit - 2
        assert bounds.refusal({}, {"k": [0] * at}, ()) is None  # exactly the limit
        assert count_of({"k": [0] * at}) == limit
        assert bounds.refusal({}, {"k": [0] * (at + 1)}, ()) is not None  # one above
        assert bounds.refusal({}, {"k": [0] * (at - 1)}, ()) is None  # one below

    @pytest.mark.parametrize("filler", ["", "x", "x" * 1000, 7, None])
    def test_a_member_costs_one_however_long_it_is(self, filler, members):
        members(12)
        assert bounds.refusal({}, {"k": [filler] * 10}, ()) is None  # 2 + 10
        assert bounds.refusal({}, {"k": [filler] * 11}, ()) is not None

    @pytest.mark.parametrize("limit", [10, 70, 5000])
    def test_across_events_the_stored_data_counts(self, limit, members):
        members(limit)
        stored = {"a": [0] * (limit // 2 - 2)}  # limit // 2 members
        incoming_fits = {"b": [0] * (limit - limit // 2 - 2)}
        assert bounds.refusal(stored, incoming_fits, ()) is None  # exactly the limit
        assert count_of({**stored, **incoming_fits}) == limit
        assert bounds.refusal(stored, {"b": [0] * (limit - limit // 2 - 1)}, ()) is not None
        assert bounds.refusal(stored, {"b": [0] * (limit - limit // 2 - 3)}, ()) is None

    @pytest.mark.parametrize("limit", [10, 70, 5000])
    def test_a_key_sent_again_replaces_and_is_not_counted_twice(self, limit, members):
        members(limit)
        stored = {"a": [0] * (limit - 2)}  # exactly the limit
        assert bounds.refusal(stored, {"a": [1] * (limit - 2)}, ()) is None
        assert bounds.refusal(stored, {"a": [1] * (limit - 1)}, ()) is not None
        assert bounds.refusal(stored, {"a": []}, ()) is None
        # ... and freeing what it replaced makes room for a second key
        assert bounds.refusal(stored, {"a": [], "b": [0] * (limit - 4)}, ()) is None
        assert bounds.refusal(stored, {"a": [], "b": [0] * (limit - 3)}, ()) is not None

    def test_what_is_already_past_the_limit_can_be_replaced_with_less(self, members):
        members(10)
        stored = {"a": [0] * 500}  # a limit lowered after it was stored
        assert bounds.refusal(stored, {"a": [0] * 8}, ()) is None  # 10
        assert bounds.refusal(stored, {"a": [0] * 9}, ()) is not None
        assert bounds.refusal(stored, {"b": 1}, ()) is not None  # the rest still does not fit

    def test_a_replaced_entry_already_past_the_limit_with_a_survivor(self, members):
        members(10)
        stored = {"big": [0] * 500, "keep": "v"}  # keep is two members
        assert bounds.refusal(stored, {"big": [0] * 6}, ()) is None  # 2 + 8 = 10
        assert bounds.refusal(stored, {"big": [0] * 7}, ()) is not None

    @pytest.mark.parametrize("depth", [1, 2, 10, 300])
    def test_nesting_costs_a_member_a_level(self, depth, members):
        value: Any = 0
        for _ in range(depth):
            value = [value]
        mapping = {"k": value}
        assert count_of(mapping) == 2 + depth == reference_members(mapping)
        members(2 + depth)
        assert bounds.refusal({}, mapping, ()) is None
        members(1 + depth)
        assert bounds.refusal({}, mapping, ()) is not None

    def test_zero_removes_it(self, members):
        members(0)
        assert bounds.refusal({}, {"k": [0] * 1_000_000}, ()) is None

    def test_the_message_names_the_limit_and_nothing_the_client_sent(self, members):
        members(5)
        message = bounds.refusal({}, {"secret-key": ["secret-value"] * 10}, ())
        assert "(5)" in message and "secret" not in message
        assert "more fields" not in message  # it is not the field-count message
        assert "larger than" not in message  # nor the size message


# ---------------------------------------------------------------------------------------------
# #3842 review item 2: the exact limit inside a bulk container with pending non-leaf members.
# Mutation N08 (`>` to `>=` in `_charge_rest`) survived every test of #3842: at a total exactly
# equal to the budget it charged the pending members now AND again when the walk reached them,
# refusing a value whose true weight equals the limit.
# ---------------------------------------------------------------------------------------------
class TestTheExactLimitInsideABulkContainer:
    @pytest.mark.parametrize("leaves, pending", [(64, 1), (64, 5), (100, 100), (4090, 20), (3, 70)])
    @pytest.mark.parametrize("pending_kind", ["list", "dict", "tuple"])
    def test_size_equal_to_the_limit_is_accepted(self, leaves, pending, pending_kind, monkeypatch):
        """``{"": ["a"] * leaves + [<empty container>] * pending}`` weighs 1 (the key, empty) + 1 (the list)
        + ``leaves`` + ``pending``: the walk has charged everything but the pending members when
        ``_charge_rest`` runs, and ``total + len(rest)`` is EXACTLY the limit there."""
        empty = {"list": list, "dict": dict, "tuple": tuple}[pending_kind]
        payload = {"": ["a"] * leaves + [empty() for _ in range(pending)]}
        weight = 2 + leaves + pending
        assert bounds._weigh_members(payload, 10**9) == weight == reference_size(payload)
        monkeypatch.setattr(bounds, "member_limit", lambda: 0)
        monkeypatch.setattr(bounds, "limits", lambda: (0, weight))
        assert bounds.refusal({}, payload, ()) is None  # equal to the limit: within it
        monkeypatch.setattr(bounds, "limits", lambda: (0, weight - 1))
        assert bounds.refusal({}, payload, ()) is not None  # one above
        monkeypatch.setattr(bounds, "limits", lambda: (0, weight + 1))
        assert bounds.refusal({}, payload, ()) is None  # one below

    @pytest.mark.parametrize("leaves, pending", [(64, 1), (64, 5), (100, 100), (4090, 20), (3, 70)])
    def test_the_same_for_the_member_limit(self, leaves, pending, members):
        payload = {"": ["a"] * leaves + [[] for _ in range(pending)]}
        weight = 2 + leaves + pending
        assert count_of(payload) == weight
        members(weight)
        assert bounds.refusal({}, payload, ()) is None
        members(weight - 1)
        assert bounds.refusal({}, payload, ()) is not None
        members(weight + 1)
        assert bounds.refusal({}, payload, ()) is None

    @pytest.mark.parametrize("pending", [1, 5, 100])
    def test_inside_a_big_mapping_too(self, pending, members):
        """The same early stop in ``_bulk_mapping``: a mapping of at least 64 entries whose values
        are partly containers."""
        payload = {"k%d" % i: ("v" if i < 70 else []) for i in range(70 + pending)}
        weight = reference_members(payload)
        assert count_of(payload) == weight
        members(weight)
        assert bounds.refusal({}, payload, ()) is None
        members(weight - 1)
        assert bounds.refusal({}, payload, ()) is not None

    def test_a_non_dict_big_mapping_too(self, members):
        class Other(Mapping):
            def __init__(self, data):
                self._d = data

            def __getitem__(self, key):
                return self._d[key]

            def __iter__(self):
                return iter(self._d)

            def __len__(self):
                return len(self._d)

        payload = Other({"k%d" % i: ("v" if i < 70 else []) for i in range(80)})
        weight = reference_members(payload)
        members(weight)
        assert bounds.refusal({}, payload, ()) is None
        members(weight - 1)
        assert bounds.refusal({}, payload, ()) is not None


def reference_size(mapping: Mapping[Any, Any]) -> int:
    """The characters, member by member (an empty string or bytes weighs one; any other leaf 8)."""

    def weigh(item):
        if isinstance(item, (str, bytes, bytearray)):
            return len(item) or 1
        if isinstance(item, Mapping):
            return 1 + sum(weigh(k) + weigh(v) for k, v in item.items())
        if isinstance(item, (list, tuple, set, frozenset)):
            return 1 + sum(weigh(m) for m in item)
        return 8

    return weigh(mapping) - 1


# ---------------------------------------------------------------------------------------------
# The walk is bounded by the limit, not by the value, for the count as for the size.
# ---------------------------------------------------------------------------------------------
class TestTheCountIsBoundedByTheLimit:
    @pytest.mark.parametrize("member", [int, list, dict, str, lambda: [1]])
    def test_members_pulled_before_the_limit_is_known_to_be_passed(self, member):
        pulled = []

        def endless():
            for i in range(10**6):
                pulled.append(i)
                yield member()

        class Endless(list):
            def __len__(self):
                return 10**6

            def __iter__(self):
                return endless()

        assert bounds._weigh_members({"k": Endless()}, 1_000, True) > 1_000
        assert len(pulled) <= 16_384, len(pulled)

    def test_a_cycle_runs_out_of_budget(self):
        cycle: List[Any] = [0] * 100
        cycle.append(cycle)
        assert bounds._weigh_members({"k": cycle}, 10_000, True) > 10_000

    def test_a_value_nested_past_the_stack_bound_is_refused_not_recursed_into(self):
        deep: Any = 0
        for _ in range(bounds._MAX_WEIGH_NESTING + 50):
            deep = [deep]
        assert bounds._weigh_members({"k": deep}, 10**9, True) > 10**9

    def test_nesting_is_refused_at_the_same_depth_as_the_size(self):
        for depth in (bounds._MAX_WEIGH_NESTING - 2, bounds._MAX_WEIGH_NESTING + 2):
            deep: Any = 0
            for _ in range(depth):
                deep = [deep]
            by_count = bounds._weigh_members({"k": deep}, 10**9, True) > 10**9
            by_size = bounds._weigh_members({"k": deep}, 10**9) > 10**9
            assert by_count == by_size, depth


# ---------------------------------------------------------------------------------------------
# The cost claim (#3844 item 1), reproduced as COUNTED WORK.
# ---------------------------------------------------------------------------------------------
class Recorder:
    """Stands in for the extension's view: records the members each ``update_state`` is handed,
    which is the pass that cost the review 0.8-3.9 s an event."""

    def __init__(self):
        self.handed: List[int] = []

    def update_state(self, state):
        self.handed.append(reference_count(state.get("form_data", {})) - 1)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _synced(view) -> int:
    recorder = Recorder()
    view._rust_view = recorder
    view._sync_state_to_rust()
    return recorder.handed[-1]


@pytest.fixture
def native():
    pytest.importorskip("djust._rust")


class TestTheEventPathIsBounded:
    @staticmethod
    def _flood(view, events: int, width: int, member: Any = "") -> None:
        for i in range(events):
            view.validate_field(field="k%d" % i, value=[member] * width)

    def test_the_review_scenario_hands_the_extension_at_most_the_limit(self, native):
        """260 events of ``[""] * 15000``: 2.6 million empty members stored before #3844 (the size
        limit alone allows them); what each event hands the extension is now at most the member limit."""
        view = mounted(WsContact)
        view._initialize_rust_view()
        with livecfg():
            self._flood(view, 20, 15_000)
            handed = _synced(view)
        assert handed <= DEFAULT_MAX_FORM_MEMBERS
        assert handed == reference_members(view.form_data)
        assert handed > DEFAULT_MAX_FORM_MEMBERS - 2 * 15_001  # the cap was reached, not far below it
        assert "more values" in view.form_errors[0]

    def test_the_review_scenario_with_containers_hands_the_extension_at_most_the_limit(self, native):
        view = mounted(WsContact)
        view._initialize_rust_view()
        with livecfg():
            self._flood(view, 30, 7_500, member=[])
            handed = _synced(view)
        assert handed <= DEFAULT_MAX_FORM_MEMBERS
        assert handed > DEFAULT_MAX_FORM_MEMBERS - 2 * 7_501
        assert "more values" in view.form_errors[0]

    def test_without_the_member_limit_the_same_events_hand_it_every_member(self, native):
        """The reproduction, so the bound above is not vacuous: with ``max_form_members = 0`` the
        same events are stored whole (the size limit does not stop them) and ALL of it is handed over."""
        view = mounted(WsContact)
        view._initialize_rust_view()
        with livecfg(max_form_members=0):
            self._flood(view, 60, 15_000)
            handed = _synced(view)
        assert handed > 8 * DEFAULT_MAX_FORM_MEMBERS  # 900,000 of them
        assert handed == reference_members(view.form_data)

    @pytest.mark.parametrize("limit", [1_000, 5_000])
    def test_the_bound_follows_the_configured_limit(self, native, limit):
        view = mounted(WsContact)
        view._initialize_rust_view()
        with livecfg(max_form_members=limit):
            self._flood(view, 40, 400)
            handed = _synced(view)
        assert limit - 2 * 401 < handed <= limit


class TestTheLimitHoldsOnEveryPath:
    def test_form_mixin_submit_form_is_all_or_nothing(self):
        view = mounted(WsContact)
        before = dict(view.form_data)
        with livecfg(max_form_members=100):
            view.submit_form(a=["x"] * 50, b=["y"] * 50)  # 2 + 50 + 2 + 50 + the declared field
            assert view.form_data == before
            assert view.is_valid is False and "more values" in view.form_errors[0]
            view.submit_form(a=["x"] * 40)
        assert view.form_data["a"] == ["x"] * 40
        assert "more values" not in str(getattr(view, "form_errors", ""))

    def test_form_mixin_validate_field_keeps_the_rest(self):
        view = mounted(WsContact)
        with livecfg(max_form_members=60):
            view.validate_field(field="a", value=["x"] * 40)
            view.validate_field(field="b", value=["x"] * 40)
        assert "b" not in view.form_data and len(view.form_data["a"]) == 40
        assert "more values" in view.form_errors[0]

    @pytest.mark.parametrize("event", ["update_step_field", "validate_field"])
    def test_the_wizard_step(self, event):
        view = mounted(Wizard)
        with livecfg(max_form_members=30):
            for i in range(5):
                getattr(view, event)(field="f%d" % i, value=["x"] * 10)
        assert sorted(view.wizard_step_data["one"]) == ["f0", "f1"]
        assert "more values" in view.wizard_step_errors["one"]["__all__"][0]

    def test_params_form(self):
        from test_form_data_bounds_3834 import Contact

        from djust._params_form import bind_params

        view = SimpleNamespace()
        with livecfg(max_form_members=10):
            form, values = bind_params(view, Contact, {"name": ["x"] * 20})
        assert values is None and "more values" in view.params_errors["__all__"][0]
        view = SimpleNamespace()
        with livecfg(max_form_members=10):
            form, values = bind_params(view, Contact, {"name": ["x"] * 8})  # 2 + 8 = exactly 10
        assert values is not None and view.params_errors == {}

    def test_the_admin_form_view(self):
        from test_form_data_bounds_3834 import TestAdminFormView

        view = TestAdminFormView()._view()
        with livecfg(max_form_members=20):
            view.validate_field(field="a", value=["x"] * 10)  # 12
            view.validate_field(field="b", value=["x"] * 10)  # 24: refused
        assert list(view.form_data) == ["a"]
        assert "more values" in str(view.form_errors)


# ---------------------------------------------------------------------------------------------
# Configuration: the key, its default, and what a bad value does (never "unlimited").
# ---------------------------------------------------------------------------------------------
class TestConfiguration:
    def test_the_default(self):
        assert DEFAULT_MAX_FORM_MEMBERS == 100_000
        with override_settings(LIVEVIEW_CONFIG={}):
            assert djust_config.get("max_form_members") == 100_000
            assert bounds.member_limit() == 100_000
        # the other two are exactly where they were
        assert bounds.limits() == (1000, 2_621_440)

    @pytest.mark.parametrize("bad", [-1, "1000", 1.5, None, True, [1]])
    def test_a_value_that_is_not_a_non_negative_integer_is_the_default_never_unlimited(self, bad):
        with override_settings(LIVEVIEW_CONFIG={"max_form_members": bad}):
            assert bounds.member_limit() == DEFAULT_MAX_FORM_MEMBERS

    def test_a_setting_is_read_on_every_call(self):
        with override_settings(LIVEVIEW_CONFIG={"max_form_members": 7}):
            assert bounds.member_limit() == 7
            assert bounds.refusal({}, {"k": [0] * 5}, ()) is None  # 2 + 5
            assert bounds.refusal({}, {"k": [0] * 6}, ()) is not None
        assert bounds.member_limit() == DEFAULT_MAX_FORM_MEMBERS

    def test_zero_is_no_limit(self):
        with override_settings(LIVEVIEW_CONFIG={"max_form_members": 0}):
            assert bounds.member_limit() == 0
            assert bounds.refusal({}, {"k": [0] * 200_000}, ()) is None  # inside the 2.6M characters

    def test_all_three_zero_returns_before_any_walk(self):
        class Boom(dict):
            def items(self):
                raise AssertionError("nothing is limited, nothing is walked")

        with override_settings(
            LIVEVIEW_CONFIG={"max_form_fields": 0, "max_form_data_size": 0, "max_form_members": 0}
        ):
            assert bounds.refusal({}, Boom(a=1), ()) is None

    def test_an_invalid_setting_is_reported_once_at_load(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            with override_settings(LIVEVIEW_CONFIG={"max_form_members": -5}):
                pass
        assert any("max_form_members" in r.getMessage() for r in caplog.records)

    def test_the_unlimited_warning_names_it_in_production(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            with override_settings(DEBUG=False, LIVEVIEW_CONFIG={"max_form_members": 0}):
                pass
        assert any(
            "max_form_members" in r.getMessage() and "no limit" in r.getMessage()
            for r in caplog.records
        )

    def test_it_does_not_warn_when_it_is_set(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            with override_settings(DEBUG=False, LIVEVIEW_CONFIG={"max_form_members": 500}):
                pass
        assert not any("no limit" in r.getMessage() and "form" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------------------------
# The enforcement line, written out as the mutations the gate-off ran (#1468).
# ---------------------------------------------------------------------------------------------
class TestGateOff:
    def test_with_the_check_neutered_the_exact_limit_tests_fail(self, members, monkeypatch):
        """Replace the verdict of the member check by ``False``: a payload past the limit is accepted.
        (The file-level mutation is recorded in the PR; this is the same change at runtime.)"""
        members(10)
        payload = {"k": [0] * 50}
        assert bounds.refusal({}, payload, ()) is not None
        real = bounds._over
        monkeypatch.setattr(bounds, "_over", lambda c, i, limit, count: False if count else real(c, i, limit, count))
        assert bounds.refusal({}, payload, ()) is None  # the neutered check admits what the real one refused

    def test_a_flood_of_members_is_refused_by_the_member_limit_not_the_size_limit(self, monkeypatch):
        monkeypatch.setattr(bounds, "limits", lambda: (0, 2_621_440))
        monkeypatch.setattr(bounds, "member_limit", lambda: 100_000)
        message = bounds.refusal({}, {"k": [""] * 150_000}, ())  # 150,002 characters: size is fine
        assert message is not None and "more values" in message
        assert bounds.refusal({}, {"k": [""] * 99_998}, ()) is None  # exactly 100,000 members
        assert bounds.refusal({}, {"k": [""] * 99_999}, ()) is not None


def verdict(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    return "fields" if "more fields" in message else ("members" if "more values" in message else "size")


class TestTheVerdictEqualsTheFullRecount:
    @pytest.mark.parametrize("seed", range(25))
    def test_generated_sessions(self, seed, members):
        """A session of events judged incrementally gives the verdict a full recount of the merged mapping gives."""
        rng = random.Random(seed)
        limit = rng.choice([8, 30, 70, 200])
        members(limit)
        stored: dict = {}
        accepted = refused = 0
        for _ in range(25):
            incoming = {
                rng.choice(["a", "b", "c", "d", "e"]): generated_value(rng, 2, chunks=False)
                for _ in range(rng.randrange(1, 3))
            }
            merged = {**stored, **incoming}
            expected = reference_members(merged) > limit
            got = bounds.refusal(stored, incoming, ()) is not None
            assert got == expected, (seed, limit, reference_members(merged))
            if got:
                refused += 1
            else:
                accepted += 1
                stored = merged
        assert accepted > 0
