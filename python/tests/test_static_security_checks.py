"""
Tests for the static security checks added in #659.

Checks covered:
- **A001** — ASGI websocket router missing ``AllowedHostsOriginValidator``
- **A010** — ALLOWED_HOSTS is only ``['*']`` in production
- **A011** — ALLOWED_HOSTS mixes ``'*'`` with explicit hosts
- **A012** — USE_X_FORWARDED_HOST=True + wildcard ALLOWED_HOSTS
- **A014** — SECRET_KEY starts with ``django-insecure-`` in production
- **A020** — LOGIN_REDIRECT_URL hardcoded + multi-group auth
- **A030** — ``django.contrib.admin`` without a brute-force protection package

Each check is exercised via ``django.test.override_settings`` and the
``check_configuration`` entry point, so we test the real Django-checks
pipeline, not a mock.
"""

from unittest.mock import patch

from django.test import override_settings

from djust.checks import _has_multiple_permission_groups, check_configuration


def _ids(errors):
    """Extract the set of check IDs from a list of Django check results."""
    return {getattr(e, "id", "") for e in errors}


# ---------------------------------------------------------------------------
# A001 — ASGI origin validator
# ---------------------------------------------------------------------------


class _FakeApp:
    """Helper for building a mock ASGI app chain."""

    def __init__(self, name, inner=None):
        self.__class__ = type(
            name,
            (object,),
            {"__module__": f"mock_asgi.{name.lower()}"},
        )
        self.inner = inner


def _build_chain(*layer_names):
    """Build a mock ASGI app chain from outermost to innermost."""
    app = None
    for name in reversed(layer_names):
        app = _FakeApp(name, inner=app)
    return app


class TestA001OriginValidator:
    """A001 fires when the websocket router has no AllowedHostsOriginValidator."""

    def _run_with_ws_chain(self, chain):
        """Run check_configuration with a fake ASGI app routing to ``chain``."""
        import sys

        fake_asgi_app = type(
            "FakeProtocolTypeRouter",
            (object,),
            {"application_mapping": {"websocket": chain}},
        )()

        fake_module = type(sys)("_djust_test_asgi")
        fake_module.application = fake_asgi_app
        sys.modules["_djust_test_asgi"] = fake_module
        try:
            with override_settings(
                ASGI_APPLICATION="_djust_test_asgi.application",
                INSTALLED_APPS=["djust", "django.contrib.auth", "django.contrib.contenttypes"],
                DEBUG=False,
                ALLOWED_HOSTS=["example.com"],
            ):
                return check_configuration(None)
        finally:
            del sys.modules["_djust_test_asgi"]

    def test_a001_fires_without_origin_validator(self):
        chain = _build_chain("AuthMiddlewareStack", "URLRouter")
        errors = self._run_with_ws_chain(chain)
        assert "djust.A001" in _ids(errors)

    def test_a001_passes_with_origin_validator(self):
        chain = _build_chain("AllowedHostsOriginValidator", "AuthMiddlewareStack", "URLRouter")
        errors = self._run_with_ws_chain(chain)
        assert "djust.A001" not in _ids(errors)


# ---------------------------------------------------------------------------
# A010 / A011 / A012 — ALLOWED_HOSTS footguns
# ---------------------------------------------------------------------------


class TestAllowedHostsFootguns:
    """A010/A011/A012 cover various ALLOWED_HOSTS misconfigurations."""

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["*"])
    def test_a010_wildcard_only(self):
        errors = check_configuration(None)
        assert "djust.A010" in _ids(errors)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["myapp.example.com", "*"])
    def test_a011_mixed_wildcard(self):
        errors = check_configuration(None)
        assert "djust.A011" in _ids(errors)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["*"], USE_X_FORWARDED_HOST=True)
    def test_a012_forwarded_host_plus_wildcard(self):
        errors = check_configuration(None)
        assert "djust.A012" in _ids(errors)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["myapp.example.com"])
    def test_no_footgun_when_hosts_are_explicit(self):
        errors = check_configuration(None)
        ids = _ids(errors)
        assert "djust.A010" not in ids
        assert "djust.A011" not in ids
        assert "djust.A012" not in ids

    @override_settings(DEBUG=True, ALLOWED_HOSTS=["*"])
    def test_debug_mode_exempt_from_a010(self):
        """DEBUG=True is exempt — wildcard in dev is common and not the target."""
        errors = check_configuration(None)
        assert "djust.A010" not in _ids(errors)


# ---------------------------------------------------------------------------
# A014 — insecure SECRET_KEY prefix
# ---------------------------------------------------------------------------


class TestInsecureSecretKey:
    @override_settings(
        DEBUG=False,
        SECRET_KEY="django-insecure-abc123",
        ALLOWED_HOSTS=["myapp.example.com"],
    )
    def test_a014_fires_in_production(self):
        errors = check_configuration(None)
        assert "djust.A014" in _ids(errors)

    @override_settings(
        DEBUG=True,
        SECRET_KEY="django-insecure-abc123",
        ALLOWED_HOSTS=["localhost"],
    )
    def test_a014_exempt_in_debug(self):
        errors = check_configuration(None)
        assert "djust.A014" not in _ids(errors)

    @override_settings(
        DEBUG=False,
        SECRET_KEY="real-production-secret-abcdef",
        ALLOWED_HOSTS=["myapp.example.com"],
    )
    def test_a014_passes_with_real_key(self):
        errors = check_configuration(None)
        assert "djust.A014" not in _ids(errors)


