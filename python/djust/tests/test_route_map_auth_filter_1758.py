"""Tests for the #1758 route-map auth filter (ADR-021 Stage 2).

``get_route_map_script(request)`` must never emit a login/permission-gated
route — or its view-class path — to a client that can't access it. Public
routes always emit; gated routes emit only when ``request.user`` satisfies the
requirement; the filter fails closed for anonymous / unknown callers.
"""

from __future__ import annotations

import json
import re

import tests.conftest  # noqa: F401  -- configure Django settings

import pytest
from django.test import override_settings
from django.urls import clear_url_caches, set_script_prefix

from djust.routing import _reset_route_map_cache, get_route_map_script

_AUTH_URLS = "tests.route_map_auth_test_urls"


@pytest.fixture(autouse=True)
def _reset_state():
    _reset_route_map_cache()
    clear_url_caches()
    yield
    _reset_route_map_cache()
    set_script_prefix("/")
    clear_url_caches()


class _FakeUser:
    def __init__(self, *, authenticated=True, perms=()):
        self.is_authenticated = authenticated
        self._perms = set(perms)

    def has_perms(self, perms):
        return set(perms).issubset(self._perms)


class _FakeRequest:
    def __init__(self, user=None):
        self.user = user


def _emitted_routes(request) -> dict:
    """Parse ``window.djust._routeMap = {...}`` out of the emitted <script>."""
    script = get_route_map_script(request)
    if not script:
        return {}
    m = re.search(r"_routeMap=(\{.*?\});", script)
    assert m, f"no _routeMap JSON in: {script!r}"
    return json.loads(m.group(1))


@override_settings(ROOT_URLCONF=_AUTH_URLS)
def test_anonymous_sees_only_public_routes():
    """An anonymous client must NOT receive any gated route (or its view path)."""
    routes = _emitted_routes(_FakeRequest(user=_FakeUser(authenticated=False)))
    assert "/public/" in routes
    assert "/login-attr/" not in routes  # login_required class attr
    assert "/perm-attr/" not in routes  # permission_required class attr
    assert "/deco/" not in routes  # login_required decorator wrap
    # The view-class paths of gated routes must not leak either.
    assert all("LoginAttrView" not in v and "PermAttrView" not in v for v in routes.values())


@override_settings(ROOT_URLCONF=_AUTH_URLS)
def test_authenticated_sees_login_gated_but_not_perm_gated():
    """A logged-in user without the perm gets login-gated routes but not the
    permission-gated one."""
    routes = _emitted_routes(_FakeRequest(user=_FakeUser(authenticated=True, perms=())))
    assert "/public/" in routes
    assert "/login-attr/" in routes
    assert "/deco/" in routes
    assert "/perm-attr/" not in routes  # lacks auth.view_user


@override_settings(ROOT_URLCONF=_AUTH_URLS)
def test_permitted_user_sees_everything():
    routes = _emitted_routes(
        _FakeRequest(user=_FakeUser(authenticated=True, perms=("auth.view_user",)))
    )
    assert {"/public/", "/login-attr/", "/perm-attr/", "/deco/"} <= set(routes)


@override_settings(ROOT_URLCONF=_AUTH_URLS)
def test_no_request_fails_closed():
    """No request/user → gated routes are omitted (fail closed); public stays."""
    routes = _emitted_routes(None)
    assert "/public/" in routes
    assert "/login-attr/" not in routes
    assert "/perm-attr/" not in routes
    assert "/deco/" not in routes


@override_settings(ROOT_URLCONF=_AUTH_URLS)
def test_public_route_view_path_still_present_for_anonymous():
    """The filter must not over-reach: a public route keeps its view path."""
    routes = _emitted_routes(_FakeRequest(user=_FakeUser(authenticated=False)))
    assert routes.get("/public/") == "tests.route_map_auth_test_urls.PublicView"
