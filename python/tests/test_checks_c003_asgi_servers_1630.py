"""Tests for #1630 — C003 must not fire when uvicorn or hypercorn is installed.

Reporter: djust's README + ``djust-start`` recommend uvicorn, but the
C003 check fired an INFO telling users to install daphne — forcing
every uvicorn-based project to permanently suppress C003. This file
pins the post-fix behavior: C003 only fires when NONE of
``{daphne, uvicorn, hypercorn}`` is importable.
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def asgi_settings(settings):
    """Minimal ASGI-ready settings shared by every test in this file."""
    settings.ASGI_APPLICATION = "myproject.asgi.application"
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.INSTALLED_APPS = ["django.contrib.staticfiles", "djust"]
    return settings


class TestC003AsgiServers1630:
    """C003 fires only when no ASGI server (daphne/uvicorn/hypercorn) is installed."""

    def test_c003_does_not_fire_when_uvicorn_installed(self, asgi_settings):
        """Reporter's exact case: uvicorn-based project, no daphne in INSTALLED_APPS."""

        def fake_find_spec(name):
            return object() if name == "uvicorn" else None

        from djust.checks import check_configuration

        with patch("importlib.util.find_spec", side_effect=fake_find_spec):
            errors = check_configuration(None)
            c003 = [e for e in errors if e.id == "djust.C003"]
            assert c003 == [], "C003 must NOT fire when uvicorn is installed (#1630). Got: %r" % [
                (e.msg, e.hint) for e in c003
            ]

    def test_c003_does_not_fire_when_hypercorn_installed(self, asgi_settings):
        """Hypercorn is also a valid ASGI server choice."""

        def fake_find_spec(name):
            return object() if name == "hypercorn" else None

        from djust.checks import check_configuration

        with patch("importlib.util.find_spec", side_effect=fake_find_spec):
            errors = check_configuration(None)
            c003 = [e for e in errors if e.id == "djust.C003"]
            assert c003 == []

    def test_c003_does_not_fire_when_daphne_installed_via_find_spec(self, asgi_settings):
        """Daphne short-circuits the find_spec probe at the start of the tuple."""

        def fake_find_spec(name):
            return object() if name == "daphne" else None

        from djust.checks import check_configuration

        with patch("importlib.util.find_spec", side_effect=fake_find_spec):
            errors = check_configuration(None)
            c003 = [e for e in errors if e.id == "djust.C003"]
            assert c003 == []

    def test_c003_fires_when_no_asgi_server_at_all(self, asgi_settings):
        """The legitimate trigger: no ASGI server installed anywhere."""
        from djust.checks import check_configuration

        with patch("importlib.util.find_spec", return_value=None):
            errors = check_configuration(None)
            c003 = [e for e in errors if e.id == "djust.C003"]
            assert len(c003) == 1, "Expected one C003 when no ASGI server is installed"
            # New wording (#1630) — covers all three server names, not just daphne
            assert "No ASGI server detected" in c003[0].msg
            assert "uvicorn" in c003[0].hint
            assert "hypercorn" not in c003[0].fix_hint  # we recommend uvicorn

    def test_c003_fix_hint_recommends_uvicorn(self, asgi_settings):
        """Reporter's intent: when we DO nag, we recommend the canonical pick."""
        from djust.checks import check_configuration

        with patch("importlib.util.find_spec", return_value=None):
            errors = check_configuration(None)
            c003 = [e for e in errors if e.id == "djust.C003"]
            assert len(c003) == 1
            assert "uvicorn" in c003[0].fix_hint

    def test_daphne_ordering_check_still_fires_when_daphne_installed_apps(self, asgi_settings):
        """Existing C003 ordering warning (daphne IS in INSTALLED_APPS, but after
        staticfiles) MUST still fire — broadening only affects the not-installed branch."""
        asgi_settings.INSTALLED_APPS = ["django.contrib.staticfiles", "daphne", "djust"]

        from djust.checks import check_configuration

        # No need to mock find_spec — this branch doesn't probe it
        errors = check_configuration(None)
        c003 = [e for e in errors if e.id == "djust.C003"]
        assert len(c003) == 1
        assert "before" in c003[0].msg

    def test_has_asgi_server_returns_true_when_uvicorn_resolves(self):
        """Unit test for the helper itself: find_spec('uvicorn') → True."""
        from djust.checks import _has_asgi_server

        def fake_find_spec(name):
            return object() if name == "uvicorn" else None

        with patch("importlib.util.find_spec", side_effect=fake_find_spec):
            assert _has_asgi_server() is True

    def test_has_asgi_server_returns_false_when_no_server(self):
        """Unit test for the helper: nothing installed → False."""
        from djust.checks import _has_asgi_server

        with patch("importlib.util.find_spec", return_value=None):
            assert _has_asgi_server() is False

    def test_has_asgi_server_swallows_find_spec_exceptions(self):
        """find_spec can raise ValueError on weird module names; helper must not crash."""
        from djust.checks import _has_asgi_server

        with patch("importlib.util.find_spec", side_effect=ValueError("bogus")):
            assert _has_asgi_server() is False
