"""Close the database connections a test opens on asgiref's worker thread (#4214).

``sync_to_async`` runs a callable on asgiref's process-wide executor thread unless a
``ThreadSensitiveContext`` is active. A test that calls it opens the ``default`` connection
on that thread. The test database is in-memory SQLite, whose ``close()`` is a no-op, so
pytest-django's teardown never closes it, and the connection outlives the test. Every later
test on the same worker whose consumer dispatches (``channels.consumer.dispatch`` ->
``aclose_old_connections``) then reaches ``ensure_connection`` and, with no database mark,
hits pytest-django's "Database access not allowed". Which tests that is depends on the run
order, and on Django >= 6.0 (where the victim is not skipped) it shows up.

Wrap an autouse fixture's ``yield`` in :func:`close_new_worker_connections` in a file whose
tests call ``sync_to_async`` outside a ``ThreadSensitiveContext``::

    @pytest.fixture(autouse=True)
    async def close_worker_connections(transactional_db):
        async with close_new_worker_connections():
            yield

Use it only beside a database fixture (``transactional_db`` here): dropping the worker
thread's sqlite3 handle to the shared-cache in-memory database is safe only because the
main thread's connection, opened by that fixture, keeps the database alive. Without one the
last handle closing would drop the test database.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from asgiref.sync import sync_to_async
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper


def _snapshot() -> dict[int, bool]:
    return {
        id(conn): conn.connection is not None for conn in connections.all(initialized_only=True)
    }


def _close_new(before: dict[int, bool]) -> None:
    for conn in connections.all(initialized_only=True):
        if conn.connection is None or before.get(id(conn), False):
            continue
        if conn.vendor == "sqlite" and conn.is_in_memory_db():
            # ``close()`` is a no-op for in-memory SQLite (it would drop the test database),
            # so drop only the Python-level connection state the worker thread holds.
            BaseDatabaseWrapper.close(conn)
        else:
            conn.close()


@asynccontextmanager
async def close_new_worker_connections():
    """Close, on exit, the connections opened on asgiref's executor thread inside the block."""
    before = await sync_to_async(_snapshot)()
    yield
    await sync_to_async(_close_new)(before)
