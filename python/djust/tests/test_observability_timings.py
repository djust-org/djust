"""
Tests for Phase 7.4 — handler timing ring buffer + /handler_timings/ endpoint.
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings

from djust.observability.timings import (
    _clear_timings,
    _percentile,
    get_sample_total,
    get_timing_stats,
    record_handler_timing,
)
from djust.observability.views import handler_timings as handler_timings_view


@pytest.fixture(autouse=True)
def clean_timings():
    _clear_timings()
    yield
    _clear_timings()


# --- Buffer / aggregation --------------------------------------------------


def test_record_and_single_handler_stats():
    for d in [10, 12, 14, 9, 11]:
        record_handler_timing("CounterView", "increment", d)
    rows = get_timing_stats()
    assert len(rows) == 1
    row = rows[0]
    assert row["view_class"] == "CounterView"
    assert row["handler_name"] == "increment"
    assert row["count"] == 5
    assert row["min_ms"] == 9
    assert row["max_ms"] == 14
    assert row["avg_ms"] == pytest.approx(11.2, abs=0.01)


def test_multiple_handlers_separate_rows():
    record_handler_timing("A", "one", 1)
    record_handler_timing("A", "two", 10)
    record_handler_timing("B", "one", 100)
    rows = get_timing_stats()
    # Three distinct (view_class, handler) pairs.
    assert len(rows) == 3


def test_handler_name_filter():
    record_handler_timing("A", "one", 1)
    record_handler_timing("A", "two", 10)
    record_handler_timing("B", "one", 100)
    rows = get_timing_stats(handler_name="one")
    # Both views with a handler named "one" — not the "two" row.
    assert {r["view_class"] for r in rows} == {"A", "B"}
    assert all(r["handler_name"] == "one" for r in rows)


def test_p90_sorting_slowest_first():
    for d in [1, 1, 1, 1, 1]:
        record_handler_timing("Fast", "h", d)
    for d in [50, 60, 70, 80, 90]:
        record_handler_timing("Slow", "h", d)
    rows = get_timing_stats()
    assert rows[0]["view_class"] == "Slow"
    assert rows[1]["view_class"] == "Fast"


def test_since_ms_filter():
    import time

    record_handler_timing("A", "h", 10)
    time.sleep(0.01)
    cutoff = int(time.time() * 1000)
    time.sleep(0.01)
    record_handler_timing("A", "h", 20)
    rows = get_timing_stats(since_ms=cutoff)
    assert len(rows) == 1
    assert rows[0]["count"] == 1
    assert rows[0]["avg_ms"] == 20


def test_sample_cap_per_handler():
    """Buffer evicts oldest when more than 100 samples arrive."""
    for i in range(120):
        record_handler_timing("A", "h", float(i))
    assert get_sample_total() == 100
    rows = get_timing_stats()
    # Only the last 100 samples remain (20..119).
    assert rows[0]["count"] == 100
    assert rows[0]["min_ms"] == 20
    assert rows[0]["max_ms"] == 119


def test_percentile_interpolation():
    assert _percentile([], 50) == 0.0
    assert _percentile([7], 50) == 7
    # [1,2,3,4,5] — p50 is the median (3). p90 is linear interp between
    # indices 3.6 and 4 → 4 + 0.6*(5-4) = 4.6.
    assert _percentile([1, 2, 3, 4, 5], 50) == 3
    assert _percentile([1, 2, 3, 4, 5], 90) == pytest.approx(4.6, abs=0.01)


def test_empty_buffer_returns_empty_stats():
    assert get_timing_stats() == []


# --- Endpoint --------------------------------------------------------------


@override_settings(DEBUG=True)
def test_endpoint_returns_stats():
    for d in [1, 2, 3]:
        record_handler_timing("CounterView", "increment", d)
    rf = RequestFactory()
    resp = handler_timings_view(rf.get("/"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["count"] == 1
    assert data["stats"][0]["handler_name"] == "increment"


@override_settings(DEBUG=True)
def test_endpoint_handler_name_filter():
    record_handler_timing("A", "one", 5)
    record_handler_timing("A", "two", 15)
    rf = RequestFactory()
    resp = handler_timings_view(rf.get("/?handler_name=one"))
    data = json.loads(resp.content)
    assert data["count"] == 1
    assert data["stats"][0]["handler_name"] == "one"


@override_settings(DEBUG=True)
def test_endpoint_handles_bad_since_ms():
    record_handler_timing("A", "h", 1)
    rf = RequestFactory()
    resp = handler_timings_view(rf.get("/?since_ms=not-a-number"))
    assert resp.status_code == 200


@override_settings(DEBUG=False)
def test_endpoint_404_when_debug_off():
    rf = RequestFactory()
    resp = handler_timings_view(rf.get("/"))
    assert resp.status_code == 404
