"""Worker cleanup must neither grant DB access nor start loops for sync tests."""

import asyncio

import pytest

from types import SimpleNamespace

from tests.worker_database import (
    _SCOPE_FIXTURE,
    _SYNC_SCOPE_FIXTURE,
    pytest_collection_modifyitems,
)


def test_worker_scope_is_attached_only_to_existing_database_users():
    async def asynchronous():
        pass

    def synchronous():
        pass

    def item(function, *, marked=False, fixtures=()):
        return SimpleNamespace(
            obj=function,
            fixturenames=list(fixtures),
            get_closest_marker=lambda name: object() if marked and name == "django_db" else None,
        )

    marked = item(asynchronous, marked=True)
    fixture_user = item(asynchronous, fixtures=("db",))
    blocked = item(asynchronous)
    sync_db = item(synchronous, marked=True)
    sync_fixture_user = item(synchronous, fixtures=("transactional_db",))
    sync_blocked = item(synchronous)
    items = [marked, fixture_user, blocked, sync_db, sync_fixture_user, sync_blocked]
    pytest_collection_modifyitems(items)
    pytest_collection_modifyitems(items)
    assert marked.fixturenames == [_SCOPE_FIXTURE]
    assert fixture_user.fixturenames == ["db", _SCOPE_FIXTURE]
    assert blocked.fixturenames == []
    # #4020: a synchronous database user gets the loop-free fixture, never the async one.
    assert sync_db.fixturenames == [_SYNC_SCOPE_FIXTURE]
    assert sync_fixture_user.fixturenames == ["transactional_db", _SYNC_SCOPE_FIXTURE]
    assert sync_blocked.fixturenames == []


def _open_on_the_worker_thread():
    """What a consumer's ``sync_to_async`` ORM call does when a sync test runs its own loop."""
    from asgiref.sync import sync_to_async
    from django.contrib.auth.models import User

    async def work():
        return await sync_to_async(User.objects.count)()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(work())
    finally:
        loop.close()


def _open_worker_connections():
    from asgiref.sync import SyncToAsync
    from django.db import connections

    def count():
        return sum(c.connection is not None for c in connections.all(initialized_only=True))

    return SyncToAsync.single_thread_executor.submit(count).result()


def _close_worker_connections():
    """Start from a known state: an earlier test may have left the worker thread's connection open.

    The scope under test closes only what was opened inside it, so a connection that is already
    open would be reused and the count would not move (the merge-queue failures on #4015).

    This goes through the helper's own closer on purpose. The test database is in-memory SQLite,
    where Django's ``close()`` keeps the handle, so ``connections.close_all()`` leaves it open.
    """
    from asgiref.sync import SyncToAsync
    from djust.tests._worker_database import _close_new

    SyncToAsync.single_thread_executor.submit(_close_new, {}).result()


@pytest.mark.django_db(transaction=True)
def test_the_sync_scope_closes_what_a_sync_test_opened_on_the_worker_thread():
    """The mechanism of #4020, in one test: the helper closes exactly what its scope opened."""
    from djust.tests._worker_database import owned_worker_database_connections_sync

    _close_worker_connections()
    before = _open_worker_connections()
    assert before == 0
    with owned_worker_database_connections_sync():
        _open_on_the_worker_thread()
        assert _open_worker_connections() == before + 1, "the premise: the call leaves one open"
    assert _open_worker_connections() == before


@pytest.mark.django_db(transaction=True)
def test_a_connection_that_was_already_open_is_left_alone():
    from djust.tests._worker_database import owned_worker_database_connections_sync

    _close_worker_connections()
    with owned_worker_database_connections_sync():
        _open_on_the_worker_thread()  # opened by the OUTER scope
        opened = _open_worker_connections()
        assert opened == 1
        with owned_worker_database_connections_sync():
            _open_on_the_worker_thread()  # reuses the open connection
        assert _open_worker_connections() == opened, "the inner scope must not close it"
