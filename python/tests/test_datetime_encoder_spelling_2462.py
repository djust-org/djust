"""Every datetime sink spells a value the way ``DjangoJSONEncoder`` does (#2462).

The claim, and what running it actually showed
----------------------------------------------
``normalize_django_value``'s docstring states an identity::

    json.dumps(normalize_django_value(v), cls=DjangoJSONEncoder)
        == json.dumps(v, cls=DjangoJSONEncoder)

and calls itself *"the pre-pass for DjangoJSONEncoder, not a replacement for
it"*.  The issue reported that 4 of 10 datetime shapes violate it, and the
measurement reproduces exactly.  What it does NOT show is where the defect is,
and that changes where the fix goes.

``DjangoJSONEncoder`` in ``djust/serialization.py`` is **djust's own** subclass
of ``json.JSONEncoder``, not ``django.core.serializers.json.DjangoJSONEncoder``
— and djust's spelled a datetime with a bare ``isoformat()`` too.  So against
the encoder the docstring actually names, the identity **held** for every one of
the four::

    datetime(µs=123456)   djust-enc direct "…05.123456"   djust-enc(normalized) "…05.123456"   ok
    datetime(tz=utc)      djust-enc direct "…05+00:00"    djust-enc(normalized) "…05+00:00"    ok

The issue's table was produced against *Django's* encoder — the one
``json_script`` and every Django-shaped consumer uses::

    value                 djust                DJANGO
    datetime(µs=123456)   "…03:04:05.123456"   "…03:04:05.123"
    datetime(µs=7)        "…03:04:05.000007"   "…03:04:05.000"
    datetime(tz=utc)      "…03:04:05+00:00"    "…03:04:05Z"
    time(µs=123456)       "03:04:05.123456"    "03:04:05.123"

So the defect is real and *wider* than reported: it is not the pre-pass
disagreeing with the encoder, it is **both of them disagreeing with Django**.
djust's encoder is the one that feeds the WebSocket frame (``websocket.py``),
the SSE stream (``sse.py``) and the HTTP-API body (``api/dispatch.py``), so the
divergence was on the wire as well as in the template context.

Fixing only ``normalize_django_value`` would have *created* the violation the
issue describes — the pre-pass spelling ``.123`` while the encoder it feeds
spelled ``.123456``.  Both had to move, plus a third sink the grep found:
``djust/template/serialization.py::serialize_value`` (#1646 — grep the SINK,
not the callers you already know).

Why the parity test could not have caught it
--------------------------------------------
``tests/unit/test_normalize_django_value.py::TestParityWithJSONRoundtrip``
exists to pin exactly this identity, and the issue diagnosed its blindness as a
sampling problem: every ``datetime``/``time`` in its 17-value list has
``microsecond == 0`` and no ``tzinfo`` — the one band where the two spellings
agree.

That is true, and it is not the load-bearing half.  The test imports
``DjangoJSONEncoder`` **from djust.serialization**, so it compared the pre-pass
against a copy of the same defect: a fully randomized sweep over every
microsecond and every offset would still have been **green**.  The axis it was
blind on is the *reference implementation*, not the values.  Widening the value
set without changing the reference would have left the class exactly as
reachable as before.

Both axes are closed here.  The value set is re-derived from the branches
``DjangoJSONEncoder.default`` actually has — ``o.microsecond`` truthiness,
``r.endswith("+00:00")``, ``is_aware(o)``, and ``duration_iso_string``'s sign /
day / microsecond splits — crossed rather than sampled, and swept randomly on
top; and every assertion runs against **Django's real encoder** as well as
djust's.

What this deliberately does NOT close
-------------------------------------
* **An aware ``time``.**  Django's ``default()`` raises
  ``ValueError: JSON can't represent timezone-aware times.``  djust keeps
  emitting, which is the more-permissive direction it takes for every
  unserialisable value (#2429, and the direction ``django_json_encoded`` takes
  by failing closed).  Adopting the raise would 500 renders that work today and
  is a refusal-class change, not a spelling one.
* **The LiveView path still FLATTENS.**  ``normalize_django_value`` turns a
  ``datetime`` into a ``str`` in Python, so Rust never sees the object and
  ``Value::Encoded`` (#2448) is never built there; ``{{ p }}`` renders that
  string rather than ``str(o)``.  Spelling it correctly is what #2462 asked for.
  Not flattening at all would change what every consumer of the function
  receives — the session round trip, the wire encoders and the JIT serializer
  all need a JSON-able value — and is filed separately rather than folded in
  (#1079).
"""

