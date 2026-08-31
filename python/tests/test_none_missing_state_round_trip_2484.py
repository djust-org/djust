"""A state round trip keeps `None` a `None` — it no longer becomes `""` (#2484).

The divergence
--------------
``Value::None`` and ``Value::Missing`` are deliberately DISTINCT (#2203):
``None`` renders ``"None"`` as ``str(None)`` does, ``Missing`` renders ``""`` as
Django's ``string_if_invalid`` does. The msgpack codec collapsed them —
``impl Serialize for Value`` wrote both as one ``nil`` and ``visit_unit`` read
every ``nil`` back as ``Missing``::

    {{ p }}      p = None            django 'None'   djust 'None'  -> after one round trip: ''
    {{ d.a }}    d = {"a": None}     django 'None'   djust 'None'  -> after one round trip: ''

``SerializableViewState.state`` round-trips through msgpack on EVERY read of
the default ``InMemoryStateBackend`` and of the Redis backend, so a ``None``
rendered correctly on the first render and rendered the EMPTY STRING after one
cache hit. Nondeterminism the app author cannot explain from the template.

The blast radius, measured
--------------------------
``TestTheWholeFilterRegistry`` sweeps Django's LIVE ``defaultfilters`` registry
with ``p = None``. Before the fix, **35 of 58** cells agreed with Django on the
first render and stopped agreeing after one round trip — including the two
filters that exist to branch on exactly this value,
``{{ p|default_if_none:"D" }}`` (``"D"`` -> ``""``) and
``{{ p|yesno:"y,n,m" }}`` (``"m"`` -> ``"n"``). In a plausible LiveView state
blob, 13 of 27 leaf values were ``None``.

The encoding decision
---------------------
The tag goes on ``Missing``, NOT on ``None``, so the common value's bytes do
not change. Both cross-version directions, stated:

* **OLD payload, NEW reader** — a bare ``nil`` reads as ``Value::None`` and
  renders ``"None"``. Correct, not merely tolerated: ``FromPyObject`` has no arm
  producing a ``Missing``, so a Python ``None`` is the only thing that can have
  put a ``nil`` in a state blob. A stale Redis entry is FIXED by the upgrade.
  Pinned in ``test_a_pre_fix_state_blob_reads_as_None``.
* **NEW payload, OLD reader** — a ``None`` is still one ``nil`` byte, so an old
  reader still turns it into a ``Missing`` and still renders ``""``: exactly
  today's behaviour, no new failure during a rolling deploy. Pinned as a byte
  identity in ``test_the_wire_shape_of_None_did_not_move`` and, in Rust, in
  ``test_none_missing_codec_2484.rs::the_none_encoding_is_byte_identical_to_the_pre_fix_one``.

Every expectation is LIVE Django, never a transcription.

Refs #2484, #2481, #2478, #2466, #2458, #2448, #2276, #2260, #2214, #2203, #1541.
"""

from __future__ import annotations

import datetime

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register as django_filter_registry  # noqa: E402

from djust._rust import RustLiveView  # noqa: E402

DT = datetime.datetime(2020, 1, 1, 3, 4, 5)


def django_render(source: str, context: dict) -> str:
    try:
        return DjangoTemplate(source).render(DjangoContext(dict(context)))
    except Exception as exc:  # noqa: BLE001 — the refusal IS the answer
        return f"<<REFUSED {type(exc).__name__}>>"


def _view(source: str, state: dict) -> RustLiveView:
    view = RustLiveView(source)
    for key, value in state.items():
        view.set_state(key, value)
    return view


def first_render(source: str, state: dict) -> str:
    try:
        return _view(source, state).render()
    except Exception as exc:  # noqa: BLE001
        return f"<<REFUSED {type(exc).__name__}>>"


def after_round_trip(source: str, state: dict) -> str:
    """Render after a msgpack state round trip — what the default
    `InMemoryStateBackend` and the Redis backend do on EVERY read."""
    try:
        view = _view(source, state)
        view.render()
        return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()
    except Exception as exc:  # noqa: BLE001
        return f"<<REFUSED {type(exc).__name__}>>"


# Every shape a `None` can sit in, which is the issue's scope line: the gap was
# the CODEC's, so the containers are separate doors onto one arm.
PLACEMENTS = [
    pytest.param("{{ p }}", {"p": None}, id="top-level"),
    pytest.param("{{ d.a }}", {"d": {"a": None}}, id="in-a-dict"),
    pytest.param("{{ d.a.b }}", {"d": {"a": {"b": None}}}, id="two-dicts-deep"),
    pytest.param("{{ xs.0 }}", {"xs": [None, 1]}, id="in-a-list"),
    pytest.param("{{ d.a.0 }}", {"d": {"a": [None]}}, id="a-list-in-a-dict"),
    pytest.param("{{ xs.0.a }}", {"xs": [{"a": None}]}, id="a-dict-in-a-list"),
    pytest.param("{{ p.tzinfo }}", {"p": DT}, id="an-Encoded-attribute"),
    pytest.param("{{ xs|join:',' }}", {"xs": [None, 1]}, id="joined-out-of-a-list"),
    pytest.param("{{ d }}", {"d": {"a": None}}, id="inside-a-rendered-dict"),
]


