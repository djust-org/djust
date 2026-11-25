"""
Tests for Phase 7.1 — /_djust/observability/view_assigns/ endpoint.
"""

from __future__ import annotations

import json

import pytest
from django.test import override_settings

from .conftest import observability_request_factory

from djust import LiveView
from djust.decorators import state
from djust.observability.registry import _clear_registry, register_view
from djust.observability.views import view_assigns


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class _CounterView(LiveView):
    """An explicit view: only its declared fields are reported."""

    exposure_policy = "explicit"
    count = state(0, client=True)
    label = state("test", client=True)
    secret = state("hidden")

    def mount(self, request, **kwargs):
        self._private = "hidden"


@override_settings(DEBUG=True)
def test_view_assigns_returns_state_for_registered_session():
    view = _CounterView()
    view.count = 7
    view.label = "counter"
    register_view("session-1", view)

    rf = observability_request_factory()
    resp = view_assigns(rf.get("/?session_id=session-1"))
    assert resp.status_code == 200

    data = json.loads(resp.content)
    assert data["session_id"] == "session-1"
    assert data["view_class"] == "_CounterView"
    assert data["assigns"] == {"count": 7, "label": "counter", "secret": "[redacted]"}


@override_settings(DEBUG=True)
def test_view_assigns_excludes_private_and_callable_attrs():
    view = _CounterView()
    register_view("s", view)

    rf = observability_request_factory()
    resp = view_assigns(rf.get("/?session_id=s"))
    data = json.loads(resp.content)
    assert "_private" not in data["assigns"]
    assert "_websocket_session_id" not in data["assigns"]
    # A field that is not client-visible is reported as redacted, never its value.
    assert data["assigns"]["secret"] == "[redacted]"


@override_settings(DEBUG=True)
def test_view_assigns_400_when_session_id_missing():
    rf = observability_request_factory()
    resp = view_assigns(rf.get("/"))
    assert resp.status_code == 400


@override_settings(DEBUG=True)
def test_view_assigns_404_when_session_unknown():
    rf = observability_request_factory()
    resp = view_assigns(rf.get("/?session_id=never-registered"))
    assert resp.status_code == 404


@override_settings(DEBUG=False)
def test_view_assigns_404_when_debug_off():
    view = _CounterView()
    register_view("s", view)
    rf = observability_request_factory()
    resp = view_assigns(rf.get("/?session_id=s"))
    assert resp.status_code == 404
