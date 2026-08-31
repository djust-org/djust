"""`{{ dt.year }}` resolves — a `Value::Encoded` carries its attributes (#2481).

The divergence
--------------
Django's ``Variable._resolve_lookup`` tries three things per dotted segment:
mapping item access, then ``getattr``, then an integer index. djust's
``context::lookup_segment`` implemented steps 1 and 3 and said so in as many
words — *"attribute access — see the note above; a `Value` has none"*. So every
dotted lookup on a ``datetime`` / ``date`` / ``time`` / ``timedelta`` resolved
to NOTHING::

    {{ p.year }}    datetime(2026, 3, 4)   django 2026   djust ''
    {{ p.days }}    timedelta(days=3)      django 3      djust ''

``{{ post.published.year }}`` is an ordinary Django idiom and it rendered the
empty string.

Which paths, measured rather than reasoned about
-------------------------------------------------
* The ``DjustTemplateBackend`` path is affected, and every case below is
  measured through BOTH ``render_template`` and ``render_template_with_dirs``
  — the entry points ``python/djust/template/backend.py`` binds. They are one
  sink (``Context::get`` → ``lookup_segment``), and asserting both is what
  keeps that a measurement.
* The LiveView path had a partial escape: ``crates/djust_live/src/lib.rs``
  attaches a ``raw_py_objects`` sidecar, so ``{{ dt.year }}`` could resolve
  through ``getattr`` THERE. A fallback on one path is not the rule (#1646),
  and the fix is at the carrier rather than at one caller — which is why
  ``TestBothPathsAgree`` asserts the sidecar path answers the same.
* It predates ``Value::Encoded``: before #2448 a ``datetime`` was
  ``Value::String(str(o))``, which has no attributes either.

What this closes, and what it deliberately does not
-----------------------------------------------------
The map carries the attributes Python answers WITHOUT being called and WITHOUT
recursing. Two families stay divergent and are pinned below in the diverging
direction rather than quietly widened:

* **class attributes** ``min`` / ``max`` / ``resolution``. Their values are
  themselves ``datetime``s, and ``datetime.min.min is datetime.min`` — so
  collecting them would not terminate. Measured, in
  ``test_the_class_attributes_would_not_terminate``.
* **nullary methods** — CLOSED by #2485, which added a second table and a
  second producer writing into this same map. What is still exempt here is the
  subset whose RESULT spells differently (``date`` / ``time`` / ``timetuple``
  and five more the #2485 issue never named): Django LOCALIZES a ``date``, so
  carrying the call's result would swap one divergence for another rather than
  close a cell. See ``METHODS_RESULT_SPELLS_DIFFERENTLY`` below and
  ``python/tests/test_nullary_autocall_2485.py``.

``Decimal`` is a different CARRIER (``Value::Decimal``, not ``Encoded``), so
``{{ d.real }}`` is untouched here and pinned as still-divergent.

Every expectation is LIVE Django and LIVE Python, never a transcription.

Refs #2481, #2478, #2466, #2458, #2448, #2371, #1541, #1646, #1079.
"""

from __future__ import annotations

import datetime
import decimal
import pathlib
import re
import zoneinfo

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE_RS = REPO / "crates" / "djust_core" / "src" / "lib.rs"
CONTEXT_RS = REPO / "crates" / "djust_core" / "src" / "context.rs"


def django_render(source: str, context: dict) -> str:
    try:
        return DjangoTemplate(source).render(DjangoContext(dict(context)))
    except Exception:  # noqa: BLE001 — the refusal IS the answer
        return "<<REFUSED>>"


def djust_render(source: str, context: dict) -> str:
    try:
        return _rust.render_template(source, dict(context))
    except Exception:  # noqa: BLE001
        return "<<REFUSED>>"


def self_round_trip(source: str, value: object) -> str:
    """Render after a msgpack state round trip — what the default
    `InMemoryStateBackend` does on every read."""
    from djust._rust import RustLiveView

    view = RustLiveView(source)
    view.set_state("p", value)
    return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()


