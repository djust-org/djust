"""Tests for djust.urls (B7 iter B — DEBUG-gated route registration, #1562).

Covers the acceptance criterion: "DEBUG-gated route registration —
djust.urls does NOT include the replay path when DEBUG=False unless
the prod opt-in is set."

``djust.urls.urlpatterns`` is computed once at import time (same as any
Django urlconf module — Django caches the resolved module). These tests
reload the module under ``override_settings`` to exercise both branches
of the gate; an autouse fixture reloads it once more after every test
so the module's live state reflects the ambient test-settings DEBUG
value again, keeping this file from poisoning any other test that
happens to import ``djust.urls``.
"""

from __future__ import annotations

import importlib

import pytest
from django.test import override_settings
from django.urls import include, path as url_path

import djust.urls as djust_urls


@pytest.fixture(autouse=True)
def _restore_djust_urls_after():
    yield
    importlib.reload(djust_urls)


class TestDebugGatedRegistration:
    @override_settings(DEBUG=False)
    def test_route_omitted_when_debug_false(self):
        importlib.reload(djust_urls)
        assert djust_urls.urlpatterns == []

    @override_settings(DEBUG=True)
    def test_route_included_when_debug_true(self):
        importlib.reload(djust_urls)
        assert len(djust_urls.urlpatterns) == 1

    @override_settings(DEBUG=False, DJUST_BUG_CAPTURE_PROD_OPT_IN=True)
    def test_route_included_in_production_with_opt_in(self):
        importlib.reload(djust_urls)
        assert len(djust_urls.urlpatterns) == 1

    @override_settings(DEBUG=False, DJUST_BUG_CAPTURE_PROD_OPT_IN="yes")
    def test_prod_opt_in_must_be_literal_true(self):
        """Same defensive contract as bug_capture._enforce_prod_gate and
        bug_capture_views._prod_gate_open: a truthy-but-not-True value
        does NOT opt in."""
        importlib.reload(djust_urls)
        assert djust_urls.urlpatterns == []

    @override_settings(DEBUG=True)
    def test_registered_route_resolves_to_replay_view(self):
        from djust.bug_capture_views import replay_view

        importlib.reload(djust_urls)
        assert len(djust_urls.urlpatterns) == 1
        registered = djust_urls.urlpatterns[0]
        assert registered.callback is replay_view

    @override_settings(DEBUG=True)
    def test_route_pattern_matches_the_documented_path(self):
        """Sanity-check the URL pattern actually matches
        `/__djust__/replay/<blob>` — a self-contained urlconf resolves it
        the same way `test_observability_localhost_gate.py` verifies its
        own routes (local precedent), without depending on the ambient
        ROOT_URLCONF."""
        from django.urls import resolve
        from django.urls.exceptions import Resolver404

        importlib.reload(djust_urls)
        test_urlconf = type("TestUrlconf", (), {"urlpatterns": [url_path("", include(djust_urls))]})
        with override_settings(ROOT_URLCONF=test_urlconf):
            match = resolve("/__djust__/replay/djbug1.abc123")
            assert match.func is not None
            assert match.kwargs == {"blob": "djbug1.abc123"}
            with pytest.raises(Resolver404):
                resolve("/__djust__/replay/")
