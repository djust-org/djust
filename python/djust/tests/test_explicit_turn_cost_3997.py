"""The work an explicit turn's state save does, counted (#3997).

``tests/benchmarks/test_explicit_turn_cost_3997.py`` times complete turns;
this file pins the structural facts behind the savings, never a duration:

* the automatic save encodes the view's state ONCE per changed turn, and the
  whole text, the per-name digests and the envelope's size are all built from
  that encoding, character for character what encoding each again gives;
* the save's name rule is built once per save.
"""

from __future__ import annotations

import json
import zlib
from typing import Any, Dict, List

import pytest

from djust import LiveView, _automatic_state, event_handler
from djust._automatic_state import Projection, _digests, _encode, _envelope_length
from djust.decorators import state
from djust.tests import _per_turn_harness as harness
from djust.tests._frames import legacy_type

MODULE = __name__
ROWS = 200


def _rows(n: int) -> List[Dict[str, Any]]:
    return [{"id": i, "name": f"row {i}", "qty": i % 7, "tags": ["a", f"t{i}"]} for i in range(n)]


class SavedTable(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root><h1>{{ title }}</h1>{% for row in items %}<p>{{ row.name }} "
        "{{ row.qty }}</p>{% endfor %}</div>"
    )
    title = state("")
    items = state(default_factory=list)

    def _get_context_processors(self):
        return []

    def mount(self, request, **kwargs):
        self.title = "Rows"
        self.items = _rows(ROWS)
        self.note = "kept, never rendered"
        self._cursor = 3

    @event_handler()
    def cell(self, **kwargs):
        self.items[ROWS // 2]["qty"] += 1


# --- the encodings are the same text ------------------------------------------------

#: Values as ``encode_state_value`` hands them over: JSON data, tagged forms
#: included, with keys out of order, non-ASCII text and every scalar kind.
SECTIONS: List[Dict[str, Any]] = [
    {},
    {"b": 1, "a": 2},
    {
        "zeta": {"y": [1, 2.5, None, True], "x": "été ☃", "ä": {}},
        "alpha": [{"k": "v", "B": 0, "a": -1.25e-7}, [], ""],
        "Mixed_Case": False,
        "big": 2**40,
        "tagged": {"__djust_tuple": [1, "two"]},
        "über": "unicode name",
        "quote": 'he said "hi"\n\t\\',
    },
]


@pytest.mark.parametrize("public", SECTIONS)
@pytest.mark.parametrize("private", [{}, {"_cursor": 3, "_seen": ["x"]}])
@pytest.mark.parametrize("remount", [False, True])
def test_the_projection_text_is_the_whole_state_encoded_once(public, private, remount):
    components = {"tabs": {"active": "b"}} if public else {}
    projection = Projection(public, private, remount, components)
    assert projection.text == _encode([public, private, remount, components])
    assert projection.public_digests() == _digests(public)
    assert projection.private_digests() == _digests(private)


def test_a_section_with_a_key_that_is_not_text_is_encoded_whole():
    from djust._automatic_state import _section_text, _texts

    values = {2: "two", 1: "one"}
    assert _section_text(values, _texts(values)) == _encode(values)


@pytest.mark.parametrize("public", SECTIONS)
def test_the_envelope_length_is_the_length_of_the_envelope_encoded(public):
    projection = Projection(public, {"_c": 1}, False, {"tabs": {"n": 2}})
    envelope = {
        "version": 1,
        "binding": {"session": "abc", "user": None},
        "created": 1700000000,
        "expires": 1700086400,
        "values": projection.public,
        "private": projection.private,
        "remount": False,
        "components": projection.components,
        "changed": ["b", "é"],
        "deleted": [],
        "page": "x.y.Z",
        "bytes": len(projection.text.encode("utf-8")),
        "url": None,
    }
    assert _envelope_length(envelope, projection) == len(_encode(envelope))


# --- one encoding per changed turn, through the real consumer ---------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_changed_turn_encodes_the_state_once_and_builds_one_name_rule(monkeypatch):
    """Counted, not timed: the characters ``_encode`` writes during one turn
    that changed a 200-row list, against the size of the state it saved.

    Before #3997 the save encoded the state three times (the whole text, then
    each attribute again for its digest, then the envelope for its size)."""
    written = [0]
    real_encode = _automatic_state._encode

    def counting_encode(values: Any) -> str:
        text = real_encode(values)
        written[0] += len(text)
        return text

    rules = [0]
    real_names_init = _automatic_state._Names.__init__

    def counting_names_init(self: Any, view: Any) -> None:
        rules[0] += 1
        real_names_init(self, view)

    saved: List[Any] = []
    real_stage = _automatic_state.stage

    def recording_stage(view: Any, request: Any, **kwargs: Any) -> Any:
        undo = real_stage(view, request, **kwargs)
        rollback = view.__dict__["_djust_auto_basis"].rollback
        text = zlib.decompress(rollback).decode("utf-8") if type(rollback) is bytes else rollback
        saved.append(text)
        return undo

    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(SavedTable, key)
        try:
            await harness.event(comm, "cell")  # warm-up
            monkeypatch.setattr(_automatic_state, "_encode", counting_encode)
            monkeypatch.setattr(_automatic_state._Names, "__init__", counting_names_init)
            monkeypatch.setattr(_automatic_state, "stage", recording_stage)
            reply = await harness.event(comm, "cell")
        finally:
            await comm.disconnect()
    assert legacy_type(reply) == "patch"
    assert len(saved) == 1, "exactly one save ran in the measured turn"
    state_chars = len(saved[0])  # the save's whole text (its rollback copy)
    assert state_chars > ROWS * 30, "the measured save carried the rows"
    # Once: the state's own text plus its names and the envelope's skeleton.
    assert state_chars <= written[0] < state_chars * 1.1, (written[0], state_chars)
    assert rules[0] == 1, "one name rule per save"


# --- the state encoder's leaf fast path ------------------------------------------------


def _node_count(value: Any) -> int:
    """The nodes ``encode_state_value`` counts for plain JSON data: one per
    container and per member (a dict's keys are not nodes)."""
    if isinstance(value, dict):
        return 1 + sum(_node_count(child) for child in value.values())
    if isinstance(value, list):
        return 1 + sum(_node_count(child) for child in value)
    return 1


def _depth(value: Any) -> int:
    if isinstance(value, dict) and value:
        return 1 + max(_depth(child) for child in value.values())
    if isinstance(value, list) and value:
        return 1 + max(_depth(child) for child in value)
    return 0


PLAIN = [
    [],
    {},
    [None, True, False, "", "x", 0, -1, 2**53 - 1, -(2**53) + 1, 1.5, -0.0],
    {"a": [1, {"b": [None, "é", 2.25]}], "c": {}, "d": []},
    [{"id": i, "name": f"row {i}", "qty": i % 7, "tags": ["a", f"t{i}"]} for i in range(20)],
    [[[[["deep"]]]]],
]


@pytest.mark.parametrize("value", PLAIN)
def test_plain_data_encodes_as_itself_with_exact_node_and_depth_limits(value):
    from djust.serialization import StateValueError, encode_state_value

    nodes, depth = _node_count(value), _depth(value)
    encoded = encode_state_value(value, max_nodes=nodes, max_depth=depth)
    assert encoded == value and json.dumps(encoded) == json.dumps(value)
    assert encoded is not value or not isinstance(value, (list, dict)), "a copy, never the value"
    with pytest.raises(StateValueError) as over_nodes:
        encode_state_value(value, max_nodes=nodes - 1, max_depth=depth)
    assert over_nodes.value.kind == "structure"
    if depth:
        with pytest.raises(StateValueError) as over_depth:
            encode_state_value(value, max_nodes=nodes, max_depth=depth - 1)
        assert over_depth.value.kind == "structure"


@pytest.mark.parametrize(
    "value, tagged",
    [
        ([2**53, 2.0], [{"__djust_type": ["int", str(2**53)]}, 2.0]),
        ([float("inf")], [{"__djust_type": ["float", "inf"]}]),
        ({"t": (1, "a")}, {"t": {"__djust_type": ["tuple", [1, "a"]]}}),
        ({1: "x"}, {"__djust_type": ["dict", [[1, "x"]]]}),
        ({"__djust_x": 1}, {"__djust_type": ["dict", [["__djust_x", 1]]]}),
    ],
)
def test_values_that_are_not_their_own_encoding_still_go_through_the_walk(value, tagged):
    from djust.serialization import STATE_VALUE_TAG, encode_state_value

    expected = json.loads(json.dumps(tagged).replace("__djust_type", STATE_VALUE_TAG))
    assert encode_state_value(value) == expected


def test_the_first_refusal_is_the_one_met_first_in_order():
    from djust.serialization import StateValueError, encode_state_value

    with pytest.raises(StateValueError) as early_type:
        encode_state_value([object()] + [0] * 10, max_nodes=5)
    assert early_type.value.kind != "structure"
    with pytest.raises(StateValueError) as early_size:
        encode_state_value([0] * 10 + [object()], max_nodes=5)
    assert early_size.value.kind == "structure"
    with pytest.raises(StateValueError) as subclass:
        encode_state_value({"a": [1, True, type("S", (str,), {})("x")]})
    assert subclass.value.kind != "structure", "an str subclass is refused, never kept"


def test_a_list_of_scalars_is_one_walk_call_not_one_per_member():
    """Counted with a profile hook, not timed: the encoder's ``walk`` runs
    once for a list of 1,000 scalars, and once per row (plus once per nested
    list) for a list of dict rows, not once per cell."""
    import sys

    from djust.serialization import encode_state_value

    def walk_calls(value: Any) -> int:
        calls = [0]

        def hook(frame: Any, event: str, arg: Any) -> None:
            if event == "call" and frame.f_code.co_name == "walk":
                if frame.f_code.co_filename.endswith("serialization.py"):
                    calls[0] += 1

        sys.setprofile(hook)
        try:
            encode_state_value(value)
        finally:
            sys.setprofile(None)
        return calls[0]

    assert walk_calls(list(range(1000)) + ["x", None, 1.5, True]) == 1
    rows = [{"id": i, "name": f"r{i}", "tags": ["a", "b"]} for i in range(100)]
    assert walk_calls(rows) == 1 + 100 + 100


# --- the save's name rule -----------------------------------------------------------------


class NamedView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0)
    plain_default = 3

    def mount(self, request, **kwargs):
        self.count = 1
        self.note = "public, undeclared"
        self._cursor = 2  # recorded as mount()'s private attribute below
        self.template_name = "x.html"  # configuration a djust base defines