def djust_render_with_dirs(source: str, context: dict) -> str:
    """The OTHER entry point `DjustTemplateBackend` binds."""
    try:
        return _rust.render_template_with_dirs(source, dict(context), [])
    except Exception:  # noqa: BLE001
        return "<<REFUSED>>"


DT = datetime.datetime(2026, 3, 4, 5, 6, 7, 8)
DATE = datetime.date(2026, 3, 4)
TIME = datetime.time(5, 6, 7, 8)
TD = datetime.timedelta(days=3, seconds=90, microseconds=5)
AWARE = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc)
ZONED = datetime.datetime(2026, 3, 4, tzinfo=zoneinfo.ZoneInfo("America/New_York"))

#: `(label, value, attribute)` for every attribute the map carries. Named per
#: TYPE rather than swept off one value, because each type has a DIFFERENT list
#: and a fix that served only `datetime` would look complete against a sweep
#: that only fed it datetimes (#1104).
CLOSED = [
    *[
        pytest.param(DT, a, id=f"datetime-{a}")
        for a in (
            "year",
            "month",
            "day",
            "hour",
            "minute",
            "second",
            "microsecond",
            "fold",
            "tzinfo",
        )
    ],
    *[pytest.param(DATE, a, id=f"date-{a}") for a in ("year", "month", "day")],
    *[
        pytest.param(TIME, a, id=f"time-{a}")
        for a in ("hour", "minute", "second", "microsecond", "fold", "tzinfo")
    ],
    *[pytest.param(TD, a, id=f"timedelta-{a}") for a in ("days", "seconds", "microseconds")],
    *[pytest.param(AWARE, a, id=f"aware-{a}") for a in ("year", "hour", "tzinfo")],
    *[pytest.param(ZONED, a, id=f"zoneinfo-{a}") for a in ("year", "tzinfo")],
]

#: Class attributes. Django renders them; the map cannot carry them because
#: their values are the same type and the collection would not terminate.
CLASS_ATTRS = (
    [pytest.param(DT, a, id=f"datetime-{a}") for a in ("min", "max", "resolution")]
    + [pytest.param(DATE, a, id=f"date-{a}") for a in ("min", "max", "resolution")]
    + [pytest.param(TIME, a, id=f"time-{a}") for a in ("min", "max", "resolution")]
    + [pytest.param(TD, a, id=f"timedelta-{a}") for a in ("min", "max", "resolution")]
)

#: Nullary methods Django AUTO-CALLS whose RESULT still spells differently here.
#:
#: This list was the whole nullary-method family until #2485, which closed every
#: member whose result djust renders the way Django renders it (`isoformat`,
#: `weekday`, `ctime`, `toordinal`, `total_seconds`, `timestamp`, `utcoffset`,
#: `tzname`, `dst`, `isoweekday` — measured, and asserted in
#: `python/tests/test_nullary_autocall_2485.py`). The exemption is FLIPPED
#: rather than deleted (#1859): what stays is what is genuinely still exempt,
#: and it grew — the members below that the #2485 issue never listed
#: (`isoweekday` aside, it named `date`/`time`/`timetuple` and stopped) were
#: found by sweeping `dir(o)` rather than by reading the report.
#:
#: The reason they stay is one reason, and it is not the auto-call: their result
#: is itself a `date` / `time` / `datetime` / `struct_time` / `IsoCalendarDate`,
#: and djust's BARE render of those already differs from Django's — Django
#: LOCALIZES (`March 4, 2026` against `2026-03-04`). Carrying them would swap
#: one divergence for another rather than close a cell.
METHODS_RESULT_SPELLS_DIFFERENTLY = [
    *[
        pytest.param(DT, a, id=f"datetime-{a}")
        for a in (
            "date",
            "time",
            "timetz",
            "timetuple",
            "utctimetuple",
            "isocalendar",
            "replace",
            "astimezone",
        )
    ],
    *[pytest.param(DATE, a, id=f"date-{a}") for a in ("timetuple", "isocalendar", "replace")],
    *[pytest.param(TIME, a, id=f"time-{a}") for a in ("replace",)],
]


