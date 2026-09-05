"""`{{ dt.isoformat }}` renders — a `Value::Encoded` carries the auto-called half (#2485).

The divergence
--------------
Django's ``Variable._resolve_lookup`` AUTO-CALLS a callable attribute
(ADR-024), so ``{{ p.isoformat }}`` is an EVALUATION where ``{{ p.year }}`` is a
lookup. #2481 gave ``Value::Encoded`` a map of the LOOKUP half and left the
call half open::

    {{ p.isoformat }}      datetime    django '2026-03-04T05:06:07.000008'   djust ''
    {{ p.total_seconds }}  timedelta   django '259290.000005'                djust ''
    {{ p.utcoffset }}      aware dt    django '0:00:00'                      djust ''

``<time datetime="{{ obj.created.isoformat }}">`` is an ordinary Django idiom
and it rendered nothing.

The membership rule is a MEASUREMENT, and the issue's list was wrong both ways
----------------------------------------------------------------------------
The issue named twelve methods. Sweeping ``dir(o)`` on live objects and
comparing three columns per name — what Django renders for ``{{ p.<name> }}``,
what djust renders, and what djust renders for the call's RESULT — says the
issue's list both **omits** members (``isoweekday``) and **includes** members
that carrying would not close (``date``, ``time``, ``timetuple``).

The rule that falls out of the sweep is not "nullary and cheap"; it is *carrying
the result makes djust render what Django renders*. Every dropped name is
dropped because its result is itself a ``date`` / ``time`` / ``datetime`` /
``struct_time``, whose BARE render already differs from Django's — Django
LOCALIZES. Carrying those would swap one divergence for another:

===================== ================================= ============================
name                  Django renders                    the result renders as
===================== ================================= ============================
``isoformat``         ``2026-03-04T05:06:07.000008``    the same — CARRIED
``date``              ``March 4, 2026``                 ``2026-03-04`` — DROPPED
``timetuple``         ``time.struct_time(tm_year=…)``   ``(2026, 3, 4, …)`` — DROPPED
===================== ================================= ============================

``now`` / ``today`` / ``utcnow`` are dropped for a second reason on top: their
value is the CURRENT time, so carrying them would do nondeterministic work at
every conversion. A method that needs ARGUMENTS (``strftime``, ``combine``)
needs neither entry nor exclusion — Django's auto-call catches the ``TypeError``
and renders ``string_if_invalid``, which is the empty string djust already
renders.

What this deliberately does NOT close
-------------------------------------
The CLASS-attribute half of #2485 — ``min`` / ``max`` / ``resolution`` — stays
open and stays pinned in ``test_encoded_attributes_2481.py``. They are data
attributes whose values are values of the same family
(``datetime.min.min is datetime.min``), so collecting them does not terminate;
closing them needs a depth bound, which is a design decision rather than three
more strings.

Every expectation is LIVE Django and LIVE Python, never a transcription.

Refs #2485, #2481, #2484, #2448, ADR-024.
"""

from __future__ import annotations

import datetime
import pathlib
import zoneinfo

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE_RS = REPO / "crates" / "djust_core" / "src" / "lib.rs"


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


def djust_render_with_dirs(source: str, context: dict) -> str:
    """The OTHER entry point `DjustTemplateBackend` binds."""
    try:
        return _rust.render_template_with_dirs(source, dict(context), [])
    except Exception:  # noqa: BLE001
        return "<<REFUSED>>"


def liveview_render(source: str, context: dict) -> str:
    """The LiveView path — `RustLiveView` with the raw-Python sidecar attached.

    The FOURTH render path. It has had `Context::resolve`'s lazy `getattr`
    walk since ADR-024, so it answered several of the cells this file pins as
    empty all along; #2501 attached the same sidecar to the three entry points
    measured here. Used below to state that convergence as a measurement
    rather than as a claim (#1646).
    """
    try:
        view = _rust.RustLiveView(source, [])
        view.set_raw_py_values(dict(context))
        return view.render()
    except Exception:  # noqa: BLE001
        return "<<REFUSED>>"


