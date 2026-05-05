"""Regression test for the 2026-05-05 incident.

A deployed djust app missing the optional ``psycopg[binary]>=3.2``
dependency had ``PostgresNotifyListener._run()`` retrying every 1
second for 3.5 days (~302,000 attempts), each leaving asyncio Task
state behind. The Daphne worker accumulated 15 GiB of anonymous
heap before kubelet tripped from memory pressure.

The fix: detect ``DatabaseNotificationNotSupported`` (raised by
``_import_psycopg`` when the driver is missing, and by ``_build_dsn``
on a non-postgres backend) as a permanent failure, log once, set
``_stopping``, and exit the loop. Transient failures (anything else)
keep their existing 1-second-backoff retry behaviour.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from djust.db.exceptions import DatabaseNotificationNotSupported
from djust.db.notifications import PostgresNotifyListener


@pytest.fixture(autouse=True)
def _isolate_listener_singleton():
    """Each test gets a fresh listener instance.

    The class-level singleton would otherwise leak state across tests
    in the same process.
    """
    PostgresNotifyListener._instance = None
    yield
    PostgresNotifyListener._instance = None


@pytest.mark.asyncio
async def test_run_exits_immediately_on_permanent_failure(monkeypatch, caplog):
    """psycopg-not-installed (or non-postgres backend) → loop exits, no retry.

    Pre-fix this would retry every 1s forever, leaking Task state.
    The fix: log once at WARNING, set ``_stopping``, and return.
    """
    listener = PostgresNotifyListener()

    call_count = 0

    async def fake_connect():
        nonlocal call_count
        call_count += 1
        raise DatabaseNotificationNotSupported(
            "psycopg (>=3.2) is required for djust.db notifications."
        )

    monkeypatch.setattr(listener, "_connect", fake_connect)
    listener._ready_event = asyncio.Event()

    with caplog.at_level(logging.WARNING, logger="djust.db.notifications"):
        await asyncio.wait_for(listener._run(), timeout=2.0)

    assert call_count == 1, (
        f"expected one connect attempt for a permanent failure, got {call_count}"
    )
    assert listener._stopping is True, (
        "permanent failure must flip _stopping so future ensure_task_started "
        "calls don't restart the loop"
    )
    assert listener._ready_event.is_set(), (
        "_ready_event must be set even on permanent failure so awaiters don't hang"
    )

    # Exactly one WARNING about being disabled — and not the looping
    # "retrying in 1s" message that pre-fix code would have emitted.
    permanent_logs = [r for r in caplog.records if "permanent failure" in r.getMessage()]
    assert len(permanent_logs) == 1
    retry_logs = [r for r in caplog.records if "retrying in 1s" in r.getMessage()]
    assert retry_logs == [], "permanent failure path must not emit the retry log line"


@pytest.mark.asyncio
async def test_run_retries_transient_connect_failures(monkeypatch, caplog):
    """Transient OperationalError-style failures still retry (existing behaviour).

    Guards against an over-aggressive interpretation of the fix that
    would also drop the loop on transient DB outages.
    """
    listener = PostgresNotifyListener()

    attempts = 0

    async def fake_connect():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionRefusedError("postgres temporarily unreachable")
        # On the third attempt, raise the permanent error to break the loop
        # so the test can assert on retry behaviour without hanging forever.
        raise DatabaseNotificationNotSupported("switching to permanent to terminate the test")

    # Sleep 0 so the test runs fast.
    async def fake_sleep(_):
        return

    monkeypatch.setattr(listener, "_connect", fake_connect)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.WARNING, logger="djust.db.notifications"):
        await asyncio.wait_for(listener._run(), timeout=2.0)

    # Two transient retries + one permanent = 3 attempts.
    assert attempts == 3
    retry_logs = [r for r in caplog.records if "retrying in 1s" in r.getMessage()]
    assert len(retry_logs) == 2, (
        f"expected 2 retry log lines for the 2 transient failures, "
        f"got {len(retry_logs)}: {[r.getMessage() for r in retry_logs]}"
    )