def _brute_present(view: Any, names: Any) -> frozenset:
    return frozenset(n for n in list(vars(view)) if names.application(n) or names.private(n))


def test_present_asks_the_rule_only_for_names_that_are_not_the_frameworks(monkeypatch):
    view = NamedView()
    view.mount(None)
    view.__dict__["_user_private_keys"] = frozenset({"_cursor"})
    names = _automatic_state._Names(view)
    expected = _brute_present(view, names)
    assert {"note", "_cursor"} <= expected and "template_name" not in expected

    asked: List[str] = []
    real_application = _automatic_state._Names.application

    def counting_application(self: Any, name: Any) -> bool:
        asked.append(name)
        return real_application(self, name)

    monkeypatch.setattr(_automatic_state._Names, "application", counting_application)
    assert _automatic_state._present(view, names) == expected
    framework = set(vars(view)) & names.framework
    assert len(framework) > 20, "a constructed view holds the framework's own attributes"
    assert not framework & set(asked), "no framework attribute is classified"
    assert len(asked) == len(set(vars(view)) - names.framework)


def test_a_name_a_djust_mixin_defines_is_configuration_read_live():
    """The rule looks a class-level name up in the live class dictionaries of
    the view's MRO (no snapshot): a name a djust mixin defines is
    configuration, never saved, and so is one the mixin gains later."""
    from djust.forms import FormMixin

    class FormNamed(FormMixin, LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    view = FormNamed()
    view.form_class = None
    view.late_name = 1
    names = _automatic_state._Names(view)
    assert not names.application("form_class")
    assert names.application("late_name")
    setattr(FormMixin, "late_name", None)
    try:
        assert not _automatic_state._Names(view).application("late_name")
    finally:
        delattr(FormMixin, "late_name")
    assert _automatic_state._Names(view).application("late_name")
