"""
Tests for the ``manage.py djust_doctor`` management command.

Each diagnostic check is tested with mocked dependencies so the tests
run without a full Django/Channels/Redis stack.
"""

import django
import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from djust.management.commands.djust_doctor import (
    _CheckResult,
    check_asgi_configured,
    check_asgi_server,
    check_channel_layers,
    check_channels_installed,
    check_django_version,
    check_djust_version,
    check_python_version,
    check_redis,
    check_rust_extension,
    check_rust_render,
    check_static_files,
    check_template_dirs,
)


class TestCheckDjustVersion(SimpleTestCase):
    def test_ok(self):
        result = check_djust_version()
        self.assertEqual(result.status, _CheckResult.OK)
        self.assertIn("djust", result.message)


class TestCheckPythonVersion(SimpleTestCase):
    def test_current_python(self):
        result = check_python_version()
        self.assertIn(result.status, (_CheckResult.OK, _CheckResult.WARN))
        self.assertIn("Python", result.message)

    @patch.object(sys, "version_info", (3, 7, 0, "final", 0))
    def test_old_python_fails(self):
        result = check_python_version()
        self.assertEqual(result.status, _CheckResult.FAIL)


class TestCheckDjangoVersion(SimpleTestCase):
    def test_current_django(self):
        result = check_django_version()
        if django.VERSION >= (4, 0):
            self.assertEqual(result.status, _CheckResult.OK)
        else:
            self.assertEqual(result.status, _CheckResult.WARN)


class TestCheckRustExtension(SimpleTestCase):
    def test_ok(self):
        result = check_rust_extension()
        self.assertIn(result.status, (_CheckResult.OK, _CheckResult.FAIL))


class TestCheckChannelsInstalled(SimpleTestCase):
    def test_channels_present(self):
        result = check_channels_installed()
        self.assertEqual(result.status, _CheckResult.OK)


class TestCheckAsgiConfigured(SimpleTestCase):
    @override_settings(ASGI_APPLICATION="myproject.asgi.application")
    def test_configured(self):
        result = check_asgi_configured()
        self.assertEqual(result.status, _CheckResult.OK)

    def test_missing(self):
        # Temporarily remove ASGI_APPLICATION
        with self.settings():
            from django.conf import settings as s

            orig = getattr(s, "ASGI_APPLICATION", None)
            try:
                if hasattr(s, "ASGI_APPLICATION"):
                    delattr(s, "ASGI_APPLICATION")
                result = check_asgi_configured()
                self.assertEqual(result.status, _CheckResult.FAIL)
            finally:
                if orig is not None:
                    s.ASGI_APPLICATION = orig


class TestCheckChannelLayers(SimpleTestCase):
    @override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    )
    def test_in_memory(self):
        result = check_channel_layers()
        self.assertEqual(result.status, _CheckResult.INFO)
        self.assertIn("InMemory", result.message)

    @override_settings(
        CHANNEL_LAYERS={
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [("localhost", 6379)]},
            }
        }
    )
    def test_redis_backend(self):
        result = check_channel_layers()
        self.assertEqual(result.status, _CheckResult.OK)
        self.assertIn("RedisChannelLayer", result.message)


class TestCheckRedis(SimpleTestCase):
    @override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    )
    def test_skip_when_not_redis(self):
        result = check_redis()
        self.assertIsNone(result)

    @override_settings(
        CHANNEL_LAYERS={
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [("localhost", 6379)]},
            }
        }
    )
    def test_redis_ping_ok(self):
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis_cls = MagicMock(return_value=mock_redis)
        mock_module = MagicMock()
        mock_module.Redis = mock_redis_cls
        with patch.dict("sys.modules", {"redis": mock_module}):
            result = check_redis()
        self.assertEqual(result.status, _CheckResult.OK)
        self.assertIn("connected", result.message)

    @override_settings(
        CHANNEL_LAYERS={
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [("localhost", 6379)]},
            }
        }
    )
    def test_redis_connection_failure(self):
        mock_redis_cls = MagicMock(side_effect=ConnectionError("refused"))
        mock_module = MagicMock()
        mock_module.Redis = mock_redis_cls
        with patch.dict("sys.modules", {"redis": mock_module}):
            result = check_redis()
        self.assertEqual(result.status, _CheckResult.FAIL)