class TestTheRoundTripKeepsIt:
    """The reproducer, on the real path, against live Django."""

    @pytest.mark.parametrize(("source", "state"), PLACEMENTS)
    def test_the_answer_survives_one_state_round_trip(self, source: str, state: dict) -> None:
        expected = django_render(source, state)
        assert first_render(source, state) == expected
        assert after_round_trip(source, state) == expected, (
            "the value changed across one cache hit — #2484 reopened"
        )

    @pytest.mark.parametrize(("source", "state"), PLACEMENTS)
    def test_the_round_trip_is_idempotent(self, source: str, state: dict) -> None:
        """Two hits, not one. A codec that merely moved the collapse one trip
        later would pass the test above."""
        view = _view(source, state)
        view.render()
        blob = view.serialize_msgpack()
        for _ in range(3):
            view = RustLiveView.deserialize_msgpack(blob)
            assert view.render() == django_render(source, state)
            blob = view.serialize_msgpack()

    def test_a_missing_variable_still_renders_empty(self) -> None:
        """The other half of the pair, which the fix must NOT move: an absent
        name is Django's `string_if_invalid`, not `str(None)`. Without this the
        fix could 'pass' by making everything a `None`."""
        assert django_render("{{ nope }}", {}) == ""
        assert first_render("{{ nope }}", {"p": 1}) == ""
        assert after_round_trip("{{ nope }}", {"p": 1}) == ""

    def test_None_and_a_missing_name_still_disagree_after_a_round_trip(self) -> None:
        """#2203's distinction, restated after the trip — the fix separates the
        two on the wire rather than merging them the other way."""
        for renderer in (first_render, after_round_trip):
            assert renderer("{{ p }}|{{ nope }}", {"p": None}) == "None|"


class TestTheWholeFilterRegistry:
    """The measured blast radius. Django's LIVE registry, read rather than
    transcribed, so a Django release that adds a filter is swept too."""

    # Enough of an argument to make each filter do its work; `None` for the
    # nullary ones. Read against the live registry below, so a name that
    # disappears fails loudly.
    ARGS = {
        "add": "'1'",
        "center": "5",
        "cut": "'a'",
        "date": "'Y'",
        "default": "'D'",
        "default_if_none": "'D'",
        "dictsort": "'k'",
        "dictsortreversed": "'k'",
        "divisibleby": "2",
        "floatformat": "2",
        "get_digit": "1",
        "join": "','",
        "ljust": "5",
        "rjust": "5",
        "slice": "':2'",
        "stringformat": "'s'",
        "time": "'H'",
        "truncatechars": "3",
        "truncatechars_html": "3",
        "truncatewords": "3",
        "truncatewords_html": "3",
        "urlizetrunc": "5",
        "wordwrap": "5",
        "yesno": "'y,n,m'",
    }

    def _cells(self) -> list[str]:
        cells = ["{{ p }}"]
        for name in sorted(django_filter_registry.filters):
            arg = self.ARGS.get(name)
            cells.append("{{ p|%s%s }}" % (name, f":{arg}" if arg else ""))
        return cells

    def test_the_argument_table_names_only_live_filters(self) -> None:
        """Non-vacuity for the table: a stale name would silently stop
        supplying an argument and the cell would test something else."""
        live = set(django_filter_registry.filters)
        assert set(self.ARGS) <= live, sorted(set(self.ARGS) - live)

    def test_no_cell_changes_its_answer_across_one_round_trip(self) -> None:
        state = {"p": None}
        moved = []
        for source in self._cells():
            before = first_render(source, state)
            after = after_round_trip(source, state)
            if before != after:
                moved.append((source, before, after))
        assert not moved, (
            f"{len(moved)} of {len(self._cells())} registry cells change their "
            f"answer after one cache hit: {moved[:5]}"
        )

    def test_the_sweep_is_wide_enough_to_have_caught_the_defect(self) -> None:
        """Non-vacuity for the sweep itself. Before #2484, 35 of these cells
        agreed with Django and then stopped; the count is not asserted (it
        tracks Django's registry) but the two SHARPEST cells are, because they
        are the ones whose whole purpose is branching on this value."""
        cells = self._cells()
        assert len(cells) >= 50, "the registry sweep shrank"
        for source, expected in (
            ("{{ p|default_if_none:'D' }}", "D"),
            ("{{ p|yesno:'y,n,m' }}", "m"),
            ("{{ p|length }}", "0"),
            ("{{ p|pprint }}", "None"),
        ):
            assert source in cells
            assert django_render(source, {"p": None}) == expected
            assert after_round_trip(source, {"p": None}) == expected


