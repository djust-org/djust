"""
Tests for Phase 7.3 — log-tail ring buffer + /log/ endpoint.
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from django.test import RequestFactory, override_settings

from djust.observability.log_handler import (
    ObservabilityLogHandler,
    _clear_logs,
    get_buffer_size,
    get_recent_logs,
    install_handler,
)
from djust.observability.views import log_tail


@pytest.fixture(autouse=True)
def clean_logs_fixture():
    _clear_logs()
    yield
    _clear_logs()


def _emit(level: str, name: str, message: str, args=()):
    """Synthesize a LogRecord and push through a fresh handler instance."""
    handler = ObservabilityLogHandler(level=logging.DEBUG)
    record = logging.LogRecord(
        name=name,
        level=logging._nameToLevel[level],
        pathname=f"/fake/{name}.py",
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )
    handler.emit(record)


# --- Handler + buffer ------------------------------------------------------


def test_emit_captures_record():
    _emit("INFO", "djust.test", "hello %s", ("world",))
    entries = get_recent_logs()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["level"] == "INFO"
    assert entry["logger_name"] == "djust.test"
    assert entry["message"] == "hello world"
    assert entry["pathname"] == "/fake/djust.test.py"


def test_buffer_caps_at_500():
    for i in range(520):
        _emit("INFO", "djust", f"msg-{i}")
    assert get_buffer_size() == 500
    entries = get_recent_logs(limit=500)
    assert len(entries) == 500
    assert entries[0]["message"] == "msg-20"
    assert entries[-1]["message"] == "msg-519"


def test_since_ms_filters():
    _emit("INFO", "djust", "before")
    time.sleep(0.01)
    cutoff = int(time.time() * 1000)
    time.sleep(0.01)
    _emit("INFO", "djust", "after")
    entries = get_recent_logs(since_ms=cutoff)
    assert len(entries) == 1
    assert entries[0]["message"] == "after"


def test_level_filter_minimum():
    _emit("DEBUG", "djust", "dbg")
    _emit("INFO", "djust", "info")
    _emit("WARNING", "djust", "warn")
    _emit("ERROR", "djust", "err")

    warn_plus = get_recent_logs(level="WARNING")
    assert [e["level"] for e in warn_plus] == ["WARNING", "ERROR"]

    all_ = get_recent_logs(level="DEBUG")
    assert len(all_) == 4


def test_unknown_level_defaults_to_info():
    _emit("DEBUG", "djust", "dbg")
    _emit("INFO", "djust", "info")
    entries = get_recent_logs(level="BANANA")
    assert [e["level"] for e in entries] == ["INFO"]


def test_install_handler_is_idempotent():
    """Re-calling install_handler() must not double-attach."""
    install_handler()
    initial_count = len(logging.getLogger("djust").handlers)
    install_handler()
    assert len(logging.getLogger("djust").handlers) == initial_count


# --- Endpoint --------------------------------------------------------------


@override_settings(DEBUG=True)
def test_endpoint_returns_entries():
    _emit("INFO", "djust", "test entry")
    rf = RequestFactory()
    resp = log_tail(rf.get("/"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["count"] == 1
    assert data["entries"][0]["message"] == "test entry"


@override_settings(DEBUG=True)
def test_endpoint_honors_level_param():
    _emit("INFO", "djust", "info")
    _emit("ERROR", "djust", "err")
    rf = RequestFactory()
    resp = log_tail(rf.get("/?level=ERROR"))
    data = json.loads(resp.content)
    assert data["count"] == 1
    assert data["entries"][0]["level"] == "ERROR"


@override_settings(DEBUG=True)
def test_endpoint_honors_since_ms():
    _emit("INFO", "djust", "before")
    time.sleep(0.01)
    cutoff = int(time.time() * 1000)
    time.sleep(0.01)
    _emit("INFO", "djust", "after")
    rf = RequestFactory()
    resp = log_tail(rf.get(f"/?since_ms={cutoff}"))
    data = json.loads(resp.content)
    assert data["count"] == 1
    assert data["entries"][0]["message"] == "after"


@override_settings(DEBUG=True)
def test_endpoint_handles_bad_params():
    """Non-int params shouldn't 500."""
    _emit("INFO", "djust", "test")
    rf = RequestFactory()
    resp = log_tail(rf.get("/?since_ms=abc&limit=xyz"))
    assert resp.status_code == 200


@override_settings(DEBUG=False)
def test_endpoint_404_when_debug_off():
    rf = RequestFactory()
    resp = log_tail(rf.get("/"))
    assert resp.status_code == 404