class TestTheCitedDivergenceIsClosed:
    """The issue's table, per type, through both entry points."""

    @pytest.mark.parametrize(("value", "attr"), CLOSED)
    def test_the_attribute_renders_exactly_as_django_renders_it(
        self, value: object, attr: str
    ) -> None:
        source = "{{ p.%s }}" % attr
        expected = django_render(source, {"p": value})
        # Non-vacuity for the row itself: an attribute Django ALSO renders
        # empty would make the assertion below pass without the fix.
        assert expected != "", f"Django renders nothing for .{attr} — bad row"
        assert djust_render(source, {"p": value}) == expected
        assert djust_render_with_dirs(source, {"p": value}) == expected

    def test_the_issues_own_two_headline_cells(self) -> None:
        """Spelled out verbatim so the issue's claim has a named assertion."""
        assert django_render("{{ p.year }}", {"p": DT}) == "2026"
        assert djust_render("{{ p.year }}", {"p": DT}) == "2026"
        assert django_render("{{ p.days }}", {"p": datetime.timedelta(days=3)}) == "3"
        assert djust_render("{{ p.days }}", {"p": datetime.timedelta(days=3)}) == "3"


class TestTheValueIsAnINTNotItsSpelling:
    """A carried attribute has to be the Python value, or every consumer that
    is not `{{ }}` answers differently from Django.

    The cheap wrong fix — carrying `str(getattr(o, name))` — renders the same
    and fails all of these.
    """

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            ("{% if p.year > 2000 %}T{% else %}F{% endif %}", DT),
            ("{% if p.year < 2000 %}T{% else %}F{% endif %}", DT),
            ("{{ p.days|add:1 }}", TD),
            ("{{ p.month|divisibleby:3 }}", DT),
            ("{{ p.microsecond|stringformat:'05d' }}", DT),
            ("{% if p.fold %}T{% else %}F{% endif %}", DT),
            ("{% if p.tzinfo %}T{% else %}F{% endif %}", DT),
            ("{% if p.tzinfo %}T{% else %}F{% endif %}", AWARE),
        ],
    )
    def test_the_operand_channels_agree_with_django(self, source: str, value: object) -> None:
        assert djust_render(source, {"p": value}) == django_render(source, {"p": value})

    def test_a_string_spelling_would_have_failed_the_comparison(self) -> None:
        """Why the assertions above are not decoration: the string spelling of
        the same attribute answers a DIFFERENT `{% if %}`."""
        source = "{% if p > 2000 %}T{% else %}F{% endif %}"
        assert django_render(source, {"p": 2026}) == "T"
        assert djust_render(source, {"p": 2026}) == "T"
        assert djust_render(source, {"p": "2026"}) == "F"


class TestWhereTheAttributeIsREACHED:
    """The lookup is one segment of a walk, so it has to work at depth, in a
    loop, and under the constructs that resolve through the other resolver
    (`renderer::get_value_safe`, #1646)."""

    @pytest.mark.parametrize(
        "source",
        [
            "{{ d.when.year }}",
            "{% for x in rows %}{{ x.year }}|{% endfor %}",
            "{% with y=d.when.year %}{{ y }}{% endwith %}",
            "{% if d.when.year %}{{ d.when.year }}{% endif %}",
            "{% firstof d.when.year %}",
            "{{ rows.0.year }}",
            "{{ d.when.year|add:1 }}",
        ],
    )
    def test_every_operand_channel_reaches_it(self, source: str) -> None:
        ctx = {"d": {"when": DT}, "rows": [DT, DATE]}
        expected = django_render(source, ctx)
        assert expected != "", source
        assert djust_render(source, ctx) == expected
        assert djust_render_with_dirs(source, ctx) == expected

    def test_a_datetime_SUBCLASS_gets_the_datetime_list(self) -> None:
        """The name list is keyed off the `tp_name` `django_json_encoded`
        resolved, NOT off the `type_name` it carries — which for a Python-level
        subclass is the SUBCLASS's `__name__` (right for a `TypeError` message,
        wrong as a lookup key)."""

        class MyDT(datetime.datetime):
            pass

        sub = MyDT(2026, 3, 4, 5, 6, 7)
        for attr in ("year", "month", "hour"):
            source = "{{ p.%s }}" % attr
            assert djust_render(source, {"p": sub}) == django_render(source, {"p": sub})

    def test_an_attribute_the_object_does_not_have_stays_empty(self) -> None:
        """The map adds names; it must not add an ANSWER for a name that is
        not one. Django renders empty for an unresolvable path."""
        for source in ("{{ p.nope }}", "{{ p.year.nope }}", "{{ p.days }}"):
            assert djust_render(source, {"p": DT}) == django_render(source, {"p": DT}) == ""


