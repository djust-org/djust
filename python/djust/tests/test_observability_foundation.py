"""
Tests for Phase 7.0 observability foundation: session registry,
localhost-only middleware, and health endpoint.
"""

from __future__ import annotations

import gc

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from djust.observability import (
    get_registered_session_count,
    get_view_for_session,
    register_view,
    unregister_view,
)
from djust.observability.middleware import (
    LocalhostOnlyObservabilityMiddleware,
    is_localhost,
)
from djust.observability.registry import _clear_registry
from djust.observability.views import health


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure each test starts with an empty registry."""
    _clear_registry()
    yield
    _clear_registry()


class _FakeView:
    """Stand-in for a LiveView — registry only cares about object identity."""

    def __init__(self, name="fake"):
        self.name = name


# --- Registry --------------------------------------------------------------


def test_register_and_lookup():
    view = _FakeView()
    register_view("session-1", view)
    assert get_view_for_session("session-1") is view


def test_unregister_removes_entry():
    view = _FakeView()
    register_view("session-1", view)
    unregister_view("session-1")
    assert get_view_for_session("session-1") is None


def test_unregister_missing_session_noop():
    unregister_view("never-registered")  # Must not raise.


def test_registered_count_tracks_inserts_and_removes():
    assert get_registered_session_count() == 0
    v1, v2 = _FakeView(), _FakeView()
    register_view("a", v1)
    register_view("b", v2)
    assert get_registered_session_count() == 2
    unregister_view("a")
    assert get_registered_session_count() == 1


def test_registry_holds_weak_references():
    """Entry must evaporate when the view is GC'd — guards against leaks
    if consumers forget to call unregister_view()."""
    view = _FakeView()
    register_view("ephemeral", view)
    assert get_view_for_session("ephemeral") is view
    del view
    gc.collect()
    assert get_view_for_session("ephemeral") is None


def test_re_register_replaces_entry():
    v1, v2 = _FakeView("first"), _FakeView("second")
    register_view("same-session", v1)
    register_view("same-session", v2)
    assert get_view_for_session("same-session") is v2


# --- Middleware ------------------------------------------------------------


def _dummy_get_response(request):
    return HttpResponse("ok")


def test_localhost_middleware_allows_127_0_0_1():
    rf = RequestFactory()
    req = rf.get("/_djust/observability/health/", REMOTE_ADDR="127.0.0.1")
    mw = LocalhostOnlyObservabilityMiddleware(_dummy_get_response)
    resp = mw(req)
    assert resp.status_code == 200


def test_localhost_middleware_allows_ipv6_loopback():
    rf = RequestFactory()
    req = rf.get("/_djust/observability/health/", REMOTE_ADDR="::1")
    mw = LocalhostOnlyObservabilityMiddleware(_dummy_get_response)
    assert mw(req).status_code == 200


def test_localhost_middleware_rejects_lan_address():
    rf = RequestFactory()
    req = rf.get("/_djust/observability/health/", REMOTE_ADDR="192.168.1.5")
    mw = LocalhostOnlyObservabilityMiddleware(_dummy_get_response)
    resp = mw(req)
    assert resp.status_code == 403
    assert b"localhost-only" in resp.content


def test_localhost_middleware_ignores_non_observability_paths():
    """Requests to other paths flow through regardless of IP."""
    rf = RequestFactory()
    req = rf.get("/some/other/path/", REMOTE_ADDR="192.168.1.5")
    mw = LocalhostOnlyObservabilityMiddleware(_dummy_get_response)
    assert mw(req).status_code == 200


def test_is_localhost_helper():
    rf = RequestFactory()
    assert is_localhost(rf.get("/", REMOTE_ADDR="127.0.0.1"))
    assert is_localhost(rf.get("/", REMOTE_ADDR="::1"))
    assert not is_localhost(rf.get("/", REMOTE_ADDR="10.0.0.1"))
    assert not is_localhost(rf.get("/", REMOTE_ADDR=""))


# --- Health endpoint -------------------------------------------------------


@override_settings(DEBUG=True)
def test_health_endpoint_debug_on():
    rf = RequestFactory()
    resp = health(rf.get("/_djust/observability/health/"))
    assert resp.status_code == 200
    import json

    data = json.loads(resp.content)
    assert data["ok"] is True
    assert data["debug"] is True
    assert data["registered_sessions"] == 0


@override_settings(DEBUG=True)
def test_health_reports_registered_session_count():
    # Hold strong references so the weakref registry keeps the entries.
    views = [_FakeView(), _FakeView()]
    register_view("a", views[0])
    register_view("b", views[1])
    rf = RequestFactory()
    resp = health(rf.get("/_djust/observability/health/"))
    import json

    data = json.loads(resp.content)
    assert data["registered_sessions"] == 2


@override_settings(DEBUG=False)
def test_health_endpoint_debug_off_returns_404():
    """In production the endpoint should 404 — don't leak its existence."""
    rf = RequestFactory()
    resp = health(rf.get("/_djust/observability/health/"))
    assert resp.status_code == 404
