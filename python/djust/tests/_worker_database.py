"""Resource cleanup for tests that open database connections on asgiref's worker.

Use at the source of the worker I/O. Downstream socket tests must retain their
normal database blocker, so they can detect a connection leaked by another test.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from asgiref.sync import SyncToAsync, sync_to_async
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper


def _snapshot() -> dict[int, bool]:
    """Which of this thread's connections are open, by identity."""
    return {
        id(conn): conn.connection is not None for conn in connections.all(initialized_only=True)
    }


def _close_new(before: dict[int, bool]) -> None:
    """Close the connections of this thread that were not open in ``before``."""
    for conn in connections.all(initialized_only=True):
        if conn.connection is None or before.get(id(conn), False):
            continue
        if conn.vendor == "sqlite" and conn.is_in_memory_db():
            # SQLite's subclass keeps in-memory databases alive; the
            # base close releases this test's worker-owned handle.
            BaseDatabaseWrapper.close(conn)
        else:
            conn.close()


@asynccontextmanager
async def owned_worker_database_connections() -> AsyncIterator[None]:
    """Close only connections opened by this scope on the synchronous worker."""
    before = await sync_to_async(_snapshot)()
    try:
        yield
    finally:
        await sync_to_async(_close_new)(before)


@contextmanager
def owned_worker_database_connections_sync() -> Iterator[None]:
    """The same ownership for a SYNCHRONOUS test, with no event loop (#4020).

    A sync test can run async code on a loop of its own (``loop.run_until_complete``,
    ``asyncio.run``): a WebSocket communicator against a consumer, say. With no
    ``async_to_sync`` above it, the consumer's ``sync_to_async`` database work runs on
    asgiref's one shared worker thread, opens a connection there and leaves it open.
    The next socket test that has no database access then fails in its disconnect
    ("Database access not allowed"), far from the test that leaked.

    The work is submitted straight to that worker thread's executor, which is where
    the connection lives. Nothing here starts a loop or grants database access.
    """
    executor = SyncToAsync.single_thread_executor
    before = executor.submit(_snapshot).result()
    try:
        yield
    finally:
        executor.submit(_close_new, before).result()