class TestBothPathsAgree:
    """The sidecar path (`raw_py_objects`, LiveView) and the plain value-stack
    path must answer the same — the partial escape #2481 is about (#1646).

    `RustLiveView.set_state` is the sidecar-attaching entry point.
    """

    @pytest.mark.parametrize(("value", "attr"), CLOSED)
    def test_the_liveview_path_answers_the_same(self, value: object, attr: str) -> None:
        from djust._rust import RustLiveView

        source = "{{ p.%s }}" % attr
        view = RustLiveView(source)
        view.set_state("p", value)
        assert view.render() == django_render(source, {"p": value})


class TestTheStateRoundTripKeepsTheAttributes:
    """`SerializableViewState.state` round-trips through msgpack on EVERY read
    of the default `InMemoryStateBackend`, so an uncarried attribute map would
    answer on the first render and go empty after one cache hit — the shape
    #2458's `truthy` and #2471/#2472's `repr`/`cmp_key` each shipped to
    prevent, now for the fourth time.
    """

    @staticmethod
    def _round_trip(source: str, value: object) -> str:
        from djust._rust import RustLiveView

        view = RustLiveView(source)
        view.set_state("p", value)
        return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()

    @pytest.mark.parametrize(("value", "attr"), CLOSED)
    def test_a_round_trip_preserves_the_attribute(self, value: object, attr: str) -> None:
        source = "{{ p.%s }}" % attr
        assert self._round_trip(source, value) == django_render(source, {"p": value})

    # Two rows — the NAIVE `tzinfo`s, whose value is Python `None` — used to be
    # excluded here, for a codec gap that was not this slot's: `Value::None`
    # and `Value::Missing` shared one msgpack `nil` (#2484). #2484 closed it, so
    # the exemption is REMOVED rather than left standing (#1859: a stale
    # exemption is a pin that can no longer go red). The sweep is now the whole
    # of `CLOSED`.

    def test_the_payload_is_what_carries_it(self) -> None:
        """Non-vacuity for the round trip: the blob names the tag AND the
        NINTH element is the map. Without reading the payload the assertions
        above would also pass on an implementation that kept the value alive
        some other way, which is the #2135 shape exactly."""
        msgpack = pytest.importorskip("msgpack")

        from djust._rust import RustLiveView

        view = RustLiveView("{{ p.days }}")
        view.set_state("p", datetime.timedelta(days=3, seconds=90, microseconds=5))
        blob = view.serialize_msgpack()
        assert b"__djust_encoded__" in blob
        payload = msgpack.unpackb(blob, raw=False, strict_map_key=False)[1]["p"][
            "__djust_encoded__"
        ]
        # TEN, and the two widenings that got it there are of different KINDS
        # — which is the whole of why they compose without either re-numbering
        # the other. #2485 grew the MAP at slot 8 and added no position;
        # #2477/#2489 widened slot 4 to a count and appended slot 9 for the
        # items. Both assertions below are kept, so a change that moved the
        # map's contents into a POSITION of their own fails here rather than
        # arriving as a plausible width.
        assert len(payload) == 11, payload  # ten until #2480 appended the class
        # The three DATA attributes, then the one AUTO-CALLED result (#2485) —
        # both producers write this ONE map, in table order, and the order is
        # what `values_structurally_equal`'s zipped compare reads.
        assert payload[8] == {
            "days": 3,
            "seconds": 90,
            "microseconds": 5,
            "total_seconds": 259290.000005,
        }
        assert list(payload[8]) == ["days", "seconds", "microseconds", "total_seconds"]
        # ...and the appended slot is the ITEMS, `None` for a `timedelta`.
        assert payload[9] is None

    def test_an_EIGHT_element_payload_still_reads_with_no_attributes(self) -> None:
        """A pre-#2481 process's state outlives it: a Redis backend hands back
        an eight-element payload on the first request after a rolling deploy.

        It restores to the answer that entry was WRITTEN with — no attributes,
        so `{{ p.days }}` renders empty exactly as it did — rather than half-way
        between. Built by TRUNCATING a real nine-element blob, so the test
        cannot drift from the shape the serializer writes.
        """
        msgpack = pytest.importorskip("msgpack")

        from djust._rust import RustLiveView

        view = RustLiveView("{{ p.days }}|{{ p }}")
        view.set_state("p", datetime.timedelta(days=3))
        decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
        payload = decoded[1]["p"]["__djust_encoded__"]
        assert len(payload) == 11, payload
        # A LEGACY payload is not a truncation of the current one (#2477/#2489):
        # slot 4 widened from #2466's `sized_empty` boolean to `len(o)` itself,
        # so every width below 10 carries a `Bool` there while a 10-element one
        # carries an int or `None`. Put the boolean back before truncating, or
        # the test measures that mismatch rather than the fallback it is named
        # for.
        payload[4] = False
        decoded[1]["p"]["__djust_encoded__"] = payload[:8]
        legacy = msgpack.packb(decoded, use_bin_type=True)

        assert RustLiveView.deserialize_msgpack(legacy).render() == "|3 days, 0:00:00"
        # And the CURRENT payload for the same value answers the new way, which
        # is what makes the arm above a compatibility read rather than a bug.
        assert self._round_trip("{{ p.days }}|{{ p }}", datetime.timedelta(days=3)) == (
            "3|3 days, 0:00:00"
        )

    def test_a_user_dict_under_the_tag_cannot_forge_an_encoded(self) -> None:
        """The map is the ninth slot and reads as a `Value::Object`, so the
        question "could a user dict be mistaken for a payload" is worth asking
        again. It cannot: a nine-element LIST is required, and a dict is not
        one."""
        pytest.importorskip("msgpack")

        from djust._rust import RustLiveView

        view = RustLiveView("{{ p.days }}")
        view.set_state("p", {"__djust_encoded__": {"days": 3}})
        out = RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()
        assert out == "", out