from __future__ import annotations

import datetime
import json
import pathlib
import random
import re

import pytest
from django.core.serializers.json import DjangoJSONEncoder as RealEncoder

from djust.serialization import DjangoJSONEncoder as DjustEncoder
from djust.serialization import django_json_datetime, normalize_django_value
from djust.template.serialization import serialize_value

SER_PY = pathlib.Path(__file__).resolve().parents[2] / "python" / "djust" / "serialization.py"
TSER_PY = (
    pathlib.Path(__file__).resolve().parents[2]
    / "python"
    / "djust"
    / "template"
    / "serialization.py"
)

UTC = datetime.timezone.utc

#: Every offset shape the `r.endswith("+00:00")` branch can see, including the
#: two near-misses a curated table skips: a `timezone(timedelta(0))` that is not
#: `timezone.utc` but formats identically, and `+00:01`, which ends in `0:00`
#: without ending in `+00:00`.
OFFSETS = {
    "naive": None,
    "utc-singleton": UTC,
    "utc-constructed": datetime.timezone(datetime.timedelta(0)),
    "plus-0001": datetime.timezone(datetime.timedelta(minutes=1)),
    "plus-0530": datetime.timezone(datetime.timedelta(hours=5, minutes=30)),
    "minus-0800": datetime.timezone(datetime.timedelta(hours=-8)),
    "minus-0001": datetime.timezone(datetime.timedelta(minutes=-1)),
}

#: `if o.microsecond:` is a truthiness test, so 0 is one branch and everything
#: else is the other — but the SLICE differs per type (`r[:23] + r[26:]` for a
#: datetime, `r[:12]` for a time), and a sub-millisecond value is where a
#: truncation shows as `.000` rather than as a shortening.
MICROSECONDS = [0, 1, 999, 1000, 123456, 999999]


def _datetimes() -> dict[str, datetime.datetime]:
    return {
        f"datetime µs={us} tz={tzname}": datetime.datetime(2020, 1, 1, 3, 4, 5, us, tzinfo=tz)
        for us in MICROSECONDS
        for tzname, tz in OFFSETS.items()
    }


def _times() -> dict[str, datetime.time]:
    return {
        f"time µs={us} tz={tzname}": datetime.time(3, 4, 5, us, tzinfo=tz)
        for us in MICROSECONDS
        for tzname, tz in OFFSETS.items()
    }


def _dates() -> dict[str, datetime.date]:
    return {
        "date ordinary": datetime.date(2020, 1, 1),
        "date epoch": datetime.date(1970, 1, 1),
        "date min": datetime.date.min,
        "date max": datetime.date.max,
    }


def _timedeltas() -> dict[str, datetime.timedelta]:
    return {
        "timedelta zero": datetime.timedelta(0),
        "timedelta +90s": datetime.timedelta(seconds=90),
        "timedelta -90s": datetime.timedelta(seconds=-90),
        "timedelta +1µs": datetime.timedelta(microseconds=1),
        "timedelta -1µs": datetime.timedelta(microseconds=-1),
        "timedelta +3d4h": datetime.timedelta(days=3, hours=4),
        "timedelta -3d": datetime.timedelta(days=-3),
        "timedelta exact day": datetime.timedelta(days=1),
        "timedelta max": datetime.timedelta.max,
        "timedelta min": datetime.timedelta.min,
    }


FAMILY: dict[str, object] = {**_datetimes(), **_times(), **_dates(), **_timedeltas()}


def _is_aware_time(value: object) -> bool:
    return isinstance(value, datetime.time) and value.utcoffset() is not None


def _dumps(value: object, encoder: type[json.JSONEncoder]) -> str:
    return json.dumps(value, cls=encoder)