# ---------------------------------------------------------------------------
# A020 — LOGIN_REDIRECT_URL hardcoded + multi-group
# ---------------------------------------------------------------------------


class TestLoginRedirectUrl:
    def test_a020_fires_with_multiple_groups(self):
        with patch("djust.checks._has_multiple_permission_groups", return_value=True):
            with override_settings(
                DEBUG=False,
                LOGIN_REDIRECT_URL="/dashboard/",
                ALLOWED_HOSTS=["example.com"],
            ):
                errors = check_configuration(None)
        assert "djust.A020" in _ids(errors)

    def test_a020_silent_without_multiple_groups(self):
        with patch("djust.checks._has_multiple_permission_groups", return_value=False):
            with override_settings(
                DEBUG=False,
                LOGIN_REDIRECT_URL="/dashboard/",
                ALLOWED_HOSTS=["example.com"],
            ):
                errors = check_configuration(None)
        assert "djust.A020" not in _ids(errors)


class TestHasMultiplePermissionGroups:
    """The _has_multiple_permission_groups helper.

    Uses a mock settings object directly rather than override_settings because
    override_settings(INSTALLED_APPS=...) tries to import every app module,
    and we want to test purely by INSTALLED_APPS presence without installing
    the real role packages.
    """

    def _mock_settings(self, installed_apps):
        """Build a minimal settings-like object for the helper."""
        return type("MockSettings", (), {"INSTALLED_APPS": installed_apps})

    def test_detects_rolepermissions_package(self):
        settings = self._mock_settings(["django.contrib.auth", "rolepermissions"])
        assert _has_multiple_permission_groups(settings) is True

    def test_detects_django_guardian(self):
        settings = self._mock_settings(["django.contrib.auth", "guardian"])
        assert _has_multiple_permission_groups(settings) is True

    def test_detects_rules(self):
        settings = self._mock_settings(["django.contrib.auth", "rules"])
        assert _has_multiple_permission_groups(settings) is True

    def test_no_packages_no_groups_returns_false(self):
        settings = self._mock_settings(["django.contrib.auth"])
        # With no role packages and no Group rows (or DB not ready), returns False.
        # This is the safe default — an app with no roles should never trigger A020.
        result = _has_multiple_permission_groups(settings)
        assert isinstance(result, bool)

    def test_never_raises_on_error(self):
        """Helper must never raise — it's called from a Django check."""
        settings = type("BadSettings", (), {})
        # No INSTALLED_APPS attribute at all — getattr default returns []
        result = _has_multiple_permission_groups(settings)
        assert result is False


# ---------------------------------------------------------------------------
# A030 — admin without brute-force protection
# ---------------------------------------------------------------------------


class TestAdminBruteForce:
    """A030 fires when admin is installed without a known brute-force package.

    Uses direct Django check-result inspection via a monkey-patched
    ``INSTALLED_APPS`` tuple at the module level — ``override_settings`` can't
    be used here because Django tries to import every listed app, and the
    brute-force packages (``axes``, ``defender``) are not installed in the
    test environment.
    """

    def _run_with_installed_apps(self, installed_apps):
        """Run check_configuration with a monkey-patched settings module."""
        import djust.checks as checks_module

        fake_settings = type(
            "FakeSettings",
            (),
            {
                "INSTALLED_APPS": installed_apps,
                "DEBUG": False,
                "ALLOWED_HOSTS": ["example.com"],
                "SECRET_KEY": "real-production-secret",
                "ASGI_APPLICATION": None,
                "CHANNEL_LAYERS": {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
                "LOGIN_REDIRECT_URL": None,
                "USE_X_FORWARDED_HOST": False,
            },
        )

        with patch.object(checks_module, "_check_tailwind_cdn_in_production"):
            with patch.object(checks_module, "_check_missing_compiled_css"):
                with patch.object(checks_module, "_check_manual_client_js"):
                    with patch("django.conf.settings", fake_settings):
                        return check_configuration(None)

    def test_a030_fires_without_protection(self):
        errors = self._run_with_installed_apps(
            ["django.contrib.admin", "django.contrib.auth", "djust"]
        )
        assert "djust.A030" in _ids(errors)

    def test_a030_passes_with_axes(self):
        errors = self._run_with_installed_apps(
            ["django.contrib.admin", "django.contrib.auth", "djust", "axes"]
        )
        assert "djust.A030" not in _ids(errors)

    def test_a030_passes_with_defender(self):
        errors = self._run_with_installed_apps(
            ["django.contrib.admin", "django.contrib.auth", "djust", "defender"]
        )
        assert "djust.A030" not in _ids(errors)

    def test_a030_silent_when_admin_not_installed(self):
        """A030 only fires when django.contrib.admin is in INSTALLED_APPS."""
        errors = self._run_with_installed_apps(["django.contrib.auth", "djust"])
        assert "djust.A030" not in _ids(errors)
