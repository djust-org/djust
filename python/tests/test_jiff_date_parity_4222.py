"""Date filters keep Django parity across the chrono -> jiff swap (#4222).

The Rust engine's date arithmetic and timezone database moved from
``chrono``/``chrono-tz`` (both RustSec-unmaintained) to ``jiff``. The existing
suites pin the common shapes; this one is a differential against Django's own
engine for the edges a library swap is most likely to move:

* a DST **gap** (a wall clock that never existed) and **fold** (one that
  happened twice), for both aware and naive values;
* a far-future date at the top of Python's range (9999-12-31);
* negative UTC offsets, including a half-hour one (``America/St_Johns``) and a
  zone whose DST is a NEGATIVE offset in the tz database (``Europe/Dublin``);
* the extreme positive offset (``Pacific/Kiritimati``, +14:00);
* ``timesince``/``timeuntil`` with an explicit comparison instant, so the
  calendar arithmetic is pinned without reading the wall clock.

Every expected string is Django's, computed in the same process.
"""

from datetime import datetime, timedelta, timezone

import pytest
from django.template.backends.django import DjangoTemplates
from django.test import override_settings
from django.utils import timezone as dj_timezone

from djust.template import DjustTemplateBackend

FORMAT = "Y-m-d H:i:s|e|T|O|Z|I|U|c|r|N|W|o|z|t|L|D|l|F|M|b|P|f"

UTC = timezone.utc