class TestTheIdentityHoldsAgainstBOTHEncoders:
    """The docstring's identity, and the one it is only useful relative to.

    Three properties, because two of them were true before this fix and the
    third — the one that matters — was not:

    * **self** — the pre-pass agrees with djust's own encoder.  Held before
      (both spelled `isoformat()`); must still hold, or the pre-pass is lying
      to the encoder it feeds.
    * **reference** — djust's encoder agrees with DJANGO's.  Did NOT hold, and
      is the actual defect.
    * **composed** — the pre-pass's output, encoded by Django's encoder, equals
      Django's encoding of the input.  This is the issue's own measurement.
    """

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_prepass_agrees_with_djusts_own_encoder(self, name: str) -> None:
        value = FAMILY[name]
        assert _dumps(normalize_django_value(value), DjustEncoder) == _dumps(value, DjustEncoder)

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_djusts_encoder_agrees_with_djangos(self, name: str) -> None:
        value = FAMILY[name]
        if _is_aware_time(value):
            pytest.skip("Django REFUSES an aware time; the residue is pinned separately")
        assert _dumps(value, DjustEncoder) == _dumps(value, RealEncoder), name

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_composed_identity_holds_against_djangos_encoder(self, name: str) -> None:
        value = FAMILY[name]
        if _is_aware_time(value):
            pytest.skip("Django REFUSES an aware time; the residue is pinned separately")
        assert _dumps(normalize_django_value(value), RealEncoder) == _dumps(value, RealEncoder)

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_template_serializer_is_the_third_sink(self, name: str) -> None:
        """`djust/template/serialization.py::serialize_value` — found by
        grepping `isoformat()` rather than by listing known callers."""
        value = FAMILY[name]
        if isinstance(value, datetime.timedelta):
            pytest.skip("serialize_value has no timedelta branch; unchanged by this fix")
        if _is_aware_time(value):
            assert serialize_value(value) == value.isoformat()
            return
        assert serialize_value(value) == RealEncoder().default(value), name


class TestTheRandomizedDifferential:
    """The curated cross above samples the branches this fix is about; the sweep
    is what covers the axis nobody thought to name.

    Seeded, so a failure is reproducible, and the seed is in the message.
    """

    N = 3000
    SEED = 24620

    @staticmethod
    def _random_value(rng: random.Random) -> object:
        kind = rng.choice(["datetime", "date", "time", "timedelta"])
        us = rng.choice([0, rng.randrange(1, 1_000_000)])
        # Django accepts offsets strictly inside ±24h, at minute granularity.
        tz = rng.choice(
            [
                None,
                UTC,
                datetime.timezone(datetime.timedelta(minutes=rng.randrange(-1439, 1440))),
            ]
        )
        if kind == "date":
            return datetime.date.fromordinal(rng.randrange(1, datetime.date.max.toordinal() + 1))
        if kind == "time":
            return datetime.time(
                rng.randrange(24), rng.randrange(60), rng.randrange(60), us, tzinfo=tz
            )
        if kind == "timedelta":
            return datetime.timedelta(
                days=rng.randrange(-4000, 4000),
                seconds=rng.randrange(-86400, 86400),
                microseconds=rng.randrange(-1_000_000, 1_000_000),
            )
        return datetime.datetime(
            rng.randrange(1, 10000),
            rng.randrange(1, 13),
            rng.randrange(1, 29),
            rng.randrange(24),
            rng.randrange(60),
            rng.randrange(60),
            us,
            tzinfo=tz,
        )

    def test_every_sampled_value_agrees_with_django(self) -> None:
        rng = random.Random(self.SEED)
        checked = 0
        skipped_aware_times = 0
        for _ in range(self.N):
            value = self._random_value(rng)
            if _is_aware_time(value):
                skipped_aware_times += 1
                continue
            expected = _dumps(value, RealEncoder)
            assert _dumps(value, DjustEncoder) == expected, (
                f"seed={self.SEED} value={value!r}: djust's encoder disagrees"
            )
            assert _dumps(normalize_django_value(value), RealEncoder) == expected, (
                f"seed={self.SEED} value={value!r}: the pre-pass disagrees"
            )
            checked += 1
        # Non-vacuity: the sweep has to have actually run, and to have reached
        # the aware-time branch often enough for the skip to be a real skip
        # rather than a silent zero.
        assert checked > self.N * 0.8, checked
        assert skipped_aware_times > 0, "the sweep never built an aware time"

    def test_the_sweep_reaches_the_branches_it_claims_to(self) -> None:
        """A sweep that never produced a microsecond value, or never produced a
        UTC-equivalent offset, would report the same green as a fixed one — so
        the branch coverage is asserted rather than assumed.
        """
        rng = random.Random(self.SEED)
        saw = {"us": 0, "no_us": 0, "z": 0, "offset": 0, "naive": 0, "negative_delta": 0}
        for _ in range(self.N):
            value = self._random_value(rng)
            if isinstance(value, datetime.timedelta):
                saw["negative_delta"] += value < datetime.timedelta(0)
                continue
            if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
                continue
            saw["us" if value.microsecond else "no_us"] += 1
            offset = value.utcoffset()
            if offset is None:
                saw["naive"] += 1
            elif offset == datetime.timedelta(0):
                saw["z"] += 1
            else:
                saw["offset"] += 1
        for key, count in saw.items():
            assert count > 0, f"the sweep never reached {key}: {saw}"


