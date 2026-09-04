"""Naive datetime zone fields use Django's default, not the active timezone."""

from datetime import datetime

import pytest
from django.template import Context, Engine
from django.test import override_settings
from django.utils import timezone

from djust.template import DjustTemplateBackend


@pytest.mark.parametrize("use_tz", [False, True])
@pytest.mark.parametrize("default_zone", ["America/Chicago", "Europe/Paris", "Asia/Kolkata"])
@pytest.mark.parametrize("active_zone", ["UTC", "Australia/Sydney"])
@pytest.mark.parametrize("month", [1, 7])
@pytest.mark.parametrize("filter_name", ["date", "time"])
def test_naive_datetime_uses_default_timezone(
    use_tz, default_zone, active_zone, month, filter_name
):
    source = "{{ value|" + filter_name + ':"H:i:e:T:O:Z" }}'
    value = datetime(2024, month, 12, 4, 5)
    with override_settings(USE_TZ=use_tz, TIME_ZONE=default_zone), timezone.override(active_zone):
        expected = Engine().from_string(source).render(Context({"value": value}))
        backend = DjustTemplateBackend(
            {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        assert backend.from_string(source).render({"value": value}) == expected
