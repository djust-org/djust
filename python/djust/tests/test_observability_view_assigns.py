"""
Tests for Phase 7.1 — /_djust/observability/view_assigns/ endpoint.
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from djust.observability.registry import _clear_registry, register_view
from djust.observability.views import view_assigns


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class _FakeView:
    """Stand-in matching the contract get_state() expects."""

    def __init__(self, count=0, label="test"):
        self.count = count
        self.label = label
        # Private attrs should NOT appear in output.
        self._websocket_session_id = "abc"
        self._private = "hidden"

    def get_state(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and not callable(v)}


class _ExplodingView(_FakeView):
    def get_state(self):
        raise TypeError("boom — non-serializable value")


@override_settings(DEBUG=True)
def test_view_assigns_returns_state_for_registered_session():
    view = _FakeView(count=7, label="counter")
    register_view("session-1", view)

    rf = RequestFactory()
    resp = view_assigns(rf.get("/?session_id=session-1"))
    assert resp.status_code == 200

    data = json.loads(resp.content)
    assert data["session_id"] == "session-1"
    assert data["view_class"] == "_FakeView"
    assert data["assigns"] == {"count": 7, "label": "counter"}


@override_settings(DEBUG=True)
def test_view_assigns_excludes_private_and_callable_attrs():
    view = _FakeView()
    register_view("s", view)

    rf = RequestFactory()
    resp = view_assigns(rf.get("/?session_id=s"))
    data = json.loads(resp.content)
    assert "_private" not in data["assigns"]
    assert "_websocket_session_id" not in data["assigns"]


@override_settings(DEBUG=True)
def test_view_assigns_400_when_session_id_missing():
    rf = RequestFactory()
    resp = view_assigns(rf.get("/"))
    assert resp.status_code == 400


@override_settings(DEBUG=True)
def test_view_assigns_404_when_session_unknown():
    rf = RequestFactory()
    resp = view_assigns(rf.get("/?session_id=never-registered"))
    assert resp.status_code == 404


@override_settings(DEBUG=False)
def test_view_assigns_404_when_debug_off():
    view = _FakeView()
    register_view("s", view)
    rf = RequestFactory()
    resp = view_assigns(rf.get("/?session_id=s"))
    assert resp.status_code == 404


@override_settings(DEBUG=True)
def test_view_assigns_falls_back_when_get_state_raises():
    """If get_state() can't serialize, return repr()s instead of 500."""
    view = _ExplodingView(count=42)
    register_view("s", view)
    rf = RequestFactory()
    resp = view_assigns(rf.get("/?session_id=s"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    # Fallback is repr()'d strings — count=42 becomes "42"
    assert "count" in data["assigns"]
    assert data["assigns"]["count"] == "42"