class TestTheEncoderIsCalledAndNotTranscribed:
    """`django_json_datetime` calls Django's `default()`.

    A hand port would have to reproduce three rules the issue's own body got
    partly wrong: the datetime slice pair `r[:23] + r[26:]`, the DIFFERENT time
    slice `r[:12]` (the issue quoted the datetime rule for both), and
    `duration_iso_string`'s negative normalisation.
    """

    def test_the_helper_is_the_encoder(self) -> None:
        for value in FAMILY.values():
            if _is_aware_time(value):
                continue
            assert django_json_datetime(value) == RealEncoder().default(value), repr(value)

    def test_the_time_slice_is_not_the_datetime_slice(self) -> None:
        """The transcription error a re-implementation invites, made concrete.

        `r[:23] + r[26:]` applied to a `time` would keep the whole string —
        `"03:04:05.123456"` is 15 characters, so `r[:23]` is all of it. The
        encoder's `r[:12]` is what actually truncates.
        """
        value = datetime.time(3, 4, 5, 123456)
        raw = value.isoformat()
        assert raw[:23] + raw[26:] == raw, "the datetime rule is a no-op on a time"
        assert raw[:12] == "03:04:05.123"
        assert django_json_datetime(value) == "03:04:05.123"

    def test_a_sub_millisecond_datetime_truncates_to_zeroes_not_to_nothing(self) -> None:
        """`microsecond=7` is truthy, so the branch runs and the result is
        `.000` — a value a "strip trailing zeroes" re-implementation would get
        wrong in the other direction."""
        value = datetime.datetime(2020, 1, 1, 3, 4, 5, 7)
        assert django_json_datetime(value) == "2020-01-01T03:04:05.000"

    def test_a_plus_0001_offset_is_not_rewritten(self) -> None:
        """The near-miss for `r.endswith("+00:00")`: `+00:01` ends in `0:00`
        without ending in the whole suffix, so it must survive."""
        value = datetime.datetime(
            2020, 1, 1, 3, 4, 5, tzinfo=datetime.timezone(datetime.timedelta(minutes=1))
        )
        assert django_json_datetime(value) == "2020-01-01T03:04:05+00:01"

    def test_a_constructed_utc_is_rewritten_like_the_singleton(self) -> None:
        """The rule is on the rendered SUFFIX, not on `tzinfo is timezone.utc`,
        so a separately-constructed zero offset gets `Z` too."""
        for tz in (UTC, datetime.timezone(datetime.timedelta(0))):
            value = datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=tz)
            assert django_json_datetime(value) == "2020-01-01T03:04:05Z", tz


