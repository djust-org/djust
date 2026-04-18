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


class _FakeViewWithNonSerializable(_FakeView):
    """Has an attribute the framework considers non-serializable (like a
    live request or DB connection). Represents the DEBUG-mode common case.
    """

    def __init__(self):
        super().__init__(count=42, label="mixed")
        # Something that mimics a WSGIRequest — an object the framework's
        # _is_serializable will reject.
        self.request = object()

    def _is_serializable(self, value):
        # Mirror LiveView's semantics: reject objects of bare `object` type
        # (non-JSON-serializable) but accept the ints/strs/lists/dicts.
        try:
            import json

            json.dumps(value)
            return True
        except (TypeError, ValueError):
            return False

    def get_state(self):
        # Would raise in real LiveView when DEBUG=True because of self.request;
        # here we delegate to the framework-style check via super-equivalent.
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
def test_view_assigns_per_attr_fallback_keeps_serializable_native():
    """Partial serializability: good attrs stay native, bad ones get tagged.

    Previously any non-serializable attr forced an all-or-nothing blanket
    repr that lost all native values. Now each attr is handled independently.
    """
    view = _FakeViewWithNonSerializable()
    register_view("s", view)
    rf = RequestFactory()
    resp = view_assigns(rf.get("/?session_id=s"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    # Serializable attrs stay as their actual values.
    assert data["assigns"]["count"] == 42
    assert data["assigns"]["label"] == "mixed"
    # Non-serializable attr is tagged {_repr, _type} so the caller can see
    # at a glance that it fell back.
    request_field = data["assigns"]["request"]
    assert isinstance(request_field, dict)
    assert request_field["_type"] == "object"
    assert "_repr" in request_field