# (zone, instant) pairs. Aware values are given in UTC; the active zone
# converts them. The comments give the local wall clock.
AWARE_CASES = [
    # New York spring-forward: 01:59 EST, then 03:00 EDT.
    ("America/New_York", datetime(2026, 3, 8, 6, 59, tzinfo=UTC)),
    ("America/New_York", datetime(2026, 3, 8, 7, 0, tzinfo=UTC)),
    # New York fall-back: 01:30 EDT and 01:30 EST — the same wall clock twice.
    ("America/New_York", datetime(2026, 11, 1, 5, 30, tzinfo=UTC)),
    ("America/New_York", datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
    # Far future, negative offset.
    ("America/New_York", datetime(9999, 12, 31, 23, 59, tzinfo=UTC)),
    # Half-hour negative offset, both sides of the year.
    ("America/St_Johns", datetime(2026, 1, 15, 12, 0, tzinfo=UTC)),
    ("America/St_Johns", datetime(2026, 7, 15, 12, 0, tzinfo=UTC)),
    # Negative DST in the tz database: IST (+01) is "standard", GMT is "DST".
    ("Europe/Dublin", datetime(2026, 1, 15, 12, 0, tzinfo=UTC)),
    ("Europe/Dublin", datetime(2026, 7, 15, 12, 0, tzinfo=UTC)),
    # Largest positive offset; crosses the date line.
    ("Pacific/Kiritimati", datetime(2026, 1, 15, 12, 0, tzinfo=UTC)),
    # Southern hemisphere, 30-minute DST shift.
    ("Australia/Lord_Howe", datetime(2026, 1, 15, 12, 0, tzinfo=UTC)),
    ("Australia/Lord_Howe", datetime(2026, 7, 15, 12, 0, tzinfo=UTC)),
    # A zone with no DST at all.
    ("Asia/Kolkata", datetime(2026, 7, 15, 12, 0, 0, 123456, tzinfo=UTC)),
    # A fixed negative offset carried by the value itself, no conversion.
    ("UTC", datetime(2026, 8, 22, 14, 30, tzinfo=timezone(timedelta(hours=-7)))),
]

# Naive values are NOT shifted; zone fields come from the default zone.
NAIVE_CASES = [
    # Inside New York's spring-forward gap: 02:30 never happened.
    ("America/New_York", datetime(2026, 3, 8, 2, 30)),
    # Inside New York's fall-back fold: 01:30 happened twice.
    ("America/New_York", datetime(2026, 11, 1, 1, 30)),
    # Either side of both transitions.
    ("America/New_York", datetime(2026, 3, 8, 1, 59)),
    ("America/New_York", datetime(2026, 3, 8, 3, 0)),
    ("America/New_York", datetime(2026, 11, 1, 2, 0)),
    # Far future, negative offset.
    ("America/New_York", datetime(9999, 12, 31, 23, 59, 59)),
    ("America/St_Johns", datetime(2026, 1, 15, 8, 30)),
    ("Europe/Dublin", datetime(2026, 7, 15, 12, 0)),
]


def _backend():
    return DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


def _both(source, context):
    # The DjangoTemplates backend, not a bare `Engine()`, so `{% load tz %}`
    # resolves on both sides.
    django_backend = DjangoTemplates({"NAME": "dj", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    expected = django_backend.from_string(source).render(context)
    actual = _backend().from_string(source).render(context)
    return actual, expected


_DST_FIELD = FORMAT.split("|").index("I")


def _without_tzdata_dependent_dst(zone, actual, expected):
    """Drop the ``I`` field for Europe/Dublin before comparing.

    Django's ``I`` is ``tzinfo.dst() != 0``, read from whatever tz database
    Python's ``zoneinfo`` loads. Ireland is the one zone whose tz source
    gives a NEGATIVE save (winter GMT is "DST" at -1h): the "vanguard" build
    (the ``tzdata`` package, some distros) reports winter as DST, the
    "rearguard" build reports summer. So Django's own answer flips with the
    host, and a differential against it cannot pin either value. djust's
    rule (DST = clock ahead of the zone's standard offset) is pinned by
    ``test_dublin_dst_flag_means_clock_ahead_of_standard`` instead.
    """
    if zone != "Europe/Dublin":
        return actual, expected

    def drop(text):
        parts = text.split("|")
        if len(parts) > _DST_FIELD:
            del parts[_DST_FIELD]
        return "|".join(parts)

    return drop(actual), drop(expected)


@pytest.fixture(autouse=True)
def _reset_activation():
    dj_timezone.deactivate()
    yield
    dj_timezone.deactivate()


@pytest.mark.parametrize("filter_name", ["date", "time"])
@pytest.mark.parametrize(("zone", "value"), AWARE_CASES)
def test_aware_value_formats_like_django(zone, value, filter_name):
    fmt = FORMAT if filter_name == "date" else "H:i:s|e|T|O|Z|P|f|u"
    source = "{{ v|" + filter_name + ':"' + fmt + '" }}'
    with override_settings(USE_TZ=True, TIME_ZONE=zone):
        actual, expected = _both(source, {"v": value})
    if filter_name == "date":
        actual, expected = _without_tzdata_dependent_dst(zone, actual, expected)
    assert actual == expected


@pytest.mark.parametrize(("zone", "value"), NAIVE_CASES)
def test_naive_value_formats_like_django(zone, value):
    source = '{{ v|date:"' + FORMAT + '" }}'
    with override_settings(USE_TZ=True, TIME_ZONE=zone):
        actual, expected = _both(source, {"v": value})
    actual, expected = _without_tzdata_dependent_dst(zone, actual, expected)
    assert actual == expected


@pytest.mark.parametrize(
    ("value", "dst_flag"),
    [
        (datetime(2026, 1, 15, 12, 0, tzinfo=UTC), "0"),
        (datetime(2026, 7, 15, 12, 0, tzinfo=UTC), "1"),
    ],
)
def test_dublin_dst_flag_means_clock_ahead_of_standard(value, dst_flag):
    """djust's ``I`` for Europe/Dublin is pinned on its own, independent of the
    host's tz database build: summer (IST, +0100) is DST, winter (GMT) is not.
    See ``_without_tzdata_dependent_dst`` for why Django's answer varies."""
    with override_settings(USE_TZ=True, TIME_ZONE="Europe/Dublin"):
        actual = _backend().from_string('{{ v|date:"I" }}').render({"v": value})
    assert actual == dst_flag


@pytest.mark.parametrize(
    ("then", "now"),
    [
        # Across a spring-forward gap: 23 wall-clock hours, 22 real ones.
        (
            datetime(2026, 3, 7, 12, 0, tzinfo=UTC),
            datetime(2026, 3, 8, 11, 0, tzinfo=UTC),
        ),
        # Leap-day source, Django's 28-day February clamp.
        (datetime(2024, 2, 29, 10, 0), datetime(2025, 3, 1, 9, 0)),
        # Far future, multi-year.
        (datetime(9990, 1, 31, 0, 0), datetime(9999, 12, 31, 23, 59)),
        # Negative fixed offsets on both operands.
        (
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
            datetime(2026, 2, 10, 3, 30, tzinfo=timezone(timedelta(hours=-3, minutes=-30))),
        ),
    ],
)
@pytest.mark.parametrize("filter_name", ["timesince", "timeuntil"])
def test_duration_filters_with_explicit_instant_match_django(then, now, filter_name):
    if filter_name == "timeuntil":
        then, now = now, then
    source = "{{ a|" + filter_name + ":b }}"
    with override_settings(USE_TZ=True, TIME_ZONE="America/New_York"):
        actual, expected = _both(source, {"a": then, "b": now})
    assert actual == expected


# A value whose zone Django does NOT take from the active zone: the ambiguity
# guard then runs against the value's own tzinfo. Both instants are 01:30 in
# New York on the fall-back night; one minute later is unambiguous again.
NY_FOLD = [
    datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
    datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
    datetime(2026, 11, 1, 7, 31, tzinfo=UTC),
]
ZONE_CODES = "H:i|e|T|O|Z|I|U|c|r"


@pytest.mark.parametrize("value", NY_FOLD)
def test_a_value_pinned_by_the_timezone_filter_checks_its_own_zone(value):
    source = '{% load tz %}{{ v|timezone:"America/New_York"|date:"' + ZONE_CODES + '" }}'
    with override_settings(USE_TZ=True, TIME_ZONE="Asia/Tokyo"):
        actual, expected = _both(source, {"v": value})
    assert actual == expected


@pytest.mark.parametrize("value", NY_FOLD)
def test_localtime_off_checks_the_values_own_zone(value):
    from zoneinfo import ZoneInfo

    local = value.astimezone(ZoneInfo("America/New_York"))
    source = '{% load tz %}{% localtime off %}{{ v|date:"' + ZONE_CODES + '" }}{% endlocaltime %}'
    with override_settings(USE_TZ=True, TIME_ZONE="Asia/Tokyo"):
        actual, expected = _both(source, {"v": local})
    assert actual == expected


@pytest.mark.parametrize("value", NY_FOLD)
def test_use_tz_false_checks_an_aware_values_own_zone(value):
    from zoneinfo import ZoneInfo

    local = value.astimezone(ZoneInfo("America/New_York"))
    source = '{{ v|date:"' + ZONE_CODES + '" }}'
    with override_settings(USE_TZ=False, TIME_ZONE="Asia/Tokyo"):
        actual, expected = _both(source, {"v": local})
    assert actual == expected


@pytest.mark.parametrize(
    "value",
    [
        datetime(9998, 9, 28, 10, 10, 25, 999999, tzinfo=UTC),
        datetime(1900, 9, 22, 22, 51, 49, 123456, tzinfo=UTC),
        datetime(1969, 12, 31, 23, 59, 59, 500000, tzinfo=UTC),
        datetime(1, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        datetime(4000, 6, 30, 12, 0, 0, 999999, tzinfo=UTC),
    ],
)
def test_u_is_int_of_the_float_timestamp(value):
    with override_settings(USE_TZ=True, TIME_ZONE="UTC"):
        actual, expected = _both('{{ v|date:"U" }}', {"v": value})
    assert actual == expected
