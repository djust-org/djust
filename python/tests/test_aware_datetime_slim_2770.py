"""An aware datetime converts in ~12 µs, not ~21 — the nested values are slim (#2770).

The finding (#2740's measurement)
---------------------------------
An aware ``datetime`` cost 21.5 µs/object to convert against 9.0 µs naive.
~11 µs of the difference was three NESTED conversions inside
``django_json_encoded``'s name tables: ``utcoffset()`` and ``dst()`` each
return a ``timedelta`` that went through the full ``str`` / ``repr`` /
``DjangoJSONEncoder`` / ``bool`` / ``isinstance`` sweep (~3.7 µs apiece), and
``tzinfo`` went through ``opaque_value`` on the ``ZoneInfo`` (~3 µs) — of which
every reader consumed one string and one bit.

The shape chosen, and why the issue's "carry as seconds" was not it
--------------------------------------------------------------------
The readers decide the carried form (#1646: one representation, every reader
on it), and they were enumerated before the shape was picked:

=============== ============================================== ==========================
name            reader                                         needs
=============== ============================================== ==========================
``utcoffset``   ``context::lookup_segment`` ``{{ p.utcoffset }}``  Django's ``str(td)`` — ``9:00:00``
``utcoffset``   ``{{ p.utcoffset.total_seconds }}``            the nested map
``dst``         ``filters::format_date`` ``|date:"I"`` (tz-pinned) ``is_truthy()``
``dst``         ``lookup_segment`` / ``{% if p.dst %}``        ``str(td)`` and ``bool(td)``
``tzinfo``      ``lookup_segment`` ``{{ p.tzinfo }}``           Django's ``str(tz)``
``tzinfo``      ``Encoded::temporal_object`` (state restore)   ``str(tz)`` as a ``ZoneInfo`` key
``tzname``      ``format_date`` ``T``/``e``; ``temporal_object`` a string — unchanged
=============== ============================================== ==========================

A plain-seconds scalar for ``utcoffset`` / ``dst`` would render ``32400`` where
Django renders ``9:00:00`` (pinned in ``test_nullary_autocall_2485.py``) and
would make ``{% if p.dst %}`` wrong for a ``"0:00:00"`` string. So the two
``timedelta``s keep their EXACT ``Encoded`` — built from the three limbs in
Rust (``slim_timedelta_encoded``) instead of through the interpreter, byte for
byte the same payload — and only ``tzinfo`` changes shape, to ``str(tz)``.

What this file pins
-------------------
1. The nested ``utcoffset`` / ``dst`` payload is BYTE-EQUAL to the full-path
   payload of the same ``timedelta`` — over a randomized sweep of offsets, with
   live Django spelling the JSON slot (the one string the Rust-side
   differential in ``test_slim_timedelta_2770.rs`` cannot ask Django for).
2. The ``tzinfo`` slot is a string, for every stdlib zone shape.
3. An aware datetime restores from a state round trip to an EQUAL object with
   the same ``utcoffset()`` / ``dst()`` / ``tzname()`` — ``ZoneInfo`` in July
   (DST-bearing) and January, a fixed offset, ``timezone.utc``, a named fixed
   offset.
4. Every ``{{ p.<name> }}`` / ``|date`` / ``timezone`` / ``localtime`` cell
   agrees with Django before AND after the round trip.
5. Rolling deploy: a msgpack state entry captured on ``main`` BEFORE this change
   (``fixtures/aware_datetime_state_pre_2770.msgpack``) restores under the new
   reader to the same objects.

Gate-off, per mechanism (#2135): reverting ``slim_tzinfo`` to
``attr.extract::<Value>()`` fails (2) and the Rust wire pin; reverting
``collect_called_attrs`` to the full path fails nothing HERE by design — (1)
asserts the slim payload equals the full one — and is caught by the Rust
differential's per-slot pins and by the µs/object table in the PR.

Refs #2770, #2740, #2767, #2741, #2481, #2485, ADR-027.
"""