class TestWhatThisDeliberatelyDoesNOTClose:
    """Pinned as still-divergent so a stale exemption goes red (#1859)."""

    @pytest.mark.parametrize(("value", "attr"), CLASS_ATTRS)
    def test_a_class_attribute_still_renders_empty(self, value: object, attr: str) -> None:
        source = "{{ p.%s }}" % attr
        assert django_render(source, {"p": value}) != ""
        assert djust_render(source, {"p": value}) == ""

    def test_the_class_attributes_would_not_terminate(self) -> None:
        """The REASON, measured rather than asserted. `min` / `max` /
        `resolution` are values of the same family, so a collector that carried
        them would convert a value whose own `min` is a value of the same
        family, forever."""
        assert datetime.datetime.min.min is datetime.datetime.min
        assert datetime.date.max.max is datetime.date.max
        assert datetime.timedelta.resolution.resolution is datetime.timedelta.resolution

    @pytest.mark.parametrize(("value", "attr"), METHODS_RESULT_SPELLS_DIFFERENTLY)
    def test_a_nullary_method_whose_RESULT_spells_differently_renders_empty(
        self, value: object, attr: str
    ) -> None:
        """#2485 closed the auto-call for every name whose result djust spells
        the way Django spells it. These are the remainder, and the reason is
        the RESULT's own rendering, not the call: Django localizes a `date` /
        `time` / `datetime` and prints a `struct_time` as a namedtuple."""
        source = "{{ p.%s }}" % attr
        assert django_render(source, {"p": value}) != ""
        assert djust_render(source, {"p": value}) == ""

    @pytest.mark.parametrize(("value", "attr"), METHODS_RESULT_SPELLS_DIFFERENTLY)
    def test_carrying_the_result_would_swap_one_divergence_for_another(
        self, value: object, attr: str
    ) -> None:
        """The REASON, measured rather than asserted — the same shape as
        `test_the_class_attributes_would_not_terminate`.

        Render the call's RESULT through djust and compare it with what Django
        renders for the lookup. They differ, which is why the name is not in
        `ENCODED_CALL_NAMES`: carrying it would move the cell without closing
        it."""
        result = getattr(value, attr)()
        assert djust_render("{{ r }}", {"r": result}) != django_render(
            "{{ p.%s }}" % attr, {"p": value}
        )

    @pytest.mark.parametrize("attr", ["real", "imag", "as_tuple", "is_finite", "adjusted"])
    def test_a_decimals_attributes_are_a_DIFFERENT_carrier(self, attr: str) -> None:
        """`Decimal` is `Value::Decimal`, not `Value::Encoded` — a different
        variant with no attribute slot. Filed rather than folded in (#1079)."""
        source = "{{ p.%s }}" % attr
        value = decimal.Decimal("1.5")
        assert django_render(source, {"p": value}) != ""
        assert djust_render(source, {"p": value}) == ""

    def test_a_None_attribute_survives_the_state_round_trip_since_2484(self) -> None:
        """`Value::None` and `Value::Missing` are deliberately DISTINCT (#2203)
        — `None` renders `"None"`, `Missing` renders `""` — and the msgpack
        codec used to collapse them: both serialized as `nil` and `visit_unit`
        read every `nil` back as `Missing`.

        So a naive datetime's `tzinfo` rendered Django's `"None"` on the first
        render and `""` after one cache hit. This was pinned in the DIVERGING
        direction here, and #2484 closed it by tagging the RARE variant
        (`MISSING_TAG` on `Missing`, `None` keeps the bare `nil`) — so the pin
        is flipped rather than deleted.

        The gap was the CODEC's, not the attribute map's, and the second half
        of this test is what makes that a measurement: a PLAIN dict lost it
        too, with no `Encoded` involved at all.
        """
        assert django_render("{{ p.tzinfo }}", {"p": DT}) == "None"
        # Before the round trip, #2481's map answers Django's spelling.
        assert djust_render("{{ p.tzinfo }}", {"p": DT}) == "None"
        # And after it, since #2484.
        assert self_round_trip("{{ p.tzinfo }}", DT) == "None"

        # The same value with no `Encoded` in reach.
        from djust._rust import RustLiveView

        view = RustLiveView("{{ d.a }}")
        view.set_state("d", {"a": None})
        assert view.render() == django_render("{{ d.a }}", {"d": {"a": None}}) == "None"
        assert RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render() == "None"

    def test_a_custom_tzinfo_with_attributes_still_diverges(self) -> None:
        """The one carried attribute whose VALUE can be an arbitrary object.

        A `tzinfo` with a `__dict__` takes the `__dict__` bulk-dump arm of
        `FromPyObject`, so it arrives as a mapping rather than as its `str()`.
        That is the pre-existing behaviour of ANY such object placed in a
        context — reached here through one more door, not created by it — and
        it is the class #2478 is about.
        """

        class MyTz(datetime.tzinfo):
            def __init__(self) -> None:
                self.label = "custom"

            def utcoffset(self, dt):  # noqa: ANN001, ANN201
                return datetime.timedelta(0)

            def tzname(self, dt):  # noqa: ANN001, ANN201
                return "CUSTOM"

            def dst(self, dt):  # noqa: ANN001, ANN201
                return None

        aware = datetime.datetime(2026, 3, 4, tzinfo=MyTz())
        assert "MyTz object at" in django_render("{{ p.tzinfo }}", {"p": aware})
        assert (
            djust_render("{{ p.tzinfo }}", {"p": aware})
            == "{&#x27;label&#x27;: &#x27;custom&#x27;}"
        )
        # The same object placed DIRECTLY in the context answers identically,
        # which is what makes this pre-existing rather than introduced.
        assert djust_render("{{ p }}", {"p": MyTz()}) == "{&#x27;label&#x27;: &#x27;custom&#x27;}"