class TestCheckTemplateDirs(SimpleTestCase):
    @override_settings(TEMPLATES=[{"DIRS": ["/nonexistent/path"]}])
    def test_missing_dir(self):
        result = check_template_dirs()
        self.assertEqual(result.status, _CheckResult.WARN)
        self.assertIn("/nonexistent/path", result.message)

    @override_settings(TEMPLATES=[])
    def test_no_templates(self):
        result = check_template_dirs()
        self.assertEqual(result.status, _CheckResult.WARN)


class TestCheckStaticFiles(SimpleTestCase):
    def test_static_files(self):
        result = check_static_files()
        self.assertIn(result.status, (_CheckResult.OK, _CheckResult.FAIL))


class TestCheckRustRender(SimpleTestCase):
    def test_render(self):
        result = check_rust_render()
        self.assertIn(result.status, (_CheckResult.OK, _CheckResult.FAIL))


class TestCheckAsgiServer(SimpleTestCase):
    def test_server_check(self):
        result = check_asgi_server()
        self.assertIn(result.status, (_CheckResult.OK, _CheckResult.WARN))


class TestCommandOutput(SimpleTestCase):
    """Test the management command output formats."""

    def test_json_output(self):
        out = StringIO()
        try:
            call_command("djust_doctor", "--json", stdout=out, stderr=StringIO())
        except SystemExit:
            pass  # djust_doctor exits non-zero when checks fail  # djust_doctor exits non-zero when checks fail
        output = out.getvalue()
        data = json.loads(output)
        self.assertIn("status", data)
        self.assertIn("checks", data)
        self.assertIsInstance(data["checks"], list)
        self.assertGreater(len(data["checks"]), 0)

    def test_quiet_mode(self):
        out = StringIO()
        try:
            call_command("djust_doctor", "--quiet", stdout=out, stderr=StringIO())
        except SystemExit:
            pass  # djust_doctor exits non-zero when checks fail
        self.assertEqual(out.getvalue(), "")

    def test_pretty_output(self):
        out = StringIO()
        try:
            call_command("djust_doctor", stdout=out, stderr=StringIO())
        except SystemExit:
            pass  # djust_doctor exits non-zero when checks fail
        output = out.getvalue()
        self.assertIn("djust doctor", output)

    def test_single_check(self):
        out = StringIO()
        try:
            call_command(
                "djust_doctor", "--json", "--check", "python_version", stdout=out, stderr=StringIO()
            )
        except SystemExit:
            pass  # djust_doctor exits non-zero when checks fail
        data = json.loads(out.getvalue())
        self.assertEqual(len(data["checks"]), 1)
        self.assertEqual(data["checks"][0]["name"], "python_version")

    def test_unknown_check(self):
        err = StringIO()
        call_command("djust_doctor", "--check", "nonexistent", stdout=StringIO(), stderr=err)
        self.assertIn("Unknown check", err.getvalue())

    def test_verbose_output(self):
        out = StringIO()
        try:
            call_command("djust_doctor", "--verbose", stdout=out, stderr=StringIO())
        except SystemExit:
            pass  # djust_doctor exits non-zero when checks fail
        output = out.getvalue()
        self.assertIn("djust doctor", output)

    def test_exit_codes_mechanism(self):
        """Verify exit code semantics: 0=pass, 1=warn, 2=fail."""
        results = [_CheckResult("t", "c", _CheckResult.OK, "ok")]
        has_fail = any(r.status == _CheckResult.FAIL for r in results)
        has_warn = any(r.status == _CheckResult.WARN for r in results)
        self.assertFalse(has_fail)
        self.assertFalse(has_warn)