from __future__ import annotations

import datetime
import pathlib
import random
import zoneinfo

import pytest

pytest.importorskip("django")
msgpack = pytest.importorskip("msgpack")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone as django_timezone  # noqa: E402

from djust import _rust  # noqa: E402
from djust._rust import RustLiveView  # noqa: E402
from djust.template_backend import DjustTemplateBackend  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "aware_datetime_state_pre_2770.msgpack"

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")

#: The five zone shapes the stdlib can put on a datetime, with the DST-bearing
#: one in BOTH halves of the year so `dst()` is non-zero in one and zero in the
#: other. These are also the exact values the pre-#2770 fixture was captured
#: from (`scratch: capture_fixture.py`), which is what makes test 5 a
#: comparison rather than a smoke test.
AWARE = {
    "berlin_july": datetime.datetime(2026, 7, 4, 5, 6, 7, 8, tzinfo=BERLIN),
    "berlin_january": datetime.datetime(2026, 1, 4, 5, 6, 7, tzinfo=BERLIN),
    "fixed": datetime.datetime(
        2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone(datetime.timedelta(hours=9))
    ),
    "utc": datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc),
    "named_fixed": datetime.datetime(
        2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone(datetime.timedelta(hours=-3), "XYZ")
    ),
}
NAIVE = datetime.datetime(2026, 3, 4, 5, 6, 7)


def payload_of(value: object) -> list:
    """The eleven-slot ``ENCODED_TAG`` payload of one state value."""
    view = RustLiveView("{{ p }}")
    view.set_state("p", value)
    decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
    return decoded[1]["p"]["__djust_encoded__"]


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


