"""
Regression tests for #1556 — djust.C014 detects multi-tenant ASGI deploys
missing TENANT_LIMIT_SET_CALLS.

Under ASGI + django-tenants, every WS event re-enters TenantMainMiddleware
and emits a redundant `SET search_path`. Without TENANT_LIMIT_SET_CALLS,
LiveView traffic (tick_interval, push_to_view, presence, @notify_on_save)
can exhaust the Postgres connection pool. C014 fires at startup when the
3 trigger conditions are all met.

Note: tests patch the settings the check reads directly rather than going
through ``override_settings(INSTALLED_APPS=...)``. Adding a string like
``"django_tenants"`` to INSTALLED_APPS via override_settings triggers
Django's app loader to actually import the module; we'd then need
django-tenants as a test dependency just to assert what the check does
on its absence/presence of the string. Direct patching keeps the check
contract observable without the dependency hop.
"""

from djust.checks import check_configuration


def _ids(errors):
    return {getattr(e, "id", "") for e in errors}


def _settings(**overrides):
    """Patch django.conf.settings attributes for the duration of a `with`.

    Each kwarg sets/replaces an attribute on `settings`; on exit the
    original value (or absence) is restored.
    """
    from django.conf import settings as dj_settings

    # `mock.patch.multiple` requires the target object to have the attrs
    # already; for absent attrs use a per-key approach.
    return _SettingsPatcher(dj_settings, overrides)


class _SettingsPatcher:
    _SENTINEL = object()

    def __init__(self, target, overrides):
        self.target = target
        self.overrides = overrides
        self.originals = {}

    def __enter__(self):
        for key, value in self.overrides.items():
            self.originals[key] = getattr(self.target, key, self._SENTINEL)
            setattr(self.target, key, value)
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, original in self.originals.items():
            if original is self._SENTINEL:
                try:
                    delattr(self.target, key)
                except AttributeError:
                    pass
            else:
                setattr(self.target, key, original)


# Baseline INSTALLED_APPS without django_tenants — used for negative cases.
_PLAIN_INSTALLED_APPS = ["djust"]
_TENANTS_INSTALLED_APPS = ["djust", "django_tenants"]


class TestC014TriggerConditions:
    """C014 fires only when all 3 conditions hold."""

    def test_fires_when_all_three_conditions_met_via_installed_apps(self):
        """Baseline: django_tenants in INSTALLED_APPS + ASGI + flag unset → C014."""
        with _settings(
            INSTALLED_APPS=_TENANTS_INSTALLED_APPS,
            ASGI_APPLICATION="myproject.asgi.application",
            TENANT_LIMIT_SET_CALLS=False,
        ):
            errors = check_configuration(None)
        assert "djust.C014" in _ids(errors)

    def test_fires_when_tenant_model_set_without_installed_app(self):
        """TENANT_MODEL alone is enough signal — INSTALLED_APPS string not required."""
        with _settings(
            INSTALLED_APPS=_PLAIN_INSTALLED_APPS,
            TENANT_MODEL="tenants.Tenant",
            ASGI_APPLICATION="myproject.asgi.application",
            TENANT_LIMIT_SET_CALLS=False,
        ):
            errors = check_configuration(None)
        assert "djust.C014" in _ids(errors)

    def test_fires_when_flag_unset(self):
        """Unset flag is the prod-config default — must trigger C014."""
        # No TENANT_LIMIT_SET_CALLS override → check reads `getattr(..., False)`.
        with _settings(
            INSTALLED_APPS=_TENANTS_INSTALLED_APPS,
            ASGI_APPLICATION="myproject.asgi.application",
        ):
            # Defensively unset in case a parent test left it set.
            from django.conf import settings as dj_settings

            had_flag = hasattr(dj_settings, "TENANT_LIMIT_SET_CALLS")
            old_flag = getattr(dj_settings, "TENANT_LIMIT_SET_CALLS", None)
            if had_flag:
                delattr(dj_settings, "TENANT_LIMIT_SET_CALLS")
            try:
                errors = check_configuration(None)
            finally:
                if had_flag:
                    setattr(dj_settings, "TENANT_LIMIT_SET_CALLS", old_flag)
        assert "djust.C014" in _ids(errors)


