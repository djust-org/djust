"""Native date/time filters retain Python temporal type and timezone metadata."""

from datetime import date, datetime, time, timedelta, timezone as fixed_timezone
from zoneinfo import ZoneInfo

import pytest
from django.template import Context, Engine
from django.test import override_settings
from django.utils import timezone

from djust.template import DjustTemplateBackend


@pytest.mark.parametrize("use_tz", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        datetime(2009, 3, 12, 4, tzinfo=timezone.get_fixed_timezone(30)),
        datetime(2009, 3, 12, 4, tzinfo=fixed_timezone(timedelta(hours=3), "custom")),
        datetime(2009, 7, 12, 4, tzinfo=ZoneInfo("Europe/Paris")),
        time(4, tzinfo=timezone.get_fixed_timezone(30)),
        time(4, 5, 6, 123456, tzinfo=fixed_timezone(timedelta(hours=-3))),
        date(2009, 3, 12),
    ],
)
@pytest.mark.parametrize(
    "filter_name,format_string",
    [
        ("date", "e:T:O:Z"),
        ("time", "P:e:O:T:Z"),
        ("date", "H:i:s:u"),
        ("date", "Y-m-d"),
        ("date", r"\H"),
        ("date", r"\\H"),
    ],
)
def test_temporal_metadata_matches_django(value, use_tz, filter_name, format_string):
    source = "{{ value|" + filter_name + ':"' + format_string + '" }}'
    with override_settings(USE_TZ=use_tz, TIME_ZONE="UTC"), timezone.override("UTC"):
        backend = DjustTemplateBackend(
            {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        try:
            expected = Engine().from_string(source).render(Context({"value": value}))
        except TypeError as error:
            with pytest.raises(TypeError) as actual:
                backend.from_string(source).render({"value": value})
            assert str(actual.value) == str(error)
        else:
            assert backend.from_string(source).render({"value": value}) == expected
