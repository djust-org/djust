"""
Tests for Phase 7.5 — SQL query capture + /sql_queries/ endpoint.

Uses Django's test database via django_db fixture so the execute_wrapper
runs against a real connection.
"""

from __future__ import annotations

import json
import time

import pytest
from django.db import connection
from django.test import RequestFactory, override_settings

from djust.observability.sql import (
    _clear_queries,
    capture_for_event,
    get_buffer_size,
    get_queries_since,
)
from djust.observability.views import sql_queries as sql_queries_view


@pytest.fixture(autouse=True)
def clean_buffer():
    _clear_queries()
    yield
    _clear_queries()


# --- Capture primitives ----------------------------------------------------


@pytest.mark.django_db
def test_capture_records_queries_with_scope():
    with capture_for_event(session_id="s-1", handler_name="increment"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    entries = get_queries_since()
    assert len(entries) >= 1
    entry = next(e for e in entries if "SELECT 1" in e["sql"])
    assert entry["session_id"] == "s-1"
    assert entry["handler_name"] == "increment"
    assert entry["duration_ms"] >= 0


@pytest.mark.django_db
def test_queries_outside_scope_are_tagged_null():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 2")
    # Without capture_for_event, no tags but still NO capture because the
    # wrapper wasn't installed. Buffer should remain empty.
    assert get_buffer_size() == 0


@pytest.mark.django_db
def test_capture_scope_restores_on_exit():
    """After the with-block exits, subsequent queries are NOT captured."""
    with capture_for_event(session_id="s", handler_name="h"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    size_during = get_buffer_size()

    # Outside the context: wrapper is uninstalled, no new entries.
    with connection.cursor() as cursor:
        cursor.execute("SELECT 3")
    assert get_buffer_size() == size_during


@pytest.mark.django_db
def test_since_ms_filter():
    with capture_for_event(session_id="s", handler_name="h"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 10")
    time.sleep(0.01)
    cutoff = int(time.time() * 1000)
    time.sleep(0.01)
    with capture_for_event(session_id="s", handler_name="h"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 20")
    recent = get_queries_since(since_ms=cutoff)
    # Only the 'SELECT 20' query should remain.
    assert any("20" in e["sql"] for e in recent)
    assert not any("SELECT 10" in e["sql"] for e in recent)


@pytest.mark.django_db
def test_handler_name_filter():
    with capture_for_event(session_id="s", handler_name="increment"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 'inc'")
    with capture_for_event(session_id="s", handler_name="decrement"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 'dec'")
    inc_only = get_queries_since(handler_name="increment")
    assert all(e["handler_name"] == "increment" for e in inc_only)
    assert any("inc" in e["sql"] for e in inc_only)


@pytest.mark.django_db
def test_no_wrapper_when_context_inactive():
    """capture_for_event installs the wrapper only within its scope."""
    # No active scope at this point.
    with connection.cursor() as cursor:
        cursor.execute("SELECT 99")
    # Nothing captured — execute_wrapper not installed.
    assert get_buffer_size() == 0


# --- Endpoint --------------------------------------------------------------


@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_endpoint_returns_entries():
    with capture_for_event(session_id="s", handler_name="h"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    rf = RequestFactory()
    resp = sql_queries_view(rf.get("/"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["count"] >= 1


@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_endpoint_session_filter():
    with capture_for_event(session_id="sA", handler_name="h"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    with capture_for_event(session_id="sB", handler_name="h"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 2")
    rf = RequestFactory()
    resp = sql_queries_view(rf.get("/?session_id=sA"))
    data = json.loads(resp.content)
    assert all(e["session_id"] == "sA" for e in data["entries"])


@override_settings(DEBUG=False)
def test_endpoint_404_when_debug_off():
    rf = RequestFactory()
    resp = sql_queries_view(rf.get("/"))
    assert resp.status_code == 404