def round_trip(source: str, value: object) -> str:
    """Render after a msgpack state round trip — what the default
    `InMemoryStateBackend` does on every read."""
    from djust._rust import RustLiveView

    view = RustLiveView(source)
    view.set_state("p", value)
    return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()


DT = datetime.datetime(2026, 3, 4, 5, 6, 7, 8)
DATE = datetime.date(2026, 3, 4)
TIME = datetime.time(5, 6, 7, 8)
TD = datetime.timedelta(days=3, seconds=90, microseconds=5)
AWARE = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc)
ZONED = datetime.datetime(2026, 3, 4, tzinfo=zoneinfo.ZoneInfo("America/New_York"))
ZONED_TIME = datetime.time(5, 6, 7, tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))

#: The whole of `ENCODED_CALL_NAMES`, per subject. `(label, value, attr)`.
CLOSED = [
    *[
        pytest.param(DT, a, id=f"datetime-{a}")
        for a in (
            "isoformat",
            "ctime",
            "weekday",
            "isoweekday",
            "toordinal",
            "timestamp",
            "utcoffset",
            "tzname",
            "dst",
        )
    ],
    *[
        pytest.param(DATE, a, id=f"date-{a}")
        for a in ("isoformat", "ctime", "weekday", "isoweekday", "toordinal")
    ],
    *[pytest.param(TIME, a, id=f"time-{a}") for a in ("isoformat", "utcoffset", "tzname", "dst")],
    *[pytest.param(TD, a, id=f"timedelta-{a}") for a in ("total_seconds",)],
    # The AWARE members, where `utcoffset` / `tzname` / `dst` answer a real
    # value rather than `None` — the branch a naive subject never reaches.
    *[
        pytest.param(AWARE, a, id=f"aware-{a}")
        for a in ("isoformat", "utcoffset", "tzname", "dst", "timestamp")
    ],
    *[
        pytest.param(ZONED, a, id=f"zoned-{a}")
        for a in ("isoformat", "utcoffset", "tzname", "dst", "timestamp")
    ],
]


class TestTheCitedDivergenceIsClosed:
    @pytest.mark.parametrize(("value", "attr"), CLOSED)
    def test_the_auto_called_method_renders_exactly_as_django_renders_it(
        self, value: object, attr: str
    ) -> None:
        source = "{{ p.%s }}" % attr
        assert djust_render(source, {"p": value}) == django_render(source, {"p": value})

    @pytest.mark.parametrize(("value", "attr"), CLOSED)
    def test_the_OTHER_entry_point_answers_the_same(self, value: object, attr: str) -> None:
        """`DjustTemplateBackend` binds two. They are one sink
        (`Context::get` -> `lookup_segment`), and asserting both is what keeps
        that a measurement rather than a claim (#1646)."""
        source = "{{ p.%s }}" % attr
        assert djust_render_with_dirs(source, {"p": value}) == django_render(source, {"p": value})

    @pytest.mark.parametrize(("value", "attr"), CLOSED)
    def test_a_state_round_trip_preserves_it(self, value: object, attr: str) -> None:
        """The map crosses msgpack on every read of the default state backend.
        A value that renders on the first render and not after one cache hit is
        the nondeterminism #2484 was about."""
        source = "{{ p.%s }}" % attr
        assert round_trip(source, value) == django_render(source, {"p": value})

    def test_the_idiom_from_the_issue(self) -> None:
        """The cell a template actually writes."""
        source = '<time datetime="{{ p.isoformat }}">{{ p.year }}</time>'
        assert djust_render(source, {"p": DT}) == django_render(source, {"p": DT})
        assert "2026-03-04T05:06:07.000008" in djust_render(source, {"p": DT})

    def test_a_nested_lookup_reaches_it(self) -> None:
        """`{{ post.published.isoformat }}` — the shape #2481 exists for, with
        the auto-called half at the end of the path."""
        source = "{{ d.published.isoformat }}"
        ctx = {"d": {"published": DT}}
        assert djust_render(source, ctx) == django_render(source, ctx)
        assert djust_render(source, ctx) == "2026-03-04T05:06:07.000008"