class TestTheSinkSetIsPinned:
    """Grep the SINK, and pin the caller SET rather than a floor (#1125), with a
    canary proving each direction can go red (#2129/#2135).

    The set is three: djust's own encoder, the LiveView pre-pass, and the
    template serializer. A fourth `isoformat()` on a value of this family would
    be a fourth spelling.
    """

    EXPECTED_CALLERS = 3

    @staticmethod
    def _count(source: str, needle: str) -> int:
        return len(re.findall(re.escape(needle), source))

    @staticmethod
    def _production(source: str) -> str:
        """Source with `#` comment lines and docstring bodies dropped, so a
        mention in prose is not counted as a call (#2237)."""
        out, in_doc = [], False
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.count('"""') == 1:
                in_doc = not in_doc
                continue
            if in_doc or stripped.startswith("#"):
                continue
            out.append(line)
        return "\n".join(out)

    def _sources(self) -> str:
        return self._production(SER_PY.read_text(encoding="utf-8")) + self._production(
            TSER_PY.read_text(encoding="utf-8")
        )

    def test_the_helper_has_exactly_the_callers_it_claims(self) -> None:
        src = self._sources()
        # The definition plus the three call sites.
        assert self._count(src, "django_json_datetime(") == self.EXPECTED_CALLERS + 1, src.count(
            "django_json_datetime("
        )

    def test_no_sink_spells_a_datetime_with_isoformat_any_more(self) -> None:
        """The pre-fix spelling, pinned so it cannot come back.

        `isoformat()` survives in ONE place — the aware-`time` branch inside the
        helper, which is the documented refusal residue — and nowhere else in
        either module.
        """
        src = self._sources()
        assert self._count(src, ".isoformat()") == 1, [
            line for line in src.splitlines() if ".isoformat()" in line
        ]
        assert "return value.isoformat()" in src

    def test_the_counter_goes_red_in_BOTH_directions(self) -> None:
        src = self._sources()
        baseline = self._count(src, "django_json_datetime(")
        assert baseline == self.EXPECTED_CALLERS + 1, baseline

        added = src.replace(
            "return django_json_datetime(obj)",
            "return django_json_datetime(django_json_datetime(obj))",
            1,
        )
        assert added != src, "the ADD mutation did not apply"
        assert self._count(added, "django_json_datetime(") == baseline + 1

        removed = src.replace("return django_json_datetime(obj)", "return obj.isoformat()", 1)
        assert removed != src, "the REMOVE mutation did not apply"
        assert self._count(removed, "django_json_datetime(") == baseline - 1


