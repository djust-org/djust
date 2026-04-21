"""Integration tests for PostgreSQL LISTEN/NOTIFY round-trip.

These tests require:
* A PostgreSQL backend configured as ``DATABASES['default']``
* The ``psycopg`` >= 3.2 driver installed

When either is missing the whole module is skipped, keeping CI green on
sqlite-only runners.
"""

import asyncio
import json
import os

import pytest
from django.conf import settings

_has_pg = settings.DATABASES.get("default", {}).get("ENGINE", "").endswith("postgresql")

try:
    import psycopg  # type: ignore[import-not-found]  # noqa: F401

    _has_psycopg = True
except ImportError:
    _has_psycopg = False

pytestmark = pytest.mark.skipif(
    not (_has_pg and _has_psycopg),
    reason="PostgreSQL backend + psycopg>=3.2 required for live LISTEN/NOTIFY tests",
)


@pytest.mark.asyncio
async def test_send_notify_round_trip():
    """``send_pg_notify`` reaches a real LISTEN connection on the same DB."""
    from djust.db import send_pg_notify

    channel = "djust_test_channel"

    async with await psycopg.AsyncConnection.connect(_dsn_from_settings(), autocommit=True) as conn:
        await conn.execute(f"LISTEN {channel}")

        # Emit via Django connection (what send_pg_notify does internally).
        from asgiref.sync import sync_to_async

        await sync_to_async(send_pg_notify)(channel, {"pk": 1, "event": "save"})

        notify = await asyncio.wait_for(conn.notifies().__anext__(), timeout=2.0)
        assert notify.channel == channel
        assert json.loads(notify.payload) == {"pk": 1, "event": "save"}


@pytest.mark.asyncio
async def test_multiple_subscribers_both_receive():
    """Two LISTEN connections on the same channel both see a NOTIFY."""
    from djust.db import send_pg_notify
    from asgiref.sync import sync_to_async

    channel = "djust_test_multi"
    dsn = _dsn_from_settings()

    async with (
        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as a,
        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as b,
    ):
        await a.execute(f"LISTEN {channel}")
        await b.execute(f"LISTEN {channel}")
        await sync_to_async(send_pg_notify)(channel, {"pk": 42})

        na = await asyncio.wait_for(a.notifies().__anext__(), timeout=2.0)
        nb = await asyncio.wait_for(b.notifies().__anext__(), timeout=2.0)
        assert json.loads(na.payload)["pk"] == 42
        assert json.loads(nb.payload)["pk"] == 42


@pytest.mark.asyncio
async def test_channel_isolation():
    """Listener on ``users`` does not see a NOTIFY on ``orders``."""
    from djust.db import send_pg_notify
    from asgiref.sync import sync_to_async

    dsn = _dsn_from_settings()
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute("LISTEN djust_test_users")
        await sync_to_async(send_pg_notify)("djust_test_orders", {"pk": 1})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(conn.notifies().__anext__(), timeout=0.5)


@pytest.mark.asyncio
async def test_payload_json_round_trip():
    """Nested JSON structures survive NOTIFY."""
    from djust.db import send_pg_notify
    from asgiref.sync import sync_to_async

    channel = "djust_test_payload"
    dsn = _dsn_from_settings()

    payload = {"pk": 9, "event": "save", "model": "shop.order", "nested": [1, 2, 3]}

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f"LISTEN {channel}")
        await sync_to_async(send_pg_notify)(channel, payload)
        notify = await asyncio.wait_for(conn.notifies().__anext__(), timeout=2.0)
        assert json.loads(notify.payload) == payload


@pytest.mark.asyncio
async def test_listener_reconnects_after_drop(monkeypatch):
    """Simulated ``OperationalError`` in the listener triggers reconnect."""
    from djust.db.notifications import PostgresNotifyListener

    PostgresNotifyListener.reset_for_tests()
    listener = PostgresNotifyListener.instance()

    # Inject a stub connection whose notifies() raises once, then behaves
    # normally. We just verify that _run() recovers without propagating.
    class _StubConn:
        def __init__(self, fail_once):
            self._fail_once = fail_once

        async def execute(self, *_a, **_kw):
            return None

        async def close(self):
            return None

        def notifies(self):
            class _Iter:
                def __init__(self, outer):
                    self.outer = outer

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if self.outer._fail_once:
                        self.outer._fail_once = False
                        raise psycopg.OperationalError("drop")
                    # Cancel the task to exit the loop deterministically.
                    raise asyncio.CancelledError()

            return _Iter(self)

    attempts = {"count": 0}

    async def fake_connect(self):
        attempts["count"] += 1
        return _StubConn(fail_once=attempts["count"] == 1)

    monkeypatch.setattr(PostgresNotifyListener, "_connect", fake_connect)

    # Kick the listener.
    await listener.ensure_listening("djust_test_recover")
    # Give _run() a chance to observe the fake drop + reconnect.
    await asyncio.sleep(0.05)
    # At least two connect attempts: original + reconnect.
    assert attempts["count"] >= 2
    PostgresNotifyListener.reset_for_tests()


def _dsn_from_settings() -> str:
    db = settings.DATABASES["default"]
    parts = []
    for key, dsn_key in (
        ("HOST", "host"),
        ("PORT", "port"),
        ("NAME", "dbname"),
        ("USER", "user"),
        ("PASSWORD", "password"),
    ):
        val = db.get(key) or os.environ.get(f"PG{dsn_key.upper()}", "")
        if val:
            parts.append(f"{dsn_key}={val}")
    return " ".join(parts)