class TestTheMembershipRuleIsMeasured:
    """The rule is "carrying the result renders what Django renders", and both
    halves of it are measured against live objects rather than asserted."""

    @pytest.mark.parametrize(("value", "attr"), CLOSED)
    def test_the_carried_value_is_the_calls_own_result(self, value: object, attr: str) -> None:
        """Not derived from any string the carrier already holds — the
        `repr`-cloned-from-`display` shape (#2472) a PR nearly shipped."""
        result = getattr(value, attr)()
        assert djust_render("{{ p.%s }}" % attr, {"p": value}) == djust_render(
            "{{ r }}", {"r": result}
        )

    def test_a_method_needing_arguments_needs_no_entry(self) -> None:
        """Django's auto-call catches the `TypeError` and renders
        `string_if_invalid`. That is the empty string djust already renders for
        a name it does not carry, so these cells agree WITHOUT an exclusion —
        which is why they are absent from the table rather than listed in it."""
        for attr in ("strftime", "combine", "fromisoformat", "fromordinal", "strptime"):
            source = "{{ p.%s }}" % attr
            assert django_render(source, {"p": DT}) == ""
            assert djust_render(source, {"p": DT}) == ""

    def test_the_nondeterministic_classmethods_are_not_carried(self) -> None:
        """`now` / `today` / `utcnow` answer the CURRENT time. Django renders
        them; carrying them in the MAP would do nondeterministic work at every
        conversion — for every value, whether or not any template spells the
        name — AND still spell the result differently. That exclusion is
        unchanged, and this is its pin.

        It was pinned in the DIVERGING direction ("djust renders empty") until
        #2501, and went red there — the pin working (#1859). The cell is now
        answered by the sidecar walk, which is LAZY: the nondeterministic call
        runs only for a template that actually spells `{{ p.now }}`, which is
        precisely the objection the map could not escape. Django calls it too,
        so this is parity on WHETHER; the result's spelling is the remaining
        divergence and is measured in `test_encoded_attributes_2481.py`.
        """
        for attr in ("now", "today", "utcnow"):
            source = "{{ p.%s }}" % attr
            assert django_render(source, {"p": DT}) != ""
            # The name is still NOT in the map: a value that has been through
            # the state round trip has no live object to walk, so the cell is
            # empty there. That is what shows the render below comes from the
            # sidecar rather than from a widened table.
            assert round_trip(source, DT) == ""
            # And with the live object in reach, the call runs.
            assert djust_render(source, {"p": DT}) != ""
            assert liveview_render(source, {"p": DT}) != ""