class TestC014NegativeCases:
    """C014 stays silent when the misconfiguration isn't present."""

    def test_does_not_fire_without_django_tenants(self):
        """Single-tenant apps don't need this flag — no warning."""
        with _settings(
            INSTALLED_APPS=_PLAIN_INSTALLED_APPS,
            ASGI_APPLICATION="myproject.asgi.application",
            TENANT_LIMIT_SET_CALLS=False,
        ):
            # Ensure TENANT_MODEL isn't set from another test.
            from django.conf import settings as dj_settings

            had_model = hasattr(dj_settings, "TENANT_MODEL")
            old_model = getattr(dj_settings, "TENANT_MODEL", None)
            if had_model:
                delattr(dj_settings, "TENANT_MODEL")
            try:
                errors = check_configuration(None)
            finally:
                if had_model:
                    setattr(dj_settings, "TENANT_MODEL", old_model)
        assert "djust.C014" not in _ids(errors)

    def test_does_not_fire_when_flag_true(self):
        """The fix: flag enabled — C014 is satisfied."""
        with _settings(
            INSTALLED_APPS=_TENANTS_INSTALLED_APPS,
            ASGI_APPLICATION="myproject.asgi.application",
            TENANT_LIMIT_SET_CALLS=True,
        ):
            errors = check_configuration(None)
        assert "djust.C014" not in _ids(errors)

    def test_does_not_fire_without_asgi_application(self):
        """Without ASGI, C001 fires instead. C014 is ASGI-specific."""
        with _settings(
            INSTALLED_APPS=_TENANTS_INSTALLED_APPS,
            ASGI_APPLICATION=None,
            TENANT_LIMIT_SET_CALLS=False,
        ):
            errors = check_configuration(None)
        assert "djust.C014" not in _ids(errors)


class TestC014Suppression:
    """C014 honors DJUST_CONFIG['suppress_checks']."""

    def test_suppressed_by_short_id(self):
        with _settings(
            INSTALLED_APPS=_TENANTS_INSTALLED_APPS,
            ASGI_APPLICATION="myproject.asgi.application",
            TENANT_LIMIT_SET_CALLS=False,
            DJUST_CONFIG={"suppress_checks": ["C014"]},
        ):
            errors = check_configuration(None)
        assert "djust.C014" not in _ids(errors)

    def test_suppressed_by_full_id(self):
        with _settings(
            INSTALLED_APPS=_TENANTS_INSTALLED_APPS,
            ASGI_APPLICATION="myproject.asgi.application",
            TENANT_LIMIT_SET_CALLS=False,
            DJUST_CONFIG={"suppress_checks": ["djust.C014"]},
        ):
            errors = check_configuration(None)
        assert "djust.C014" not in _ids(errors)


class TestC014HintQuality:
    """The warning surfaces actionable guidance."""

    def _fire(self):
        with _settings(
            INSTALLED_APPS=_TENANTS_INSTALLED_APPS,
            ASGI_APPLICATION="myproject.asgi.application",
            TENANT_LIMIT_SET_CALLS=False,
        ):
            errors = check_configuration(None)
        return next(e for e in errors if getattr(e, "id", "") == "djust.C014")

    def test_hint_names_the_setting(self):
        assert "TENANT_LIMIT_SET_CALLS" in self._fire().hint

    def test_hint_references_originating_issue(self):
        assert "#1556" in self._fire().hint

    def test_fix_hint_provides_copy_pasteable_line(self):
        assert "TENANT_LIMIT_SET_CALLS = True" in self._fire().fix_hint
