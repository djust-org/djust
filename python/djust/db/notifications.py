"""
``PostgresNotifyListener`` — process-wide async LISTEN consumer that
bridges PostgreSQL pg_notify events into Django Channels group messages.

Architecture:

* One listener per Django process. Lazy-started on the first call to
  ``ensure_listening(channel)`` (typically triggered by a view's
  ``self.listen()`` in ``mount``).
* The listener runs in a dedicated asyncio task on a **separate** psycopg
  ``AsyncConnection`` — not Django's connection pool. Long-lived LISTEN
  connections don't play nice with pgbouncer transaction pooling.
* Each NOTIFY is forwarded to ``channel_layer.group_send(
  f"djust_db_notify_{channel}", {"type": "db_notify", ...})``. The
  ``LiveViewConsumer.db_notify`` handler re-renders affected views.
* Connection drops are handled by reconnecting after a 1-second backoff
  and re-issuing LISTEN for every subscribed channel. Notifications
  missed during the drop window are lost — callers recover via the
  normal ``mount()`` re-fetch on WS reconnect. This limitation is
  documented in ``docs/website/guides/database-notifications.md``.
"""

import asyncio
import json
import logging
import os
import threading
from typing import Any, ClassVar, Dict, Optional, Set

from django.conf import settings

from .decorators import _validate_channel
from .exceptions import DatabaseNotificationNotSupported

logger = logging.getLogger(__name__)

# psycopg is imported lazily so the module stays importable on hosts
# without the driver. ``_import_psycopg`` raises ``DatabaseNotificationNotSupported``
# with a clear message if the dependency (or sql helpers) aren't available.
_psycopg = None
_psycopg_sql = None


def _import_psycopg():
    global _psycopg, _psycopg_sql
    if _psycopg is not None:
        return _psycopg, _psycopg_sql
    try:
        import psycopg  # type: ignore[import-not-found]
        from psycopg import sql as _sql  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised via mocks
        raise DatabaseNotificationNotSupported(
            "psycopg (>=3.2) is required for djust.db notifications. "
            "Install it via `pip install 'psycopg[binary]>=3.2'`."
        ) from exc
    _psycopg = psycopg
    _psycopg_sql = _sql
    return psycopg, _sql


def _build_dsn() -> str:
    """Derive a psycopg DSN from Django's default DB settings.

    We avoid inheriting Django's connection (which may be inside a
    connection pool) by creating a dedicated ``AsyncConnection``.
    """
    db = settings.DATABASES.get("default", {})
    engine = db.get("ENGINE", "")
    if "postgresql" not in engine:
        raise DatabaseNotificationNotSupported(
            f"djust.db notifications require a postgresql backend (got {engine!r})."
        )
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
            # Values are validated by psycopg on connect; we just pass through.
            parts.append(f"{dsn_key}={val}")
    return " ".join(parts)


class PostgresNotifyListener:
    """Process-wide singleton async listener for pg_notify.

    Use ``instance()`` to fetch. Call ``ensure_listening(channel)`` to
    subscribe — it's safe to call many times with the same channel.
    """

    _instance: ClassVar[Optional["PostgresNotifyListener"]] = None
    _class_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        self._channels: Set[str] = set()
        self._conn: Any = None
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopping: bool = False
        self._ready_event: Optional[asyncio.Event] = None

    @classmethod
    def instance(cls) -> "PostgresNotifyListener":
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """Discard the process-wide singleton. Test-only helper."""
        with cls._class_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None and inst._task is not None and not inst._task.done():
            inst._stopping = True
            try:
                inst._task.cancel()
            except Exception:  # noqa: BLE001
                logger.debug("reset_for_tests: task cancel raised", exc_info=True)

    async def ensure_listening(self, channel: str) -> None:
        """Idempotently subscribe the listener to ``channel``.

        On first call, starts the background listener task. Subsequent
        calls are cheap: they add to the channel set and issue a
        ``LISTEN`` on the active connection if one exists.
        """
        _validate_channel(channel)
        if channel in self._channels:
            return
        self._channels.add(channel)
        await self._ensure_task_started()
        # If the connection is up right now, issue LISTEN immediately so
        # this subscription picks up notifications without waiting for
        # the next reconnect cycle.
        if self._conn is not None:
            await self._listen_on(self._conn, channel)

    async def _ensure_task_started(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._ready_event = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._run())

    async def _listen_on(self, conn: Any, channel: str) -> None:
        _, sql_mod = _import_psycopg()
        await conn.execute(sql_mod.SQL("LISTEN {}").format(sql_mod.Identifier(channel)))

    async def _connect(self) -> Any:
        psycopg, _ = _import_psycopg()
        dsn = _build_dsn()
        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        return conn

    async def _run(self) -> None:
        while not self._stopping:
            try:
                conn = await self._connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("pg listener connect failed: %s — retrying in 1s", exc)
                await asyncio.sleep(1.0)
                continue

            self._conn = conn
            try:
                for ch in list(self._channels):
                    await self._listen_on(conn, ch)
                if self._ready_event is not None:
                    self._ready_event.set()
                async for notify in conn.notifies():
                    await self._dispatch(notify.channel, notify.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("pg listener lost connection: %s — reconnecting in 1s", exc)
                await asyncio.sleep(1.0)
            finally:
                self._conn = None
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    logger.debug("pg listener connection close failed", exc_info=True)

    async def _dispatch(self, channel: str, raw_payload: str) -> None:
        try:
            payload: Dict[str, Any] = json.loads(raw_payload)
        except (ValueError, TypeError):
            # Accept non-JSON payloads (e.g. raw SQL `NOTIFY x, 'hello'`).
            payload = {"raw": raw_payload}

        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            logger.debug(
                "pg listener dropped NOTIFY on %s — no Channels layer configured",
                channel,
            )
            return
        await layer.group_send(
            f"djust_db_notify_{channel}",
            {"type": "db_notify", "channel": channel, "payload": payload},
        )