def backend_render(source: str, context: dict) -> str:
    """The `DjustTemplateBackend` entry point — the one that activates the
    current timezone for the Rust engine. `_rust.render_template` does NOT
    (pre-existing, identical on `main`), so an aware value's `|date:"T"`
    through the raw entry renders its own zone where Django localises;
    `test_temporal_format_metadata.py` measures the same cells through this
    backend for the same reason."""
    backend = DjustTemplateBackend({"NAME": "t", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    try:
        return backend.from_string(source).render(dict(context))
    except Exception:  # noqa: BLE001
        return "<<REFUSED>>"


def round_trip_render(source: str, value: object) -> str:
    view = RustLiveView(source)
    view.set_state("p", value)
    return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()


@pytest.fixture
def utc_active():
    """Django's own localisation under test: `USE_TZ` on, `UTC` active, on
    both engines."""
    from djust.render_env import apply_active_timezone

    with override_settings(USE_TZ=True, TIME_ZONE="UTC"), django_timezone.override("UTC"):
        # The Rust engine's zone is a per-thread global the backend/LiveView
        # render paths push (`render_env.apply_active_timezone`); a raw
        # `RustLiveView.render()` reads whatever was last pushed, so push it
        # here rather than depend on an earlier backend render in the same
        # test having done so (`test_isolation` clears it between tests).
        apply_active_timezone()
        yield


def restored(value: object) -> object:
    view = RustLiveView("{{ p }}")
    view.set_state("p", value)
    return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).get_state()["p"]


class _Dst(datetime.tzinfo):
    """A zone whose ``dst()`` is whatever the sweep says — including negative
    and sub-second, which no real zone produces and which is exactly why they
    are worth sweeping."""

    def __init__(self, offset: datetime.timedelta, dst: datetime.timedelta | None) -> None:
        self._offset = offset
        self._dst = dst

    def utcoffset(self, dt):  # noqa: ANN001, ANN201
        return self._offset

    def dst(self, dt):  # noqa: ANN001, ANN201
        return self._dst

    def tzname(self, dt):  # noqa: ANN001, ANN201
        return "SWEEP"


class TestTheNestedTimedeltaIsByteEqualToTheFullPath:
    """Pin 1. A top-level ``timedelta`` in state takes ``django_json_encoded``'s
    FULL path (live ``str`` / ``repr`` / ``DjangoJSONEncoder`` / ``bool``); the
    ``utcoffset`` / ``dst`` slots of an aware datetime now take the slim one.
    The two payloads must be equal in every slot — display, JSON, truthy,
    repr, cmp_key, the four-entry attribute map in table order."""

    SEED = 2770
    CASES = 3_000

    @staticmethod
    def _offset(rng: random.Random) -> datetime.timedelta:
        # `timezone()` needs strictly within ±24h; `_Dst` takes anything.
        seconds = rng.randint(-86_399, 86_399)
        micros = rng.randint(-999_999, 999_999) if rng.random() < 0.3 else 0
        return datetime.timedelta(seconds=seconds, microseconds=micros)

    def test_utcoffset_over_a_randomized_sweep(self) -> None:
        rng = random.Random(self.SEED)
        for i in range(self.CASES):
            offset = self._offset(rng)
            tz = datetime.timezone(offset)
            value = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=tz)
            nested = payload_of(value)[8]["utcoffset"]["__djust_encoded__"]
            full = payload_of(offset)
            assert nested == full, f"case #{i}: {offset!r}\n nested={nested}\n full={full}"
            # The order of the map is part of the contract (#2481).
            assert (
                list(nested[8])
                == list(full[8])
                == [
                    "days",
                    "seconds",
                    "microseconds",
                    "total_seconds",
                ]
            )

    def test_dst_over_a_randomized_sweep_including_negative_and_None(self) -> None:
        rng = random.Random(self.SEED + 1)
        for i in range(self.CASES):
            dst = None if rng.random() < 0.1 else self._offset(rng)
            value = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=_Dst(datetime.timedelta(0), dst))
            attrs = payload_of(value)[8]
            if dst is None:
                assert attrs["dst"] is None, f"case #{i}: None dst"
                continue
            assert attrs["dst"]["__djust_encoded__"] == payload_of(dst), f"case #{i}: {dst!r}"

    def test_the_named_boundaries(self) -> None:
        """The values #2231 taught us a random sweep can still miss: zero, the
        `-1 day, 23:59:59` normalisation of a negative second, the singular
        `day` branch, microseconds alone."""
        for td in [
            datetime.timedelta(0),
            datetime.timedelta(seconds=-1),
            datetime.timedelta(hours=-3, minutes=30),
            datetime.timedelta(microseconds=1),
            datetime.timedelta(hours=23, minutes=59, seconds=59, microseconds=999_999),
            datetime.timedelta(hours=-23, minutes=-59, seconds=-59, microseconds=-999_999),
        ]:
            value = datetime.datetime(2026, 3, 4, tzinfo=_Dst(td, td))
            attrs = payload_of(value)[8]
            full = payload_of(td)
            assert attrs["utcoffset"]["__djust_encoded__"] == full, td
            assert attrs["dst"]["__djust_encoded__"] == full, td


class TestTheTzinfoSlotIsAString:
    """Pin 2 — the ONE shape change, from the Python side (the Rust side pins
    the bytes generically in ``test_slim_timedelta_2770.rs``)."""

    @pytest.mark.parametrize("label", sorted(AWARE))
    def test_an_aware_zone_is_carried_as_str_of_the_zone(self, label: str) -> None:
        value = AWARE[label]
        slot = payload_of(value)[8]["tzinfo"]
        assert isinstance(slot, str), f"{label}: {slot!r} is not a string"
        assert slot == str(value.tzinfo)

    def test_a_naive_value_keeps_its_None(self) -> None:
        assert payload_of(NAIVE)[8]["tzinfo"] is None

    def test_an_aware_time_has_no_zone_slot_to_slim(self) -> None:
        """`DjangoJSONEncoder.default` RAISES for an aware `time`, so it never
        reaches `django_json_encoded`'s tables: it lands in `opaque_value`
        (type `time`, an EMPTY attribute map — measured, not the `str()` the
        `comparison_key` doc still describes). Pinned so the slim path cannot
        be mistaken for having opened that door."""
        value = datetime.time(5, 6, 7, tzinfo=datetime.timezone.utc)
        payload = payload_of(value)
        assert payload[0] == "time"
        assert payload[8] == {}

    def test_the_client_json_never_carried_the_map(self) -> None:
        """The issue's premise, verified: `json_script` writes the encoder
        spelling and nothing else, so no client sees a state-shape change."""
        out = djust_render('{{ p|json_script:"x" }}', {"p": AWARE["berlin_july"]})
        assert "2026-07-04T05:06:07.000+02:00" in out
        assert "tzinfo" not in out and "utcoffset" not in out and "Europe/Berlin" not in out