class TestTheWireDecision:
    """The compatibility half, which is the decision this issue is about."""

    def test_the_wire_shape_of_None_did_not_move(self) -> None:
        """The NEW-payload / OLD-reader answer, made assertable without an old
        build: an old reader's behaviour on new bytes is entirely determined by
        the bytes not having moved. A `None` is still a single msgpack `nil`
        inside the state map, so an old reader still reads a `Missing` and
        still renders `""` — today's behaviour, no new failure."""
        msgpack = pytest.importorskip("msgpack")

        view = _view("{{ p }}", {"p": None})
        view.render()
        decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
        state = decoded[1]
        assert state["p"] is None, f"the encoding of None moved: {state['p']!r}"

        # And the tag is nowhere in the blob, because no real path emits a
        # `Missing` — see `test_a_missing_cannot_enter_state_through_the_python_conversion`.
        assert b"__djust_missing__" not in view.serialize_msgpack()

    def test_a_pre_fix_state_blob_reads_as_None(self) -> None:
        """The OLD-payload / NEW-reader answer. A blob written by any pre-fix
        build carries a bare `nil` for a `None`; forge exactly that and check
        it renders Django's spelling. This is the direction the upgrade FIXES,
        which is only sound because Python `None` is the only producer of a
        `nil` in a state blob."""
        msgpack = pytest.importorskip("msgpack")

        view = _view("{{ p }}|{{ d.a }}", {"p": "x", "d": {"a": "y"}})
        view.render()
        decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
        # Rewrite both slots to the bare `nil` a pre-fix writer produced.
        decoded[1]["p"] = None
        decoded[1]["d"]["a"] = None
        legacy = msgpack.packb(decoded, use_bin_type=True)

        assert RustLiveView.deserialize_msgpack(legacy).render() == "None|None"
        assert django_render("{{ p }}|{{ d.a }}", {"p": None, "d": {"a": None}}) == "None|None"

    def test_a_missing_cannot_enter_state_through_the_python_conversion(self) -> None:
        """The measurement the tag-on-`Missing` decision rests on, and the
        reason the OLD-payload direction is a fix rather than a guess:
        `FromPyObject` has no arm producing a `Value::Missing`, so nothing a
        Python caller can put in state serializes to the tagged spelling an old
        reader would not understand.

        Swept over every shape the conversion has an arm for, rather than over
        `None` alone.
        """
        import decimal

        shapes = {
            "none": None,
            "true": True,
            "int": 7,
            "bigint": 10**30,
            "decimal": decimal.Decimal("1.50"),
            "float": 1.5,
            "str": "x",
            "bytes_like": "y",
            "list": [None, 1],
            "tuple": (None, 1),
            "dict": {"a": None, "b": {"c": None}},
            "set": {1},
            "datetime": DT,
            "date": DT.date(),
            "time": DT.time(),
            "timedelta": datetime.timedelta(days=3),
            "empty_list": [],
            "empty_dict": {},
        }
        view = RustLiveView("{{ none }}")
        for key, value in shapes.items():
            view.set_state(key, value)
        view.render()
        blob = view.serialize_msgpack()
        assert b"__djust_missing__" not in blob, (
            "a Python value reached the serializer as a Missing — the "
            "tag-on-Missing compatibility argument needs re-checking"
        )
        # And nothing was lost doing it.
        assert RustLiveView.deserialize_msgpack(blob).render() == "None"

    def test_only_a_one_key_map_with_a_nil_payload_reads_as_a_missing(self) -> None:
        """The tag's discrimination, from Python. `{{ d|length }}` is the probe
        because a `Missing` has no length and a dict does — and because the tag
        name itself is `_`-prefixed, so djust refuses `{{ d.__djust_missing__ }}`
        outright (#2418) and cannot be used to look inside.

        A dict that is EXACTLY the tag key with a `None` value IS read back as
        a `Missing` — the same deliberate ugliness the four sibling tags carry,
        pinned here in the diverging direction rather than discovered later.
        The name is chosen to make that a thing you have to try to do, and
        every producer of an attribute map skips `_`-prefixed names.
        """
        tag = "__djust_missing__"

        # Two keys: not a one-key map, so still a dict on both sides.
        two = {"d": {tag: None, "other": 1}}
        assert django_render("{{ d|length }}", two) == "2"
        assert first_render("{{ d|length }}", two) == "2"
        assert after_round_trip("{{ d|length }}", two) == "2"

        # Right key, wrong payload: still a dict.
        wrong = {"d": {tag: 1}}
        assert django_render("{{ d|length }}", wrong) == "1"
        assert first_render("{{ d|length }}", wrong) == "1"
        assert after_round_trip("{{ d|length }}", wrong) == "1"

        # Near-miss key names: still a dict.
        for near in ("__djust_missing", "_djust_missing__", "__djust_missing__x"):
            assert after_round_trip("{{ d|length }}", {"d": {near: None}}) == "1", near

        # And the documented collision, stated rather than hidden: exactly the
        # key, exactly a `None`, and it comes back a `Missing`.
        forged = {"d": {tag: None}}
        assert django_render("{{ d|length }}", forged) == "1"
        assert first_render("{{ d|length }}", forged) == "1"
        assert after_round_trip("{{ d|length }}", forged) == "0"