class TestTheCallsFailSoft:
    """A call runs code the framework does not own. A raising one must leave
    the cell where it was — empty — rather than take the whole value back to
    its pre-#2448 string path or raise out of the conversion."""

    @staticmethod
    def _partly_hostile() -> datetime.datetime:
        """A tzinfo whose `utcoffset` WORKS and whose `tzname` / `dst` RAISE.

        The choice is load-bearing rather than fussy. A tzinfo whose
        `utcoffset` raises takes `str(o)` down with it — `datetime.__str__`
        calls `isoformat()`, which calls `utcoffset()` — so the value never
        becomes an `Encoded` at all and the terminal
        `Ok(Value::String(ob.str()?))` propagates the error out of the whole
        conversion. That is PRE-EXISTING behaviour of any object with a raising
        `__str__` and has nothing to do with this table; a test using it would
        measure that instead of this. Measured, not assumed: see
        `test_a_raising___str___is_a_DIFFERENT_pre_existing_path` below.
        """

        class PartlyHostile(datetime.tzinfo):
            def utcoffset(self, dt):  # noqa: ANN001, ANN201
                return datetime.timedelta(0)

            def tzname(self, dt):  # noqa: ANN001, ANN201
                raise RuntimeError("no")

            def dst(self, dt):  # noqa: ANN001, ANN201
                raise RuntimeError("no")

        return datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=PartlyHostile())

    def test_a_raising_call_is_skipped_and_the_rest_of_the_value_answers(self) -> None:
        value = self._partly_hostile()
        # The two raising calls are SKIPPED by the map's collector, so the
        # cell it produces stays where it was — measured after the state round
        # trip, where the map is the only thing in reach.
        assert round_trip("{{ p.tzname }}", value) == ""
        assert round_trip("{{ p.dst }}", value) == ""
        # With the live object in reach, the sidecar walk calls it and the
        # exception propagates — which is `Context::maybe_call`'s documented
        # rule ("any other exception raised by the method propagates as a
        # render error, matching Django") and is what Django does here too:
        # `test_the_skipped_cell_is_one_django_500s_on` below measures that.
        # Pinned as a REFUSAL on both engines rather than as a djust-only one
        # (#2501 moved this cell; before it, djust was more permissive).
        assert djust_render("{{ p.tzname }}", {"p": value}) == "<<REFUSED>>"
        assert djust_render("{{ p.dst }}", {"p": value}) == "<<REFUSED>>"
        assert liveview_render("{{ p.tzname }}", {"p": value}) == "<<REFUSED>>"
        # Everything else about the value still answers — the failure is
        # per-name, not per-value.
        assert djust_render("{{ p.isoformat }}", {"p": value}) == "2026-03-04T05:06:07+00:00"
        assert djust_render("{{ p.utcoffset }}", {"p": value}) == "0:00:00"
        assert djust_render("{{ p.year }}", {"p": value}) == "2026"
        # Bare rendering follows Django localization even when unrelated
        # methods on the timezone raise.
        expected = django_render("{{ p }}", {"p": value})
        assert expected != "<<REFUSED>>"
        assert djust_render("{{ p }}", {"p": value}) == expected

    def test_the_skipped_cell_is_one_django_500s_on(self) -> None:
        """Non-vacuity: djust renders EMPTY where Django RAISES, so the
        fail-soft skip is MORE permissive than Django rather than a new
        refusal — the direction the cell was already in before #2485, and the
        direction to fail in."""
        value = self._partly_hostile()
        assert django_render("{{ p.tzname }}", {"p": value}) == "<<REFUSED>>"
        assert django_render("{{ p.dst }}", {"p": value}) == "<<REFUSED>>"

    def test_a_raising___str___is_a_DIFFERENT_pre_existing_path(self) -> None:
        """The measurement behind the fixture above, recorded rather than
        reasoned about. When `utcoffset` raises, `str(o)` raises with it and
        the value takes the terminal string arm, whose `?` propagates — so BOTH
        engines refuse, on every cell, with or without this table."""

        class WhollyHostile(datetime.tzinfo):
            def utcoffset(self, dt):  # noqa: ANN001, ANN201
                raise RuntimeError("no")

            def tzname(self, dt):  # noqa: ANN001, ANN201
                raise RuntimeError("no")

            def dst(self, dt):  # noqa: ANN001, ANN201
                raise RuntimeError("no")

        value = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=WhollyHostile())
        with pytest.raises(RuntimeError):
            str(value)
        # djust refuses on EVERY cell, including `{{ p.year }}` — a #2481 DATA
        # attribute this table does not touch. That is what shows the refusal
        # belongs to the conversion (the terminal `Value::String(ob.str()?)`
        # arm, whose `?` propagates) rather than to the auto-call.
        for source in ("{{ p }}", "{{ p.year }}", "{{ p.tzname }}"):
            assert djust_render(source, {"p": value}) == "<<REFUSED>>", source
        # Django refuses only where IT calls the raising code: `{{ p }}` is
        # `str(o)` and `{{ p.tzname }}` is the auto-call, but `{{ p.year }}`
        # never touches the tzinfo. So this is a pre-existing
        # djust-REFUSES-where-Django-RENDERS cell of the CONVERSION's, unmoved
        # by #2485 and recorded here rather than left to be rediscovered.
        assert django_render("{{ p }}", {"p": value}) == "<<REFUSED>>"
        assert django_render("{{ p.tzname }}", {"p": value}) == "<<REFUSED>>"
        assert django_render("{{ p.year }}", {"p": value}) == "2026"

    def test_an_aware_time_is_not_an_Encoded_so_no_name_reaches_it(self) -> None:
        """`DjangoJSONEncoder.default` RAISES for a timezone-aware `time`
        (`JSON can't represent timezone-aware times`), so `django_json_encoded`
        fails closed above both tables and the value keeps its
        `Value::String(str(o))` path — pinned since #2471.

        This table changes nothing there, and the pin says so: the DATA half
        (#2481) renders empty for the same reason, which is what makes this the
        conversion's decision rather than this table's omission."""
        assert djust_render("{{ p }}", {"p": ZONED_TIME}) == "05:06:07-05:00"
        # Neither table answers this value — measured after the state round
        # trip, where no live object is left to fall back to.
        assert round_trip("{{ p.hour }}", ZONED_TIME) == ""  # the #2481 half
        assert round_trip("{{ p.isoformat }}", ZONED_TIME) == ""  # this half
        # #2501: with the live object in reach the sidecar answers both, in
        # Django's spelling, WITHOUT either table gaining an entry — the
        # argument for resolving lazily against the object rather than by
        # widening a carrier.
        assert django_render("{{ p.isoformat }}", {"p": ZONED_TIME}) == "05:06:07-05:00"
        assert djust_render("{{ p.isoformat }}", {"p": ZONED_TIME}) == "05:06:07-05:00"
        assert djust_render("{{ p.hour }}", {"p": ZONED_TIME}) == "5"
        assert liveview_render("{{ p.isoformat }}", {"p": ZONED_TIME}) == "05:06:07-05:00"

    def test_a_subclass_gets_the_builtins_names(self) -> None:
        """`django_json_encoded` keys the tables off the `tp_name` it already
        resolved, not off the subclass's `__name__` — a `datetime` subclass has
        a `datetime`'s methods."""

        class MyDT(datetime.datetime):
            pass

        value = MyDT(2026, 3, 4, 5, 6, 7, 8)
        assert djust_render("{{ p.isoformat }}", {"p": value}) == django_render(
            "{{ p.isoformat }}", {"p": value}
        )

    def test_an_OVERRIDING_subclass_is_answered_by_the_object(self) -> None:
        """The call is made on the live object, so a subclass that overrides
        the method is answered by ITS method — which is what Django's auto-call
        does too."""

        class Shouty(datetime.date):
            def isoformat(self):  # noqa: ANN201
                return "SHOUTY"

        value = Shouty(2026, 3, 4)
        assert djust_render("{{ p.isoformat }}", {"p": value}) == "SHOUTY"
        assert djust_render("{{ p.isoformat }}", {"p": value}) == django_render(
            "{{ p.isoformat }}", {"p": value}
        )


