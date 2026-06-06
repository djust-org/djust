"""Tests for ``djust.routing.build_route_map_from_urlconf`` (#1733, ADR-021 Stage 1).

Each test encodes a doc claim from ``docs/website/guides/navigation.md`` and
``docs/adr/021-automatic-spa-navigation.md``:

* "route map auto-derived from the Django URLconf (every route whose callback
  resolves to a LiveView subclass)" →
  :func:`test_derives_liveview_routes` / :func:`test_ignores_non_liveview_routes`.
* "View-class resolution handles login_required-wrapped views" →
  :func:`test_resolves_login_required_wrapped_view`.
* "Django params ``<int:id>`` → ``:id``" → :func:`test_converts_path_params`.
* "applies the FORCE_SCRIPT_NAME sub-path prefix" →
  :func:`test_applies_force_script_name_prefix`.
* "descends into include() prefixes" → :func:`test_descends_into_includes`.
* "Return {} when no LiveView routes exist" → :func:`test_empty_when_no_liveviews`.
* "Cached at module level with a reset hook for tests" →
  :func:`test_cache_and_reset`.
"""

from __future__ import annotations

import tests.conftest  # noqa: F401  -- configure Django settings

from django.test import override_settings
from django.urls import clear_url_caches, set_script_prefix

import pytest

from djust.routing import build_route_map_from_urlconf, _reset_route_map_cache


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the derived-route-map cache + script prefix around each test."""
    _reset_route_map_cache()
    clear_url_caches()
    yield
    _reset_route_map_cache()
    set_script_prefix("/")
    clear_url_caches()


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_derives_liveview_routes():
    """Every route whose callback resolves to a LiveView is in the map."""
    route_map = build_route_map_from_urlconf()
    assert route_map["/dashboard/"] == "tests.route_map_test_urls.DashboardView"


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_ignores_non_liveview_routes():
    """Plain Django views must NOT appear in the derived route map."""
    route_map = build_route_map_from_urlconf()
    assert "/plain/" not in route_map
    # And no value should reference the plain view function.
    assert all("plain_django_view" not in v for v in route_map.values())


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_resolves_login_required_wrapped_view():
    """login_required(View.as_view()) resolves via __wrapped__.view_class."""
    route_map = build_route_map_from_urlconf()
    assert route_map["/secret/"] == "tests.route_map_test_urls.ProtectedView"


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_converts_path_params():
    """``<int:id>`` is converted to the JS-friendly ``:id`` form."""
    route_map = build_route_map_from_urlconf()
    assert route_map["/items/:id/"] == "tests.route_map_test_urls.ItemDetailView"
    # The raw Django form must NOT leak through.
    assert "/items/<int:id>/" not in route_map


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_descends_into_includes():
    """Nested include() patterns get the include prefix accumulated."""
    route_map = build_route_map_from_urlconf()
    assert route_map["/section/deep/"] == "tests.route_map_test_urls.NestedView"


@override_settings(
    ROOT_URLCONF="tests.route_map_test_urls",
    FORCE_SCRIPT_NAME="/mysite",
)
def test_applies_force_script_name_prefix():
    """FORCE_SCRIPT_NAME is prepended to every derived path.

    Production Django's BaseHandler calls set_script_prefix() from
    FORCE_SCRIPT_NAME at the start of each request; mirror that here.
    """
    set_script_prefix("/mysite/")
    clear_url_caches()
    _reset_route_map_cache()
    route_map = build_route_map_from_urlconf()
    assert route_map["/mysite/dashboard/"] == "tests.route_map_test_urls.DashboardView"
    assert route_map["/mysite/items/:id/"] == "tests.route_map_test_urls.ItemDetailView"
    # The un-prefixed form must NOT also be present.
    assert "/dashboard/" not in route_map


@override_settings(ROOT_URLCONF="tests.api_test_urls_unmounted")
def test_empty_when_no_liveviews():
    """A URLconf with no LiveView routes yields an empty map."""
    assert build_route_map_from_urlconf() == {}


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_cache_and_reset():
    """Result is cached at module level; _reset_route_map_cache() clears it."""
    first = build_route_map_from_urlconf()
    second = build_route_map_from_urlconf()
    # Same cached object identity (URLconf is static at runtime).
    assert first is second
    _reset_route_map_cache()
    third = build_route_map_from_urlconf()
    assert third is not first
    assert third == first