class TestAnAwareDatetimeRestoresEqual:
    """Pin 3. `get_state()` after a msgpack round trip hands the handler back an
    EQUAL datetime whose zone answers the same three questions."""

    @pytest.mark.parametrize("label", sorted(AWARE))
    def test_equal_with_the_same_zone_behaviour(self, label: str) -> None:
        value = AWARE[label]
        back = restored(value)
        assert isinstance(back, datetime.datetime), type(back)
        assert back == value
        assert back.utcoffset() == value.utcoffset()
        assert back.tzname() == value.tzname()
        assert back.fold == value.fold
        if label == "utc":
            # `str(timezone.utc)` is `"UTC"`, which IS a `ZoneInfo` key, so the
            # zone restores as `ZoneInfo("UTC")` — whose `dst()` is
            # `timedelta(0)` where `timezone.utc.dst()` is `None`. Pre-existing
            # (the old carrier spelled the same string; the fixture test below
            # restores the same way) and recorded rather than hidden.
            assert value.dst() is None
            assert back.dst() == datetime.timedelta(0)
        else:
            assert back.dst() == value.dst()

    def test_the_dst_bearing_zone_restores_as_the_named_zone_not_a_fixed_offset(self) -> None:
        back = restored(AWARE["berlin_july"])
        assert isinstance(back.tzinfo, zoneinfo.ZoneInfo)
        assert back.tzinfo.key == "Europe/Berlin"
        # Transition rules survive: the same zone answers differently in
        # January, which a fixed +02:00 could not.
        assert back.replace(month=1).utcoffset() == datetime.timedelta(hours=1)
        assert back.dst() == datetime.timedelta(hours=1)
        assert back.replace(month=1).dst() == datetime.timedelta(0)

    def test_a_fixed_offset_and_a_named_fixed_offset_keep_their_name(self) -> None:
        assert restored(AWARE["named_fixed"]).tzname() == "XYZ"
        assert restored(AWARE["fixed"]).tzname() == "UTC+09:00"


#: The cells every reader in the table above serves, rendered on the live
#: value and after a round trip, against Django. `USE_TZ` is on in the test
#: settings, so the bare `{{ p }}` and the unpinned `|date` localise to the
#: active zone on both engines.
CELLS = [
    "{{ p.tzinfo }}",
    "{{ p.utcoffset }}",
    "{{ p.dst }}",
    "{{ p.tzname }}",
    "{{ p.utcoffset.total_seconds }}",
    "{{ p.utcoffset.days }}",
    "{% if p.tzinfo %}T{% else %}F{% endif %}",
    "{% if p.dst %}T{% else %}F{% endif %}",
    "{% if p.utcoffset %}T{% else %}F{% endif %}",
    '{{ p|date:"T e O Z I" }}',
    '{{ p|time:"H:i" }}',
    '{% load tz %}{{ p|timezone:"Asia/Tokyo"|date:"H:i T I O" }}',
    '{% load tz %}{{ p|localtime|date:"H:i T I" }}',
    '{% load tz %}{{ p|utc|date:"H:i T I" }}',
    '{% load tz %}{% timezone "Europe/Berlin" %}{{ p|date:"H:i T I" }}{% endtimezone %}',
    "{{ p }}",
]


