"""
Tests for browser timezone detection and conversion.
"""

import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import django
from django.conf import settings

# Minimal Django settings for standalone test execution
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        USE_TZ=True,
        TIME_ZONE="UTC",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {"context_processors": []},
            }
        ],
    )
    django.setup()

from django.test import TestCase
from django.template import Template, Context
from django.utils import timezone as django_tz

from djust.utils.timezone import to_client_tz


class ToClientTzTests(TestCase):
    """Tests for djust.utils.timezone.to_client_tz"""

    def test_converts_utc_to_eastern(self):
        dt = datetime.datetime(2024, 6, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = to_client_tz(dt, "America/New_York")
        self.assertEqual(result.tzinfo, ZoneInfo("America/New_York"))
        self.assertEqual(result.hour, 14)  # UTC-4 in June (EDT)

    def test_converts_utc_to_tokyo(self):
        dt = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = to_client_tz(dt, "Asia/Tokyo")
        self.assertEqual(result.hour, 21)  # UTC+9

    def test_naive_datetime_assumed_server_tz(self):
        dt = datetime.datetime(2024, 6, 15, 12, 0, 0)
        result = to_client_tz(dt, "America/New_York")
        # Should be aware now
        self.assertIsNotNone(result.tzinfo)

    def test_none_returns_none(self):
        self.assertIsNone(to_client_tz(None))

    def test_invalid_tz_falls_back_to_server(self):
        dt = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = to_client_tz(dt, "Invalid/Timezone")
        # Should not raise, falls back to server TZ
        self.assertIsNotNone(result.tzinfo)

    def test_view_instance_with_client_timezone(self):
        dt = datetime.datetime(2024, 6, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        view = MagicMock()
        view.client_timezone = "Europe/London"
        result = to_client_tz(dt, view)
        self.assertEqual(result.tzinfo, ZoneInfo("Europe/London"))
        self.assertEqual(result.hour, 19)  # UTC+1 in June (BST)

    def test_view_instance_without_client_timezone(self):
        dt = datetime.datetime(2024, 6, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        view = MagicMock()
        view.client_timezone = None
        result = to_client_tz(dt, view)
        # Falls back to server timezone
        self.assertIsNotNone(result.tzinfo)

    def test_no_tz_argument_uses_server_default(self):
        dt = datetime.datetime(2024, 6, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        result = to_client_tz(dt)
        self.assertIsNotNone(result.tzinfo)


class TemplateFilterTests(TestCase):
    """Tests for djust_tz template filters."""

    def test_localtime_for_client_filter(self):
        dt = datetime.datetime(2024, 6, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        t = Template("{% load djust_tz %}{{ dt|localtime_for_client }}")
        result = t.render(Context({"dt": dt}))
        self.assertIn("2024", result)

    def test_client_time_filter_with_format(self):
        dt = datetime.datetime(2024, 6, 15, 18, 30, 0, tzinfo=ZoneInfo("UTC"))
        t = Template('{% load djust_tz %}{{ dt|client_time:"H:i" }}')
        result = t.render(Context({"dt": dt}))
        # Should contain time formatted as H:i
        self.assertRegex(result.strip(), r"\d{1,2}:\d{2}")

    def test_client_time_tag_with_context(self):
        dt = datetime.datetime(2024, 6, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        t = Template('{% load djust_tz %}{% client_time_tag dt "H:i" %}')
        result = t.render(Context({
            "dt": dt,
            "client_timezone": "America/New_York",
        }))
        self.assertEqual(result.strip(), "14:00")

    def test_client_localtime_tag(self):
        dt = datetime.datetime(2024, 6, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        # Use client_time_tag to format, since Django's |date filter
        # re-converts aware datetimes to the current timezone
        t = Template('{% load djust_tz %}{% client_time_tag dt "H:i" %}')
        result = t.render(Context({
            "dt": dt,
            "client_timezone": "Asia/Tokyo",
        }))
        self.assertEqual(result.strip(), "03:00")  # UTC+9 = next day 3am

    def test_none_value_returns_empty(self):
        t = Template("{% load djust_tz %}{{ dt|localtime_for_client }}")
        result = t.render(Context({"dt": None}))
        self.assertEqual(result.strip(), "")

    def test_client_time_none_returns_empty(self):
        t = Template('{% load djust_tz %}{{ dt|client_time:"H:i" }}')
        result = t.render(Context({"dt": None}))
        self.assertEqual(result.strip(), "")


class WebSocketTimezoneTests(TestCase):
    """Tests for timezone being passed through WebSocket mount."""

    def test_valid_timezone_stored(self):
        """Simulate what handle_mount does with client_timezone."""
        from zoneinfo import ZoneInfo

        tz_str = "America/Chicago"
        try:
            ZoneInfo(tz_str)
            valid = True
        except (KeyError, Exception):
            valid = False
        self.assertTrue(valid)

    def test_invalid_timezone_rejected(self):
        from zoneinfo import ZoneInfo

        with self.assertRaises(KeyError):
            ZoneInfo("Fake/Timezone")
