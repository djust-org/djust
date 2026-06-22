"""Regression tests for #1865 — `_get_project_app_dirs()` discovery filter.

The app-dir discovery helper used to exclude any path that *contained*
``/djust/`` (or ended with ``djust``), to avoid linting the framework's own
templates when the checks run. That filter is too broad: a downstream project
(e.g. ``examples/demo_project``) checked from INSIDE the djust repo checkout
lives under a ``…/djust/…`` path, so it was silently dropped → S009 early-
returned and S011 saw only a fraction of templates (dogfooding blind spot).

The fix tightens the exclusion to skip ONLY djust's actual package directory
(``os.path.dirname(djust.__file__)``), so the framework's own templates stay
excluded while a consumer app that merely lives under a ``/djust/``-named path
is discovered.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

import djust
from djust.checks import _get_project_app_dirs


def _fake_config(path):
    """Minimal stand-in for a Django AppConfig (only ``.path`` is read)."""
    return SimpleNamespace(path=path)


class TestGetProjectAppDirsDiscovery1865:
    """Exercises the real ``_get_project_app_dirs`` filter, not a mock of it."""

    def test_consumer_app_under_djust_path_is_discovered(self, tmp_path):
        """A project app living under a ``/djust/``-containing path is returned.

        FAILS pre-fix: the ``"/djust/" in path`` filter drops it → [].
        PASSES post-fix: only the actual djust package dir is excluded.
        """
        # Simulate examples/demo_project/demo_app checked from inside the repo:
        # a real directory whose absolute path contains ``/djust/``.
        consumer = tmp_path / "djust" / "examples" / "demo_project" / "demo_app"
        consumer.mkdir(parents=True)
        consumer_path = str(consumer)
        assert "/djust/" in consumer_path  # precondition: triggers old filter

        with patch("django.apps.apps.get_app_configs", return_value=[_fake_config(consumer_path)]):
            dirs = _get_project_app_dirs()

        assert consumer_path in dirs, (
            "consumer app under a /djust/ path must be discovered, got %r" % (dirs,)
        )

    def test_app_path_ending_in_djust_is_discovered(self, tmp_path):
        """A consumer app whose own dir is literally named ``djust`` is returned.

        FAILS pre-fix: the ``path.endswith("djust")`` filter drops it.
        """
        consumer = tmp_path / "myproject" / "djust"
        consumer.mkdir(parents=True)
        consumer_path = str(consumer)
        assert consumer_path.endswith("djust")  # precondition: triggers old filter

        with patch("django.apps.apps.get_app_configs", return_value=[_fake_config(consumer_path)]):
            dirs = _get_project_app_dirs()

        assert consumer_path in dirs, (
            "consumer app dir named 'djust' must be discovered, got %r" % (dirs,)
        )

    def test_djust_own_package_dir_is_still_excluded(self):
        """Intent preserved: djust's OWN package dir is NOT returned.

        This must keep PASSING post-fix — we don't want to start linting the
        framework's own templates.
        """
        djust_pkg = os.path.dirname(djust.__file__)

        with patch("django.apps.apps.get_app_configs", return_value=[_fake_config(djust_pkg)]):
            dirs = _get_project_app_dirs()

        assert djust_pkg not in dirs, "djust's own package dir must stay excluded, got %r" % (dirs,)

    def test_site_packages_app_is_still_excluded(self, tmp_path):
        """Intent preserved: third-party site-packages apps stay excluded."""
        third_party = tmp_path / "site-packages" / "someapp"
        third_party.mkdir(parents=True)
        third_party_path = str(third_party)

        with patch(
            "django.apps.apps.get_app_configs",
            return_value=[_fake_config(third_party_path)],
        ):
            dirs = _get_project_app_dirs()

        assert third_party_path not in dirs

    def test_djust_subpackage_dir_is_still_excluded(self):
        """A directory INSIDE the djust package (e.g. checks/) stays excluded."""
        djust_pkg = os.path.dirname(djust.__file__)
        checks_dir = os.path.join(djust_pkg, "checks")
        assert os.path.isdir(checks_dir)

        with patch(
            "django.apps.apps.get_app_configs",
            return_value=[_fake_config(checks_dir)],
        ):
            dirs = _get_project_app_dirs()

        assert checks_dir not in dirs, (
            "a subdir of the djust package must stay excluded, got %r" % (dirs,)
        )
