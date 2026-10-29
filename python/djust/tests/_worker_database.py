"""Resource cleanup for tests that open database connections on asgiref's worker.

Use at the source of the worker I/O. Downstream socket tests must retain their
normal database blocker, so they can detect a connection leaked by another test.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asgiref.sync import sync_to_async
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper


@asynccontextmanager
async def owned_worker_database_connections() -> AsyncIterator[None]:
    """Close only connections opened by this scope on the synchronous worker."""

    def snapshot() -> dict[int, bool]:
        return {
            id(conn): conn.connection is not None for conn in connections.all(initialized_only=True)
        }

    def close_new(before: dict[int, bool]) -> None:
        for conn in connections.all(initialized_only=True):
            if conn.connection is None or before.get(id(conn), False):
                continue
            if conn.vendor == "sqlite" and conn.is_in_memory_db():
                # SQLite's subclass keeps in-memory databases alive; the
                # base close releases this test's worker-owned handle.
                BaseDatabaseWrapper.close(conn)
            else:
                conn.close()

    before = await sync_to_async(snapshot)()
    try:
        yield
    finally:
        await sync_to_async(close_new)(before)
