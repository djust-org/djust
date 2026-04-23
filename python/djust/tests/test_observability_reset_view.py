"""
Tests for Phase 11 #42 — /reset_view_state/ endpoint.
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from djust.observability.registry import _clear_registry, register_view
from djust.observability.views import reset_view_state


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class _FakeView:
    """Stand-in that behaves like a LiveView for the reset endpoint.

    The endpoint calls `_lenient_assigns(view)` which needs
    `_is_serializable` or falls back to json.dumps — either works.
    """

    def __init__(self):
        self.counter = 0
        self.label = "initial"
        self._djust_mount_request = object()
        self._djust_mount_kwargs = {}

    def mount(self, request, **kwargs):
        self.counter = 0
        self.label = "initial"


class _ViewMissingMountArgs(_FakeView):
    def __init__(self):
        self.counter = 0
        # Deliberately no _djust_mount_request — simulates a view mounted
        # before the reset feature was wired.


class _ViewMountRaises(_FakeView):
    def mount(self, request, **kwargs):
        raise RuntimeError("mount exploded")


@override_settings(DEBUG=True)
def test_reset_restores_state_to_post_mount():
    view = _FakeView()
    view.counter = 42
    view.label = "dirty"
    register_view("s", view)

    rf = RequestFactory()
    resp = reset_view_state(rf.post("/?session_id=s"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["session_id"] == "s"
    assert data["view_class"] == "_FakeView"
    # After reset: counter+label back to mount defaults (0, "initial").
    assert data["assigns_after_reset"]["counter"] == 0
    assert data["assigns_after_reset"]["label"] == "initial"

    # Instance state also cleared/remounted.
    assert view.counter == 0
    assert view.label == "initial"


@override_settings(DEBUG=True)
def test_reset_preserves_private_attrs():
    view = _FakeView()
    view._websocket_session_id = "ws-1"
    view.counter = 99
    register_view("s", view)

    rf = RequestFactory()
    reset_view_state(rf.post("/?session_id=s"))
    # Private attrs stay (framework bookkeeping isn't disturbed).
    assert view._websocket_session_id == "ws-1"
    assert view._djust_mount_request is not None


@override_settings(DEBUG=True)
def test_reset_requires_post():
    view = _FakeView()
    register_view("s", view)
    rf = RequestFactory()
    resp = reset_view_state(rf.get("/?session_id=s"))
    assert resp.status_code == 405


@override_settings(DEBUG=True)
def test_reset_400_when_session_id_missing():
    rf = RequestFactory()
    resp = reset_view_state(rf.post("/"))
    assert resp.status_code == 400


@override_settings(DEBUG=True)
def test_reset_404_when_session_unknown():
    rf = RequestFactory()
    resp = reset_view_state(rf.post("/?session_id=never"))
    assert resp.status_code == 404


@override_settings(DEBUG=True)
def test_reset_409_when_mount_args_not_stashed():
    view = _ViewMissingMountArgs()
    register_view("s", view)
    rf = RequestFactory()
    resp = reset_view_state(rf.post("/?session_id=s"))
    assert resp.status_code == 409
    assert b"before reset_view_state was wired" in resp.content


@override_settings(DEBUG=True)
def test_reset_500_when_mount_raises():
    view = _ViewMountRaises()
    register_view("s", view)
    rf = RequestFactory()
    resp = reset_view_state(rf.post("/?session_id=s"))
    assert resp.status_code == 500
    data = json.loads(resp.content)
    # Error payload is sanitized (no exception class leak to client). Details
    # go to server logs. Verify the sanitized contract + session_id echo.
    assert "mount()" in data["error"]
    assert "server logs" in data["error"]
    assert data["session_id"] == "s"


@override_settings(DEBUG=False)
def test_reset_404_when_debug_off():
    view = _FakeView()
    register_view("s", view)
    rf = RequestFactory()
    resp = reset_view_state(rf.post("/?session_id=s"))
    assert resp.status_code == 404