class TestTheSinkHasExactlyTheReadersItClaims:
    """Grep the SINK, and pin the reader SET rather than a floor (#1125).

    Both directions, with a canary that proves each can go red — a pin nobody
    has watched fail is a pin whose failure mode is unknown (#2129/#2135).
    """

    @staticmethod
    def _production(source: str) -> str:
        """Source with `//` comments and any `#[cfg(test)]` module removed. A
        pin that counts an occurrence in a COMMENT is the #2237 false alarm."""
        head = source.split("#[cfg(test)]", 1)[0]
        return "\n".join(line for line in head.splitlines() if not line.lstrip().startswith("//"))

    def test_lookup_segment_is_the_only_reader_of_the_attribute_map(self) -> None:
        """`Encoded::attrs` is read in exactly ONE place. Every other mention
        in the crate is the field, the two producers, the codec and the
        equality — none of which RESOLVE a name.

        The count is an equality so a SECOND reader reddens it as loudly as a
        deleted one: a second dotted-path walker that consults the map is the
        parallel-path shape (#1646) this fix exists to end, and one that does
        NOT consult it is the bug itself.
        """
        ctx = self._production(CONTEXT_RS.read_text(encoding="utf-8"))
        assert ctx.count(".attrs.get(") == 1, (
            "the attribute-map reader count moved in context.rs — a second "
            "walker consulting the map is #1646 again"
        )
        assert "fn lookup_segment" in ctx
        # And it is inside `lookup_segment`, not somewhere else in the file.
        body = ctx.split("fn lookup_segment", 1)[1].split("\n}\n", 1)[0]
        assert ".attrs.get(" in body, "the reader moved out of `lookup_segment`"

    def test_lookup_segment_has_exactly_one_caller(self) -> None:
        """The other half: `lookup_segment` serves `Context::get`, and
        `Context::get` is what `resolve` / `get_value_safe` / every operand
        channel funnel through. A second caller would be a second walk to keep
        in step."""
        ctx = self._production(CONTEXT_RS.read_text(encoding="utf-8"))
        calls = re.findall(r"(?<!fn )lookup_segment\(", ctx)
        assert len(calls) == 1, f"lookup_segment caller set moved: {len(calls)}"

    def test_the_counters_go_red_in_BOTH_directions(self) -> None:
        """The canary. Each mutation asserts it APPLIED before its count is
        read, so a no-op edit cannot report a passing number."""
        ctx = self._production(CONTEXT_RS.read_text(encoding="utf-8"))

        baseline = ctx.count(".attrs.get(")
        assert baseline == 1, baseline
        added = ctx.replace(".attrs.get(", ".attrs.get(  /*dup*/ ", 1) + "\n.attrs.get(x)\n"
        assert added != ctx, "the ADD mutation did not apply"
        assert added.count(".attrs.get(") == baseline + 1
        removed = ctx.replace(".attrs.get(", ".nothing.get(", 1)
        assert removed != ctx, "the REMOVE mutation did not apply"
        assert removed.count(".attrs.get(") == baseline - 1

        base_calls = len(re.findall(r"(?<!fn )lookup_segment\(", ctx))
        assert base_calls == 1, base_calls
        more = ctx + "\nlet _ = lookup_segment(v, p);\n"
        assert more != ctx, "the ADD mutation did not apply"
        assert len(re.findall(r"(?<!fn )lookup_segment\(", more)) == base_calls + 1
        fewer = ctx.replace("= lookup_segment(", "= other_segment(", 1)
        assert fewer != ctx, "the REMOVE mutation did not apply"
        assert len(re.findall(r"(?<!fn )lookup_segment\(", fewer)) == base_calls - 1

    def test_the_name_list_is_the_whole_of_the_policy(self) -> None:
        """One table, four types, and no `min` / `max` / `resolution` in it —
        the non-termination guard is structural rather than a comment."""
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        assert src.count("pub const ENCODED_ATTR_NAMES") == 1
        table = src.split("pub const ENCODED_ATTR_NAMES", 1)[1].split("];", 1)[0]
        for tp in ("datetime.datetime", "datetime.date", "datetime.time", "datetime.timedelta"):
            assert f'"{tp}"' in table, tp
        for banned in ('"min"', '"max"', '"resolution"'):
            assert banned not in table, (
                f"{banned} entered the attribute table — its value is a value of "
                "the same family, so the collection would not terminate"
            )
        # The collector is the only producer, and it is called once per type.
        assert src.count("fn collect_named_attrs(") == 1

    def test_the_CALL_list_is_the_whole_of_the_OTHER_half_of_the_policy(self) -> None:
        """The auto-call half (#2485) is a SECOND table with a SECOND producer,
        and it carries the same two structural guards as the first."""
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        assert src.count("pub const ENCODED_CALL_NAMES") == 1
        table = src.split("pub const ENCODED_CALL_NAMES", 1)[1].split("];", 1)[0]
        for tp in ("datetime.datetime", "datetime.date", "datetime.time", "datetime.timedelta"):
            assert f'"{tp}"' in table, tp
        for banned in ('"min"', '"max"', '"resolution"'):
            assert banned not in table, (
                f"{banned} entered the CALL table — it is a data attribute of the "
                "same family, so the collection would not terminate"
            )
        for banned in ('"now"', '"today"', '"utcnow"'):
            assert banned not in table, (
                f"{banned} entered the CALL table — its value is the CURRENT "
                "time, so the conversion would do nondeterministic work"
            )
        assert src.count("fn collect_called_attrs(") == 1

    def test_the_two_tables_are_disjoint(self) -> None:
        """Both producers write the SAME map, so a name in both would have the
        call's result overwrite the attribute's — a silent precedence rule
        nobody stated. Measured against live Python rather than read off the
        source: no name is both a data attribute and a callable here."""
        for value, data_names, call_names in (
            (
                DT,
                (
                    "year",
                    "month",
                    "day",
                    "hour",
                    "minute",
                    "second",
                    "microsecond",
                    "fold",
                    "tzinfo",
                ),
                (
                    "isoformat",
                    "ctime",
                    "weekday",
                    "isoweekday",
                    "toordinal",
                    "timestamp",
                    "utcoffset",
                    "tzname",
                    "dst",
                ),
            ),
            (
                DATE,
                ("year", "month", "day"),
                ("isoformat", "ctime", "weekday", "isoweekday", "toordinal"),
            ),
            (
                TIME,
                ("hour", "minute", "second", "microsecond", "fold", "tzinfo"),
                ("isoformat", "utcoffset", "tzname", "dst"),
            ),
            (TD, ("days", "seconds", "microseconds"), ("total_seconds",)),
        ):
            assert not set(data_names) & set(call_names), type(value)
            for n in data_names:
                assert not callable(getattr(value, n)), f"{type(value)}.{n} is callable"
            for n in call_names:
                assert callable(getattr(value, n)), f"{type(value)}.{n} is not callable"