class TestEveryReaderStillAgreesWithDjango:
    """Pin 4. The table in the module docstring is the contract; each row is a
    cell here, on the live value and after the round trip."""

    @pytest.mark.usefixtures("utc_active")
    @pytest.mark.parametrize("source", CELLS)
    @pytest.mark.parametrize("label", sorted(AWARE))
    def test_live_and_restored_match_django(self, label: str, source: str) -> None:
        value = AWARE[label]
        expected = django_render(source, {"p": value})
        assert expected != "<<REFUSED>>", (label, source)
        assert backend_render(source, {"p": value}) == expected, (label, source, "live")
        assert round_trip_render(source, value) == expected, (label, source, "restored")

    @pytest.mark.usefixtures("utc_active")
    def test_a_naive_value_did_not_move(self) -> None:
        for source in ["{{ p.tzinfo }}", "{{ p.utcoffset }}", "{{ p.dst }}", '{{ p|date:"T I" }}']:
            expected = django_render(source, {"p": NAIVE})
            assert backend_render(source, {"p": NAIVE}) == expected, source
            assert round_trip_render(source, NAIVE) == expected, source


class TestAPre2770StateEntryRestoresUnderTheNewReader:
    """Pin 5 — rolling deploy. The fixture was serialized by the pre-#2770
    build on `main` from exactly the `AWARE` values plus `NAIVE`: the zone slot
    is a nested `Encoded` there, which the new reader must still read."""

    @pytest.fixture(scope="class")
    def old_view(self) -> RustLiveView:
        return RustLiveView.deserialize_msgpack(FIXTURE.read_bytes())

    def test_the_fixture_really_is_the_old_shape(self) -> None:
        decoded = msgpack.unpackb(FIXTURE.read_bytes(), raw=False, strict_map_key=False)
        slot = decoded[1]["berlin_july"]["__djust_encoded__"][8]["tzinfo"]
        assert isinstance(slot, dict) and "__djust_encoded__" in slot, slot
        assert slot["__djust_encoded__"][1] == "Europe/Berlin"

    @pytest.mark.parametrize("label", sorted(AWARE))
    def test_every_aware_value_restores_equal(self, old_view: RustLiveView, label: str) -> None:
        value = AWARE[label]
        back = old_view.get_state()[label]
        assert isinstance(back, datetime.datetime), type(back)
        assert back == value
        assert back.utcoffset() == value.utcoffset()
        assert back.tzname() == value.tzname()
        # The same `timezone.utc` -> `ZoneInfo("UTC")` note as above: the old
        # carrier restores IDENTICALLY, which is what makes it pre-existing.
        assert back.dst() == (datetime.timedelta(0) if label == "utc" else value.dst())

    def test_the_naive_value_restores_naive(self, old_view: RustLiveView) -> None:
        back = old_view.get_state()["naive"]
        assert back == NAIVE and back.tzinfo is None

    @pytest.mark.parametrize(
        "source",
        [
            "{{ berlin_july.tzinfo }}",
            "{{ berlin_july.utcoffset }}",
            "{{ berlin_july.dst }}",
            '{{ berlin_july|date:"T e O Z I" }}',
            '{% load tz %}{{ berlin_july|timezone:"Asia/Tokyo"|date:"H:i T I" }}',
            "{{ fixed.tzinfo }}",
            "{{ named_fixed.tzname }}",
        ],
    )
    @pytest.mark.usefixtures("utc_active")
    def test_the_old_shape_renders_what_django_renders(self, source: str) -> None:
        decoded = msgpack.unpackb(FIXTURE.read_bytes(), raw=False, strict_map_key=False)
        decoded[0] = source
        view = RustLiveView.deserialize_msgpack(msgpack.packb(decoded, use_bin_type=True))
        assert view.render() == django_render(source, dict(AWARE))