class TestThreeTestsWereBlindOnComplementaryAxes:
    """Why this survived, run rather than argued.

    THREE tests existed that each had half of what was needed, and the halves
    were complementary — which is why a green suite meant nothing:

    ============================================== ========= ==========
    test                                            values    reference
    ============================================== ========= ==========
    `TestParityWithJSONRoundtrip` (unit)            narrow    djust's
    `TestEncoderMatchesRealDjango` (#2239)          narrow    DJANGO's
    `TestDjangoJSONEncoderTypes` (template)         wide-ish  neither
    ============================================== ========= ==========

    The first is the one #2462's issue names, and it diagnoses the failure as
    the value sampling. That is true and it is the half that does not matter:
    with the REFERENCE held at djust's own encoder, every value in the space
    agrees, so widening alone leaves it green. The second had the right
    reference and the same three narrow values (`microsecond == 0`, no
    `tzinfo`). The third asserted a hand-written literal that was not either
    encoder's answer.

    All three are widened by this change. The measurement below is what proves
    the first one's blindness was the reference and not the values.
    """

    def test_widening_the_values_alone_would_have_left_it_green(self) -> None:
        """The load-bearing measurement of this whole issue.

        Runs the ORIGINAL parity assertion — both sides encoded by djust's own
        encoder — over a randomized sweep spanning every microsecond and every
        offset, against the PRE-FIX spelling. Zero failures: a fully randomized
        widening of that test would not have caught the defect it exists to
        catch.

        The pre-fix spelling is reconstructed as `isoformat()` rather than
        checked out, so the assertion is about the two SPELLINGS and needs no
        second build.
        """
        rng = random.Random(24620)
        naive_agreements = 0
        django_disagreements = 0
        for _ in range(2000):
            value = TestTheRandomizedDifferential._random_value(rng)
            if isinstance(value, datetime.timedelta):
                continue
            pre_fix = value.isoformat()
            # (A) the original test's shape: pre-pass vs encoder, both pre-fix.
            # Both sides were `isoformat()`, so they agree for every value.
            naive_agreements += pre_fix == value.isoformat()
            # (B) the same value against DJANGO's answer.
            if _is_aware_time(value):
                continue
            django_disagreements += pre_fix != RealEncoder().default(value)

        assert naive_agreements > 1000, naive_agreements
        assert django_disagreements > 100, (
            "the sweep did not reach values where the two spellings differ, so "
            "it cannot demonstrate anything"
        )

    def test_the_second_test_had_the_reference_and_missed_the_values(self) -> None:
        """`TestEncoderMatchesRealDjango` (#2239) compares against Django's own
        encoder — the right reference — and its datetime rows were
        `datetime(2024,6,15,12,30,45)`, `date(...)`, `time(8,0,0)`.

        Every one has `microsecond == 0` and no `tzinfo`, so it passed
        throughout. Pinned mechanically: that class must now sample both.
        """
        source = (
            pathlib.Path(__file__).resolve().parent / "test_decimal_converters_2239.py"
        ).read_text(encoding="utf-8")
        block = source.split("def test_every_other_shared_type_still_matches_django", 1)[0]
        block = block.split("class TestEncoderMatchesRealDjango", 1)[1]
        assert "123456" in block, "no sampled datetime carries microseconds"
        assert "tzinfo=" in block, "no sampled datetime carries a tzinfo"
        assert "timedelta(" in block, "timedelta is still outside the sweep"


class TestWhatThisDeliberatelyDoesNOTClose:
    """Pinned as still-divergent so a stale exemption goes red (#1859)."""

    @pytest.mark.parametrize("us", [0, 123456])
    def test_an_aware_time_still_emits_where_django_refuses(self, us: int) -> None:
        value = datetime.time(3, 4, 5, us, tzinfo=UTC)
        with pytest.raises(ValueError):
            RealEncoder().default(value)
        # djust stays permissive (#2429), with the pre-fix spelling.
        assert django_json_datetime(value) == value.isoformat()
        assert normalize_django_value(value) == value.isoformat()
        assert json.loads(_dumps(value, DjustEncoder)) == value.isoformat()

    def test_the_liveview_path_still_flattens_before_rust_sees_it(self) -> None:
        """`Value::Encoded` (#2448) is never built on this path, so `{{ p }}`
        renders the FLATTENED string rather than `str(o)`.

        Spelling that string correctly is what this fix does; not flattening at
        all is a larger change to what every consumer of the function receives.
        """
        flattened = normalize_django_value({"p": datetime.datetime(2020, 1, 1, 3, 4, 5)})["p"]
        assert isinstance(flattened, str), type(flattened)
        assert flattened != str(datetime.datetime(2020, 1, 1, 3, 4, 5))

    def test_the_bare_render_spelling_of_an_aware_datetime_changed(self) -> None:
        """The cost, stated rather than discovered later.

        On the LiveView path `{{ p }}` renders the pre-pass's string, so an
        aware datetime now renders `…Z` where it rendered `…+00:00`. Both
        already diverged from Django, which LOCALIZES a bare datetime
        (`Jan. 1, 2020, 3:04 a.m.`); this moves one non-Django spelling to
        another and buys exact JSON parity.
        """
        from djust import _rust

        value = datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=UTC)
        assert normalize_django_value(value) == "2020-01-01T03:04:05Z"
        # And the string still parses as the same instant, so every date filter
        # downstream is unaffected — which is the half that would have been a
        # regression rather than a cost.
        assert _rust.render_template(
            '{{ p|date:"Y-m-d H:i:s e" }}', {"p": normalize_django_value(value)}
        ) == _rust.render_template(
            '{{ p|date:"Y-m-d H:i:s e" }}', {"p": "2020-01-01T03:04:05+00:00"}
        )