class TestTheReaderIsStillTheOnlyOne:
    """Both producers write ONE map, so `lookup_segment` stays the one reader —
    a second resolution path for the same question is #1646."""

    @staticmethod
    def _production(source: str) -> str:
        head = source.split("#[cfg(test)]", 1)[0]
        return "\n".join(line for line in head.splitlines() if not line.lstrip().startswith("//"))

    def test_the_call_table_has_exactly_one_producer_and_one_merge(self) -> None:
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        assert src.count("fn collect_called_attrs(") == 1
        # Declared once, read once — at the merge in `django_json_encoded`.
        assert src.count("ENCODED_CALL_NAMES") == 2, (
            "the call table gained a second reader — a second place deciding "
            "which names the auto-call reaches is #1646"
        )

    def test_the_counter_goes_red_in_BOTH_directions(self) -> None:
        """The canary. Each mutation asserts it APPLIED before its count is
        read, so a no-op edit cannot report a passing number (#2129/#2135)."""
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        baseline = src.count("ENCODED_CALL_NAMES")
        assert baseline == 2, baseline
        more = src + "\nlet _ = ENCODED_CALL_NAMES;\n"
        assert more != src, "the ADD mutation did not apply"
        assert more.count("ENCODED_CALL_NAMES") == baseline + 1
        fewer = src.replace("ENCODED_CALL_NAMES", "OTHER_NAMES", 1)
        assert fewer != src, "the REMOVE mutation did not apply"
        assert fewer.count("ENCODED_CALL_NAMES") == baseline - 1
