"""An app's system checks must not outlive the test that installed it (#3177).

The failure this pins: installing daphne in one test left ``daphne.E001`` in
Django's global check registry, and a later ``collectstatic`` with checks on
raised ``SystemCheckError`` for it (#3169). These tests drive the real
registry through ``override_settings``, the same mechanism the leaking tests
use.
"""

from __future__ import annotations

import pytest
from django.core import checks
from django.core.checks.registry import registry
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import override_settings

from tests.check_registry import isolated_check_registry

pytest.importorskip("daphne")

STATICFILES_ONLY = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
]
DAPHNE_AFTER_STATICFILES = STATICFILES_ONLY + ["daphne"]


def _daphne_errors():
    return [e for e in checks.run_checks(tags=[checks.Tags.staticfiles]) if e.id == "daphne.E001"]


def _daphne_check_registered() -> bool:
    return any(c.__module__ == "daphne.checks" for c in registry.registered_checks)


def test_daphne_check_is_dropped_once_daphne_is_uninstalled():
    with isolated_check_registry():
        with override_settings(INSTALLED_APPS=DAPHNE_AFTER_STATICFILES):
            assert _daphne_check_registered()
            assert _daphne_errors(), "precondition: daphne after staticfiles fires E001"
    assert not _daphne_check_registered()
    with override_settings(INSTALLED_APPS=STATICFILES_ONLY):
        assert _daphne_errors() == []


def test_collectstatic_with_checks_is_clean_after_a_daphne_test(tmp_path):
    """The #3169 shape: a daphne test, then collectstatic with skip_checks=False."""
    with isolated_check_registry():
        with override_settings(INSTALLED_APPS=DAPHNE_AFTER_STATICFILES):
            pass
    with override_settings(
        INSTALLED_APPS=STATICFILES_ONLY, STATIC_ROOT=str(tmp_path), STATICFILES_DIRS=[]
    ):
        try:
            call_command("collectstatic", interactive=False, verbosity=0, skip_checks=False)
        except SystemCheckError as exc:  # pragma: no cover - the failure being pinned
            pytest.fail("collectstatic ran a leaked check: %s" % exc)


def test_reinstalling_daphne_registers_its_check_again():
    with isolated_check_registry():
        with override_settings(INSTALLED_APPS=DAPHNE_AFTER_STATICFILES):
            pass
    with isolated_check_registry():
        with override_settings(INSTALLED_APPS=DAPHNE_AFTER_STATICFILES):
            assert _daphne_errors(), "daphne's ready() must re-register its check"


def test_checks_of_installed_apps_survive_and_removed_checks_come_back():
    def probe(app_configs, **kwargs):  # module: tests.* -- not an installed app
        return []

    djust_check = next(c for c in registry.registered_checks if c.__module__.startswith("djust."))
    with isolated_check_registry():
        checks.register(probe)
        registry.registered_checks.discard(djust_check)
    assert probe not in registry.registered_checks
    assert djust_check in registry.registered_checks


def test_a_check_first_registered_inside_a_test_by_an_installed_app_is_kept():
    """A djust check module imported for the first time inside a test is never
    imported again, so its checks must survive the test."""

    def late(app_configs, **kwargs):
        return []

    late.__module__ = "djust.checks.late_import_probe"
    try:
        with isolated_check_registry():
            checks.register(late)
        assert late in registry.registered_checks
    finally:
        registry.registered_checks.discard(late)


def test_a_partial_of_an_installed_apps_check_is_kept():
    """#3213 review 5: ``functools.partial`` reports ``__module__ ==
    "functools"``; the fixture must judge it by the function it wraps."""
    import functools

    djust_check = next(c for c in registry.registered_checks if c.__module__.startswith("djust."))
    wrapped = functools.partial(djust_check)
    try:
        with isolated_check_registry():
            checks.register(wrapped, "djust")
        assert wrapped in registry.registered_checks
    finally:
        registry.registered_checks.discard(wrapped)


def test_a_partial_of_a_non_app_check_is_still_dropped():
    import functools

    def probe(app_configs, **kwargs):  # module: tests.* -- not an installed app
        return []

    wrapped = functools.partial(probe)
    with isolated_check_registry():
        checks.register(wrapped)
    assert wrapped not in registry.registered_checks
