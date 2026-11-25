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
from django.template import Context, Engine
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
    expected = Engine().from_string(source).render(Context(context))
    actual = _backend().from_string(source).render(context)
    return actual, expected


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
    assert actual == expected


@pytest.mark.parametrize(("zone", "value"), NAIVE_CASES)
def test_naive_value_formats_like_django(zone, value):
    source = '{{ v|date:"' + FORMAT + '" }}'
    with override_settings(USE_TZ=True, TIME_ZONE=zone):
        actual, expected = _both(source, {"v": value})
    assert actual == expected


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
