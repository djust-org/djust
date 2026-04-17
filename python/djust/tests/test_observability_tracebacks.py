"""
Tests for Phase 7.2 — traceback ring buffer + /last_traceback/ endpoint.
Also verifies that djust.security.error_handling.handle_exception()
pushes into the buffer automatically.
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from djust.observability.tracebacks import (
    _clear_tracebacks,
    get_buffer_size,
    get_recent_tracebacks,
    record_traceback,
)
from djust.observability.views import last_traceback


@pytest.fixture(autouse=True)
def clean_buffer():
    _clear_tracebacks()
    yield
    _clear_tracebacks()


def _make_exc():
    try:
        raise ValueError("boom — test exception")
    except ValueError as e:
        return e


# --- Buffer primitives -----------------------------------------------------


def test_record_and_retrieve():
    exc = _make_exc()
    record_traceback(exc, error_type="event", event_name="increment", view_class="CounterView")
    entries = get_recent_tracebacks(1)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["exception_type"] == "ValueError"
    assert "boom" in entry["message"]
    assert entry["error_type"] == "event"
    assert entry["event_name"] == "increment"
    assert entry["view_class"] == "CounterView"
    assert "boom" in entry["traceback"]


def test_buffer_preserves_newest_first():
    for i in range(3):
        try:
            raise RuntimeError(f"err-{i}")
        except RuntimeError as e:
            record_traceback(e)
    recent = get_recent_tracebacks(3)
    assert [e["message"] for e in recent] == ["err-2", "err-1", "err-0"]


def test_buffer_caps_at_50():
    for i in range(60):
        try:
            raise RuntimeError(f"err-{i}")
        except RuntimeError as e:
            record_traceback(e)
    assert get_buffer_size() == 50
    recent = get_recent_tracebacks(60)
    # Only the last 50 remain; entries 0..9 are gone.
    assert len(recent) == 50
    assert recent[0]["message"] == "err-59"
    assert recent[-1]["message"] == "err-10"


def test_empty_buffer_returns_empty_list():
    assert get_recent_tracebacks(5) == []


# --- Endpoint --------------------------------------------------------------


@override_settings(DEBUG=True)
def test_endpoint_returns_recent():
    exc = _make_exc()
    record_traceback(exc, event_name="click", view_class="MyView")
    rf = RequestFactory()
    resp = last_traceback(rf.get("/?n=1"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["count"] == 1
    assert len(data["entries"]) == 1
    assert data["entries"][0]["exception_type"] == "ValueError"


@override_settings(DEBUG=True)
def test_endpoint_defaults_n_to_1():
    for i in range(3):
        try:
            raise KeyError(f"k{i}")
        except KeyError as e:
            record_traceback(e)
    rf = RequestFactory()
    resp = last_traceback(rf.get("/"))
    data = json.loads(resp.content)
    assert len(data["entries"]) == 1
    assert "k2" in data["entries"][0]["message"]


@override_settings(DEBUG=True)
def test_endpoint_handles_bad_n():
    """Non-int n silently falls back to 1 rather than 500ing."""
    rf = RequestFactory()
    resp = last_traceback(rf.get("/?n=abc"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["count"] == 1


@override_settings(DEBUG=True)
def test_endpoint_caps_n_at_50():
    rf = RequestFactory()
    resp = last_traceback(rf.get("/?n=9999"))
    data = json.loads(resp.content)
    assert data["count"] == 50


@override_settings(DEBUG=False)
def test_endpoint_404_when_debug_off():
    rf = RequestFactory()
    resp = last_traceback(rf.get("/"))
    assert resp.status_code == 404


# --- Integration with handle_exception ------------------------------------


def test_handle_exception_populates_buffer():
    """Any djust-managed exception should land in the ring buffer."""
    from djust.security.error_handling import handle_exception

    exc = _make_exc()
    handle_exception(
        exc,
        error_type="event",
        event_name="increment",
        view_class="CounterView",
    )
    entries = get_recent_tracebacks(1)
    assert len(entries) == 1
    assert entries[0]["exception_type"] == "ValueError"
    assert entries[0]["event_name"] == "increment"
    assert entries[0]["view_class"] == "CounterView"
