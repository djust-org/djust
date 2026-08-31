"""``json_script`` spells a datetime VALUE the way ``DjangoJSONEncoder`` does (#2448).

The defect
----------
``django.utils.html.json_script`` is ``json.dumps(value, cls=DjangoJSONEncoder)``
and that encoder's ``default()`` is not ``str()``.  djust reached it with the
value already spelled ``str(o)`` — the TEMPLATE DISPLAY form — because
``FromPyObject for Value`` landed a ``datetime`` on its final
``Ok(Value::String(ob.str()?))`` fallback::

    {{ p|json_script:"d" }}     p = {"a": datetime(2020, 1, 1, 3, 4, 5)}

    django  {"a": "2020-01-01T03:04:05"}
    djust   {"a": "2020-01-01 03:04:05"}

Not cosmetic: ``"2020-01-01 03:04:05"`` is not ISO-8601, and ``"0:01:30"`` is
not an ISO-8601 duration, so client code parsing the ``<script>`` body gets a
string it cannot feed to ``Date.parse``.

What the re-derivation added to the issue's table
-------------------------------------------------
The issue tabulated ``time`` as AGREEING (✓).  It agrees only at
``microsecond == 0``, which is the band the report sampled::

    p = time(3, 4, 5, 123456)   django "03:04:05.123"   djust "03:04:05.123456"

``DjangoJSONEncoder`` truncates microseconds to MILLISECONDS for both
``datetime`` and ``time`` — the same coincidence-in-the-sampled-band shape as
#2425's float keys, one axis over.  ``date`` is the only member of the family
that agrees for every value, and it is carried by the fix anyway so the type set
is a SET rather than a list of the members that happened to diverge.

Two more rows the issue did not have, both found by running the encoder rather
than reading it:

* ``timedelta(seconds=-90)`` — ``str()`` normalises to ``-1 day, 23:58:30``
  while ``duration_iso_string`` gives ``-P0DT00H01M30S``;
* ``datetime(..., microsecond=123456)`` — the truncation above.

Why this is decidable where #2429 was not
------------------------------------------
#2429 (djust emits where ``json.dumps`` REFUSES) was declined because the value
position cannot see the type: for every value Django refuses, djust's output is
byte-identical to its output for a serialisable stand-in.  The erasure is real,
but it is a CHOICE MADE AT THE CONVERSION, not a property of the boundary — the
``Decimal`` arm three lines up reads the type with an ``isinstance``.  So the
fix stops discarding the type rather than trying to reconstruct it downstream:
``Value::Encoded`` carries ``str(o)``, ``DjangoJSONEncoder.default(o)`` and
CPython's ``tp_name``.

The KEY position is untouched and still diverges — that IS #2429's refusal
question — and is pinned as still-divergent below so this file cannot be read as
claiming more than it closed.

Which PATH this is on
---------------------
Every case here uses ``render_template(tpl, raw_dict)`` — the raw PyO3
conversion, which ``djust/template/backend.py`` takes, so it is what a plain
Django view rendering through ``DjustTemplateBackend`` gets. The LiveView path
runs its context through ``normalize_django_value`` first, which flattens a
``datetime`` to an ISO string in PYTHON, so ``Value::Encoded`` is never built
there. That path was already mostly right — and only mostly.
:class:`TestWhichPathThisFixIsOn` measures both and pins the four shapes the
Python pre-pass still gets wrong.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import datetime
import decimal
import json
import pathlib
import random
import re
import uuid

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

TPL = '{{ p|json_script:"d" }}'

CORE_RS = pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_core" / "src" / "lib.rs"
FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)


def django_render(src: str, ctx: dict) -> str:
    return DjangoTemplate(src).render(DjangoContext(dict(ctx)))


def djust_render(src: str, ctx: dict) -> str:
    return _rust.render_template(src, dict(ctx))


def body_of(script_html: str) -> str:
    """The JSON document inside the ``<script>`` element."""
    return script_html.split(">", 1)[1].rsplit("</script>", 1)[0]


#: One value per shape the encoder spells differently, plus the two members of
#: the family that agree — carried so the set is the TYPE set and not the
#: divergent set.
FAMILY: dict[str, object] = {
    "datetime naive": datetime.datetime(2020, 1, 1, 3, 4, 5),
    "datetime microseconds": datetime.datetime(2020, 1, 1, 3, 4, 5, 123456),
    "datetime microseconds tiny": datetime.datetime(2020, 1, 1, 3, 4, 5, 7),
    "datetime utc": datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=datetime.timezone.utc),
    "datetime offset": datetime.datetime(
        2020, 1, 1, 3, 4, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    ),
    "date": datetime.date(2020, 1, 1),
    "time": datetime.time(3, 4, 5),
    "time microseconds": datetime.time(3, 4, 5, 123456),
    "timedelta seconds": datetime.timedelta(seconds=90),
    "timedelta days": datetime.timedelta(days=2, seconds=3),
    "timedelta negative": datetime.timedelta(seconds=-90),
    "timedelta microseconds": datetime.timedelta(seconds=1, microseconds=500),
}

#: The two rows the issue's table got right that this fix must not disturb:
#: ``DjangoJSONEncoder.default`` returns ``str(o)`` for both, and djust already
#: agreed because it was sending ``str(o)``.
ALREADY_AGREED: dict[str, object] = {
    "Decimal": decimal.Decimal("1.10"),
    "UUID": uuid.UUID("12345678-1234-5678-1234-567812345678"),
}


class TestTheValuePositionAgreesForTheWholeFamily:
    """The headline, as a differential rather than as literals."""

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_rendered_script_is_djangos_byte_for_byte(self, name: str) -> None:
        value = FAMILY[name]
        ctx = {"p": {"a": value}}
        d, r = django_render(TPL, ctx), djust_render(TPL, ctx)
        assert r == d, f"{name}: django={d!r} djust={r!r}"

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_body_parses_and_carries_the_encoders_string(self, name: str) -> None:
        """Not merely equal to Django — equal to what the ENCODER produces.

        Equality with Django would still pass if both engines were wrong in the
        same way, which is the shape #2340's note about `pprint` warns about.
        This reads `DjangoJSONEncoder.default` directly.
        """
        value = FAMILY[name]
        parsed = json.loads(body_of(djust_render(TPL, {"p": {"a": value}})))
        assert parsed == {"a": DjangoJSONEncoder().default(value)}

    @pytest.mark.parametrize("name", sorted(ALREADY_AGREED))
    def test_the_rows_that_already_agreed_still_do(self, name: str) -> None:
        ctx = {"p": {"a": ALREADY_AGREED[name]}}
        assert djust_render(TPL, ctx) == django_render(TPL, ctx)

    def test_the_time_row_the_issue_called_agreeing_actually_diverged(self) -> None:
        """The correction, asserted rather than only narrated.

        `time(3, 4, 5)` agrees under `str()` and `time(3, 4, 5, 123456)` does
        not — so a fix scoped to "datetime and timedelta", which is what the
        issue's table would have licensed, would have left a live divergence
        one microsecond away.
        """
        assert str(datetime.time(3, 4, 5)) == DjangoJSONEncoder().default(datetime.time(3, 4, 5))
        witness = datetime.time(3, 4, 5, 123456)
        assert str(witness) != DjangoJSONEncoder().default(witness)
        assert DjangoJSONEncoder().default(witness) == "03:04:05.123"


class TestTheSweepRatherThanTheTable:
    """A curated table samples one axis and blinds you on the next (v1.0.0rc4).

    Django, and its encoder, are a subprocess-free call away, so "what does the
    reference actually do" is answered by running it over a few thousand random
    values rather than by three per type.
    """

    @staticmethod
    def _random_values(rng: random.Random, n: int) -> list[object]:
        out: list[object] = []
        for _ in range(n):
            kind = rng.randrange(4)
            if kind == 0:
                out.append(
                    datetime.datetime(
                        rng.randrange(1, 9999),
                        rng.randrange(1, 13),
                        rng.randrange(1, 29),
                        rng.randrange(24),
                        rng.randrange(60),
                        rng.randrange(60),
                        rng.choice([0, 1, 7, 999, 1000, 123456, 999999]),
                        tzinfo=rng.choice(
                            [
                                None,
                                datetime.timezone.utc,
                                datetime.timezone(datetime.timedelta(hours=rng.randrange(-12, 13))),
                                datetime.timezone(
                                    datetime.timedelta(minutes=rng.randrange(-60, 61))
                                ),
                            ]
                        ),
                    )
                )
            elif kind == 1:
                out.append(
                    datetime.date(
                        rng.randrange(1, 9999), rng.randrange(1, 13), rng.randrange(1, 29)
                    )
                )
            elif kind == 2:
                # NAIVE only: an aware `time` makes the encoder raise, which is
                # the refusal axis #2429 declined — see the residue class below.
                out.append(
                    datetime.time(
                        rng.randrange(24),
                        rng.randrange(60),
                        rng.randrange(60),
                        rng.choice([0, 1, 7, 999, 1000, 123456, 999999]),
                    )
                )
            else:
                out.append(
                    datetime.timedelta(
                        days=rng.randrange(-4000, 4000),
                        seconds=rng.randrange(-86400, 86400),
                        microseconds=rng.choice([0, 1, 7, 999, 1000, 123456, 999999]),
                    )
                )
        return out

    def test_three_thousand_random_values_agree_with_django(self) -> None:
        rng = random.Random(2448)
        values = self._random_values(rng, 3000)
        bad = []
        for value in values:
            ctx = {"p": {"a": value}}
            d, r = django_render(TPL, ctx), djust_render(TPL, ctx)
            if d != r:
                bad.append((value, d, r))
        assert not bad, f"{len(bad)}/{len(values)} differ, first three: {bad[:3]}"

    def test_the_sweep_reaches_every_member_of_the_family(self) -> None:
        """Non-vacuity: a sweep that only ever built dates proves nothing about
        timedeltas."""
        rng = random.Random(2448)
        kinds = {type(v).__name__ for v in self._random_values(rng, 3000)}
        assert kinds == {"datetime", "date", "time", "timedelta"}, kinds


class TestTheDisplayPositionIsUnchanged:
    """The half a fix that moved `Display` would have broken.

    djust's bare render of a datetime is `str(o)` and Django's is a LOCALIZED
    date format (`Jan. 1, 2020, 3:04 a.m.`) — a divergence that predates #2448
    and is a separate defect.  What matters here is that `Value::Encoded`
    renders exactly what `Value::String(str(o))` rendered, so this fix moves the
    JSON spelling and nothing else.
    """

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_bare_render_is_still_pythons_str(self, name: str) -> None:
        value = FAMILY[name]
        assert djust_render("{{ p }}", {"p": value}) == str(value)

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_string_filters_still_see_the_str(self, name: str) -> None:
        """`@stringfilter` coerces, so `|upper` and friends operate on `str(o)`
        on BOTH engines — the property that makes an `Encoded` a string
        everywhere except `json_script`.

        `|slice` is deliberately NOT here: it is not a `@stringfilter`, it
        catches the `TypeError` and returns the datetime UNCHANGED, so Django
        then renders it through its localizing `{{ }}` path (`3:04 a.m.`).
        That divergence is djust's locale-blind bare render, which predates
        this fix; asserting agreement there would assert something Django does
        not do.
        """
        value = FAMILY[name]
        for tail in ("|upper", "|lower", "|length", "|make_list", "|wordcount", "|striptags"):
            src = "{{ p%s }}" % tail
            assert djust_render(src, {"p": value}) == django_render(src, {"p": value}), tail


class TestTheStateRoundTripKeepsTheEncoderSpelling:
    """The half the `Decimal` version of this shipped without (#2214/#2135).

    ``SerializableViewState.state`` round-trips through msgpack on every read of
    the default state backend, so an UNTAGGED ``Encoded`` would come back as a
    ``Value::String`` holding the display spelling and reopen the whole defect
    after one cache hit.  ``ENCODED_TAG`` is what stops that, and this is the
    test that would have caught its absence.
    """

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_a_value_survives_the_binary_round_trip(self, name: str) -> None:
        """Both directions at once: the tag has to be written on the way in and
        recognised on the way out.  #2214 shipped with an encode-only assertion
        that stayed green through exactly this gap (#2135)."""
        from djust._rust import RustLiveView

        value = FAMILY[name]
        view = RustLiveView(TPL)
        view.set_state("p", {"a": value})
        restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert restored.render() == django_render(TPL, {"p": {"a": value}}), name

    def test_the_tag_is_what_carries_it(self) -> None:
        """Non-vacuity for the tag: the msgpack blob names it.

        Without this the round-trip above would also pass on an implementation
        that happened to keep the value alive some other way, and the tag could
        be deleted with the suite green.
        """
        from djust._rust import RustLiveView

        view = RustLiveView(TPL)
        view.set_state("p", {"a": datetime.timedelta(seconds=90)})
        assert b"__djust_encoded__" in view.serialize_msgpack()

    def test_the_untagged_shape_is_what_the_tag_prevents(self) -> None:
        """The failure this guards, constructed rather than described.

        A `Value::Encoded` serialized as a bare string comes back as a
        `Value::String` holding the DISPLAY spelling — which renders the
        pre-#2448 bytes.  The two spellings differ, so the round-trip
        assertion above is discriminating.
        """
        value = datetime.timedelta(seconds=90)
        assert str(value) == "0:01:30"
        assert DjangoJSONEncoder().default(value) == "P0DT00H01M30S"
        untagged = djust_render(TPL, {"p": {"a": str(value)}})
        assert json.loads(body_of(untagged)) == {"a": "0:01:30"}


class TestTheTypeNameIsCPythonsOwn:
    """`tp_name`, which is not `__name__` and not `__module__ + __qualname__`.

    Measured against CPython: a static C type carries its DOTTED name into a
    `TypeError` and a Python-level SUBCLASS carries the bare `__qualname__`.
    """

    @pytest.mark.parametrize(
        "value,expected",
        [
            (datetime.datetime(2020, 1, 1), "datetime.datetime"),
            (datetime.date(2020, 1, 1), "datetime.date"),
            (datetime.time(1, 2), "datetime.time"),
            (datetime.timedelta(seconds=1), "datetime.timedelta"),
        ],
    )
    def test_the_builtin_names_are_the_dotted_ones(self, value, expected: str) -> None:
        # CPython's own message, read from CPython.
        with pytest.raises(TypeError) as exc:
            value[0]
        assert expected in str(exc.value), str(exc.value)
        # And the name djust reaches for the same value.
        with pytest.raises(RuntimeError) as djust_exc:
            djust_render("{{ p|first }}", {"p": value})
        assert expected in str(djust_exc.value), str(djust_exc.value)

    def test_a_subclass_carries_its_bare_name(self) -> None:
        """The half a hardcoded name table would have got wrong — and the half
        `__qualname__` gets wrong too.

        `tp_name` for a heap type is the name the class was CREATED with, so a
        class defined inside this method is `MyMoment` while its `__qualname__`
        is `TestTheTypeNameIsCPythonsOwn.test_….<locals>.MyMoment`. The
        expectation is read from CPython here rather than written down, which is
        how the first version of this — `PyType::qualname()` — was caught.
        """

        class MyMoment(datetime.datetime):
            pass

        assert MyMoment.__name__ != MyMoment.__qualname__, "the nesting witness went flat"
        value = MyMoment(2020, 1, 1, 3, 4, 5)
        with pytest.raises(TypeError) as exc:
            value[0]
        cpython_message = str(exc.value)
        assert cpython_message == "'MyMoment' object is not subscriptable", cpython_message
        with pytest.raises(RuntimeError) as djust_exc:
            djust_render("{{ p|first }}", {"p": value})
        assert cpython_message in str(djust_exc.value), str(djust_exc.value)

    def test_a_subclass_still_gets_the_encoders_spelling(self) -> None:
        """`isinstance`, not an identity check, so a subclass is claimed."""

        class MyDelta(datetime.timedelta):
            pass

        value = MyDelta(seconds=90)
        ctx = {"p": {"a": value}}
        assert djust_render(TPL, ctx) == django_render(TPL, ctx)
        assert '"P0DT00H01M30S"' in djust_render(TPL, ctx)


class TestWhichPathThisFixIsOn:
    """djust has TWO ways into the renderer, and this fix is on one of them.

    * **Raw** — `render_template(tpl, {"p": dt})`. The Python object crosses
      the PyO3 boundary intact; `djust/template/backend.py` takes this path, so
      it is what a plain Django view rendering through `DjustTemplateBackend`
      gets. `Value::Encoded` exists for this path, and every case above is on
      it.
    * **Normalized** — the LiveView path, which runs the context through
      `djust.serialization.normalize_django_value` first. That converts a
      `datetime` to an ISO string IN PYTHON, so Rust never sees a datetime and
      `Value::Encoded` is never constructed.

    On the normalized path `json_script` was already mostly right before this
    fix, because the normalizer is a `DjangoJSONEncoder` pre-pass — but only
    mostly, and the gap was the same two rules #2448 is about: it did not
    truncate microseconds to milliseconds and did not rewrite a trailing
    `+00:00` to `Z`, so the identity

        json.dumps(normalize_django_value(v), cls=Enc) == json.dumps(v, cls=Enc)

    was FALSE for 4 of 10 datetime shapes against DJANGO's encoder. Filed as
    #2462 rather than fixed here — Python, a different file, a different path —
    and **closed** there. The four rows below are kept and INVERTED rather than
    deleted, so the file records which path each fix was on.

    One thing #2462 established that this file's original wording missed:
    `Enc` above has to be Django's encoder for the claim to be false at all.
    Against djust's OWN `DjangoJSONEncoder` — which is what
    `djust/serialization.py` means by that name, and which spelled a datetime
    with a bare `isoformat()` too — the identity held for all four. The defect
    was both of them disagreeing with Django, not the pre-pass disagreeing with
    the encoder.

    The LiveView path used to FLATTEN, which neither fix changed — pinned
    below so that closing it would redden the pin rather than leave a stale
    claim. #2467 closed it: the normalizer now carries the value unconverted,
    `Value::Encoded` is constructed on both paths, and the pin is INVERTED
    rather than deleted (the way #2462's four rows above were).
    """

    #: The claim, run rather than read.
    @staticmethod
    def _identity_holds(value: object) -> bool:
        from djust.serialization import normalize_django_value

        return json.dumps(normalize_django_value(value), cls=DjangoJSONEncoder) == json.dumps(
            value, cls=DjangoJSONEncoder
        )

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_raw_path_is_the_one_this_fix_closes(self, name: str) -> None:
        ctx = {"p": {"a": FAMILY[name]}}
        assert djust_render(TPL, ctx) == django_render(TPL, ctx)

    def test_the_normalizer_carries_the_type_since_2467(self) -> None:
        """**Inverted by #2467**, which is what this class existed to enable.

        It asserted the flattening — `isinstance(flattened, str)` — precisely
        so that closing the LiveView path would redden it rather than leave a
        stale claim. The normalizer now carries the value UNCONVERTED (the
        `Decimal` split, #2239), so `Value::Encoded` is built on both paths.
        """
        from djust.serialization import normalize_django_value

        value = FAMILY["datetime naive"]
        carried = normalize_django_value({"p": value})["p"]
        assert carried is value, type(carried)

    @pytest.mark.parametrize(
        "name",
        [
            "datetime microseconds",
            "datetime microseconds tiny",
            "datetime utc",
            "time microseconds",
        ],
    )
    def test_the_normalized_path_agrees_on_these_four_since_2462(self, name: str) -> None:
        """The residue, CLOSED (#2462) — inverted rather than deleted.

        Each was a `DjangoJSONEncoder` rule the Python pre-pass did not apply.
        All three sinks that spelled a datetime — djust's own encoder,
        `normalize_django_value`, and `template/serialization.py::serialize_value`
        — now call `django_json_datetime`, which calls Django's `default()`.
        """
        assert self._identity_holds(FAMILY[name]), (
            f"{name} diverges again — #2462 closed it, so this going red means "
            "a sink went back to `isoformat()`"
        )

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_and_holds_for_the_WHOLE_family_since_2462(self, name: str) -> None:
        """The residue is now zero rather than four, so the sweep is the whole
        family rather than the five that happened to agree."""
        value = FAMILY[name]
        if isinstance(value, datetime.time) and value.utcoffset() is not None:
            pytest.skip("Django REFUSES an aware time; #2429's declined direction")
        assert self._identity_holds(value)

    def test_the_parity_test_now_samples_the_values_AND_the_reference(self) -> None:
        """Why it survived, and what closing it actually required (#2462).

        `tests/unit/test_normalize_django_value.py::TestParityWithJSONRoundtrip`
        exists to pin this identity. Its original 17-value list gave every
        `datetime`/`time` `microsecond == 0` and no `tzinfo` — the one band
        where the two spellings agree — which is the
        coincidence-in-the-sampled-band that made #2448's own issue table mark
        `time` as agreeing, one layer up.

        But the sampling was NOT the load-bearing blindness, and asserting only
        that it widened would pin the wrong thing. That test imported
        `DjangoJSONEncoder` **from djust.serialization**, so it compared the
        pre-pass against a copy of the same defect: measured, 3,923 randomized
        values spanning every microsecond and every offset produce ZERO
        failures of the same-encoder assertion. Widening the values alone would
        have left it green.

        So this pin asserts BOTH axes moved: the list samples microseconds and
        tzinfo, and the class runs at least one assertion against Django's own
        encoder.
        """
        source = (
            pathlib.Path(__file__).resolve().parents[2]
            / "tests"
            / "unit"
            / "test_normalize_django_value.py"
        ).read_text(encoding="utf-8")
        block = source.split("class TestParityWithJSONRoundtrip", 1)[1]
        params = block.split("def test_scalar_parity", 1)[0]
        assert "datetime(" in params, "the parity list moved — update this pin"
        # The VALUE axis: at least one sampled datetime carries microseconds (a
        # 7th positional) and at least one carries a tzinfo.
        assert any(
            len(call.split(",")) >= 7
            for call in re.findall(r"(?:datetime|time)\(([^)]*)\)", params)
        ), "no sampled datetime/time carries microseconds"
        assert "tzinfo=" in params, "no sampled datetime carries a tzinfo"
        # The REFERENCE axis, which is the one that matters.
        assert "django.core.serializers.json" in source, (
            "the parity class no longer compares against DJANGO's encoder — "
            "which is the axis it was blind on, and the reason a widened value "
            "set alone would still have been green"
        )
        assert "_encoded_by_django" in block


class TestWhatThisDeliberatelyDoesNOTClose:
    """Pinned as still-divergent so a stale exemption goes red (#1859)."""

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_KEY_position_still_emits_where_django_refuses(self, name: str) -> None:
        """#2429's refusal question, untouched.

        `json.dumps` raises `TypeError: keys must be str, int, float, bool or
        None` for a datetime KEY and djust emits its `str()`.  That is the
        MORE-permissive direction djust takes for every unserialisable value,
        and closing it for keys alone would make the two positions disagree —
        the reasoning #2429 was declined on, unchanged by this fix.
        """
        value = FAMILY[name]
        ctx = {"p": {value: "x"}}
        with pytest.raises(TypeError):
            django_render(TPL, ctx)
        assert djust_render(TPL, ctx).startswith("<script")

    def test_an_aware_time_still_emits_because_the_helper_fails_closed(self) -> None:
        """`DjangoJSONEncoder.default` RAISES for a timezone-aware `time`
        (`JSON can't represent timezone-aware times.`).

        `django_json_encoded` fails closed on any error, so such a value takes
        its pre-#2448 path — `Value::String(str(o))` — and djust keeps emitting
        where Django 500s.  That is the refusal direction, out of this fix's
        scope, and it is asserted rather than left as a surprise.
        """
        value = datetime.time(3, 4, 5, tzinfo=datetime.timezone.utc)
        with pytest.raises(ValueError):
            DjangoJSONEncoder().default(value)
        with pytest.raises(ValueError):
            django_render(TPL, {"p": {"a": value}})
        out = djust_render(TPL, {"p": {"a": value}})
        assert json.loads(body_of(out)) == {"a": "03:04:05+00:00"}

    def test_a_zero_timedelta_agrees_since_2458_closed_it(self) -> None:
        """CLOSED by #2458 — kept, inverted, rather than deleted.

        `bool(timedelta(0))` is `False` in Python and Django, and was `True`
        here — before #2448 as well as after, because the value was a non-empty
        `Value::String("0:00:00")` then and `Value::Encoded::is_truthy`
        deliberately kept that answer (`!display.is_empty()`), a truthiness
        change being no business of a JSON-spelling fix.

        #2458 gave `Encoded` a fourth field, `bool(o)` asked at the conversion,
        which `is_truthy` now reads. This row moved from
        `TestWhatThisDeliberatelyDoesNOTClose` to agreement; it stays in this
        file because the exemption it replaces was stated here.
        """
        src = "{% if p %}T{% else %}F{% endif %}"
        assert bool(datetime.timedelta(0)) is False
        assert django_render(src, {"p": datetime.timedelta(0)}) == "F"
        assert djust_render(src, {"p": datetime.timedelta(0)}) == "F"
        # Non-vacuity: every OTHER member of the family is truthy in Python for
        # every value, so a fix that answered "falsy" for the variant outright
        # would pass the assertion above and be wrong here.
        for value in (
            datetime.timedelta(seconds=1),
            datetime.time(0, 0),
            datetime.date(2020, 1, 1),
            datetime.datetime(2020, 1, 1),
        ):
            assert bool(value) is True, repr(value)
            assert django_render(src, {"p": value}) == "T", repr(value)
            assert djust_render(src, {"p": value}) == "T", repr(value)

    def test_pprint_shows_the_constructor_form_since_2472(self) -> None:
        """CLOSED by #2472, and kept here inverted rather than deleted.

        This class pinned `|pprint` as a divergence #2448 did not come to fix,
        on the reasoning that `Value::Encoded` carries `str()` and the
        encoder's JSON *and not* `repr()`. #2472 answered that by putting
        `repr(o)` on the variant, so the reasoning's premise moved and the
        divergence went with it. Asserting the NEW answer here is what keeps
        this file's "deliberately not closed" list honest — a stale pin on a
        closed divergence is worse than no pin.

        Full coverage lives in
        `python/tests/test_encoded_value_position_2471_2472_2473.py`.
        """
        value = datetime.datetime(2020, 1, 1, 3, 4, 5)
        assert repr(value) == "datetime.datetime(2020, 1, 1, 3, 4, 5)"
        assert djust_render("{{ p|pprint }}", {"p": value}) == repr(value)
        assert django_render("{{ p|pprint }}", {"p": value}) == repr(value)


class TestTheSinkHasExactlyTheCallersItClaims:
    """Grep the SINK, and pin the caller SET rather than a floor (#1125).

    Both directions: an ADDED caller fails because the set grows, and a REMOVED
    one fails because it shrinks.  The canary below proves each direction can
    actually go red rather than asserting that it can.
    """

    #: `django_json_encoded` is called from exactly one place — the fallback
    #: block of `FromPyObject for Value`. One conversion, so one call.
    EXPECTED_CONVERSION_CALLS = 1

    #: `Value::Encoded` must be READ by `value_to_json` (the fix) and by
    #: `python_type_name` (which is what #2449's refusals spell the type with).
    #: Every other arm treats it as its display string.
    @staticmethod
    def _call_count(source: str, name: str) -> int:
        return len(re.findall(r"(?<![\w:])%s\s*\(" % re.escape(name), source))

    @staticmethod
    def _production(source: str) -> str:
        """Source with `//` comments and the test module removed.

        A pin that counts a call named in a COMMENT is the #2237 false alarm,
        and one that counts a call in `#[cfg(test)]` measures the test rather
        than the engine.
        """
        head = source.split("#[cfg(test)]", 1)[0]
        return "\n".join(line for line in head.splitlines() if not line.lstrip().startswith("//"))

    def test_the_conversion_calls_the_helper_exactly_once(self) -> None:
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        # The definition, the CONVERSION's one call site, and — since
        # #2477/#2489 — `crosses_as_encoded`, which has to ask the same
        # question about the same objects because it stands in for the whole
        # block. That predicate is the ONLY other caller, and asking the helper
        # rather than re-stating which types it claims is exactly why it can be.
        assert self._call_count(src, "django_json_encoded") == 3, src.count("django_json_encoded")
        assert "if let Some(encoded) = django_json_encoded(&ob.to_owned())" in src
        # ...and it is one call each, in two functions, not two in one.
        for fn in ("fn extract(", "pub fn crosses_as_encoded("):
            body = src.split(fn, 1)[1].split("\n}\n", 1)[0]
            assert self._call_count(body, "django_json_encoded") == 1, (fn, body)

    def test_the_counter_goes_red_in_BOTH_directions(self) -> None:
        """The canary #2129/#2135 asks for: a pin nobody has watched fail is a
        pin whose failure mode is unknown.

        Each mutation asserts it APPLIED before its count is read, so a
        no-op edit cannot report a passing number.
        """
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        baseline = self._call_count(src, "django_json_encoded")
        assert baseline == 3, baseline

        added = src.replace(
            "if let Some(encoded) = django_json_encoded(&ob.to_owned())",
            "if let Some(encoded) = django_json_encoded(&ob.to_owned())\n"
            "            .or_else(|| django_json_encoded(&ob.to_owned()))",
            1,
        )
        assert added != src, "the ADD mutation did not apply"
        assert self._call_count(added, "django_json_encoded") == baseline + 1

        removed = src.replace(
            "if let Some(encoded) = django_json_encoded(&ob.to_owned())",
            "if let Some(encoded) = None::<Encoded>",
            1,
        )
        assert removed != src, "the REMOVE mutation did not apply"
        assert self._call_count(removed, "django_json_encoded") == baseline - 1

    def test_value_to_json_reads_the_encoder_field_and_nothing_else_does(self) -> None:
        """`e.json` is read in exactly one place in the whole engine.

        If a second site starts reading it, the encoder spelling has two
        consumers and they can drift — which is the #1646 shape this codebase
        keeps paying for.
        """
        filters = self._production(FILTERS_RS.read_text(encoding="utf-8"))
        core = self._production(CORE_RS.read_text(encoding="utf-8"))
        # `filters.rs`: the one `value_to_json` arm, and nothing else.
        assert filters.count("e.json") == 1, filters.count("e.json")
        assert 'format!("\\"{}\\"", json_string_body(&e.json))' in filters
        # `lib.rs`: exactly one, the binary tag's payload. The tag TRANSPORTS
        # the spelling; it does not decide it, and the round-trip test above is
        # what proves the two agree.
        assert core.count("e.json") == 1, core.count("e.json")
        assert "e.json.as_str()" in core
