"""
Tests for the djust_check management command.
"""
import os
from io import StringIO
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.core.management import call_command
from djust.management.commands.djust_check import Command as DjustCheckCommand


def _run_djust_check(*args, stdout=None):
    """Run djust_check command directly (works even when djust is not in INSTALLED_APPS)."""
    cmd = DjustCheckCommand(stdout=stdout or StringIO(), stderr=StringIO())
    # Call handle() directly to skip Django system checks (which fail when apps are removed)
    from django.core.management.base import BaseCommand
    parser = cmd.create_parser("manage.py", "djust_check")
    options = parser.parse_args(list(args))
    cmd_options = vars(options)
    cmd_options.pop("args", ())
    cmd.handle(**cmd_options)
    return cmd


MINIMAL_INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "channels",
    "djust",
]


class DjustCheckSettingsTest(TestCase):
    """Test settings validation checks."""

    @override_settings(
        INSTALLED_APPS=MINIMAL_INSTALLED_APPS,
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        ASGI_APPLICATION="djust.asgi.application",
    )
    def test_all_settings_pass(self):
        out = StringIO()
        call_command("djust_check", "--verbose", stdout=out)
        output = out.getvalue()
        assert "djust" in output
        assert "✅" in output

    @override_settings(
        INSTALLED_APPS=["django.contrib.staticfiles"],
        ASGI_APPLICATION="djust.asgi.application",
    )
    def test_missing_djust_in_installed_apps(self):
        out = StringIO()
        _run_djust_check(stdout=out)
        output = out.getvalue()
        assert "'djust' is not in INSTALLED_APPS" in output

    @override_settings(
        INSTALLED_APPS=["django.contrib.staticfiles", "djust"],
        ASGI_APPLICATION="djust.asgi.application",
    )
    def test_missing_channels_in_installed_apps(self):
        out = StringIO()
        call_command("djust_check", stdout=out)
        output = out.getvalue()
        assert "'channels' is not in INSTALLED_APPS" in output

    @override_settings(
        INSTALLED_APPS=MINIMAL_INSTALLED_APPS,
        ASGI_APPLICATION=None,
    )
    def test_missing_asgi_application(self):
        out = StringIO()
        call_command("djust_check", stdout=out)
        output = out.getvalue()
        assert "ASGI_APPLICATION is not set" in output

    @override_settings(
        INSTALLED_APPS=MINIMAL_INSTALLED_APPS,
        ASGI_APPLICATION="djust.asgi.application",
        CHANNEL_LAYERS={},
    )
    def test_missing_channel_layers_warns(self):
        out = StringIO()
        call_command("djust_check", stdout=out)
        output = out.getvalue()
        assert "CHANNEL_LAYERS is not configured" in output
        assert "⚠" in output

    @override_settings(
        INSTALLED_APPS=["channels", "djust"],
        ASGI_APPLICATION="djust.asgi.application",
    )
    def test_missing_staticfiles(self):
        out = StringIO()
        call_command("djust_check", stdout=out)
        output = out.getvalue()
        assert "django.contrib.staticfiles" in output


class DjustCheckStaticFilesTest(TestCase):
    """Test static file checks."""

    @override_settings(
        INSTALLED_APPS=MINIMAL_INSTALLED_APPS,
        ASGI_APPLICATION="djust.asgi.application",
    )
    @patch("djust.management.commands.djust_check.find_static")
    def test_static_files_found(self, mock_find):
        mock_find.return_value = "/some/path/client.js"
        out = StringIO()
        call_command("djust_check", "--verbose", stdout=out)
        output = out.getvalue()
        # Should not contain static file errors
        assert "Static file not found" not in output

    @override_settings(
        INSTALLED_APPS=MINIMAL_INSTALLED_APPS,
        ASGI_APPLICATION="djust.asgi.application",
    )
    @patch("djust.management.commands.djust_check.find_static")
    def test_static_files_missing(self, mock_find):
        mock_find.return_value = None
        out = StringIO()
        call_command("djust_check", stdout=out)
        output = out.getvalue()
        assert "Static file not found" in output

    @override_settings(
        INSTALLED_APPS=MINIMAL_INSTALLED_APPS + ["daphne"],
        ASGI_APPLICATION="djust.asgi.application",
    )
    @patch("djust.management.commands.djust_check.find_static")
    def test_daphne_warning(self, mock_find):
        mock_find.return_value = "/some/path"
        out = StringIO()
        call_command("djust_check", stdout=out)
        output = out.getvalue()
        assert "Daphne detected" in output or "ASGIStaticFilesHandler" in output


class DjustCheckFixSuggestionsTest(TestCase):
    """Test that --fix flag shows suggestions."""

    @override_settings(
        INSTALLED_APPS=["django.contrib.staticfiles"],
        ASGI_APPLICATION="djust.asgi.application",
    )
    def test_fix_flag_shows_suggestions(self):
        out = StringIO()
        _run_djust_check("--fix", stdout=out)
        output = out.getvalue()
        assert "Fix:" in output

    @override_settings(
        INSTALLED_APPS=["django.contrib.staticfiles"],
        ASGI_APPLICATION="djust.asgi.application",
    )
    def test_no_fix_flag_hides_suggestions(self):
        out = StringIO()
        _run_djust_check(stdout=out)
        output = out.getvalue()
        # Should suggest running with --fix instead
        assert "--fix" in output


class DjustCheckSummaryTest(TestCase):
    """Test summary output."""

    @override_settings(
        INSTALLED_APPS=MINIMAL_INSTALLED_APPS,
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        ASGI_APPLICATION="djust.asgi.application",
    )
    def test_summary_shows_counts(self):
        out = StringIO()
        call_command("djust_check", stdout=out)
        output = out.getvalue()
        assert "checks:" in output
        assert "passed" in output
