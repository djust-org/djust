"""Own shared-worker database handles at the async test that opens them."""

from collections.abc import AsyncIterator, Iterator
from inspect import iscoroutinefunction
from typing import Any

import pytest

_SCOPE_FIXTURE = "_owned_test_worker_database_connections"
_SYNC_SCOPE_FIXTURE = "_owned_sync_test_worker_database_connections"
_DB_FIXTURES = frozenset({"db", "transactional_db", "_django_db_helper"})


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """Attach cleanup only to tests that already request database access.

    Adding an autouse async fixture would create loops for synchronous tests.
    A DB fixture dependency would grant access to otherwise blocked tests.
    This hook does neither: it adds only ownership teardown to existing DB users.

    An async test gets the async fixture. A synchronous one gets a synchronous
    fixture that starts no loop (#4020): a sync test can drive async code on a
    loop of its own, and what that opens on asgiref's worker thread was closed by
    nobody.
    """
    for item in items:
        if item.get_closest_marker("django_db") is None and not _DB_FIXTURES.intersection(
            item.fixturenames
        ):
            continue
        fixture = (
            _SCOPE_FIXTURE
            if iscoroutinefunction(getattr(item, "obj", None))
            else _SYNC_SCOPE_FIXTURE
        )
        if fixture not in item.fixturenames:
            item.fixturenames.append(fixture)


@pytest.fixture
async def _owned_test_worker_database_connections() -> AsyncIterator[None]:
    # No db/transactional_db dependency: this fixture grants no permissions.
    from djust.tests._worker_database import owned_worker_database_connections

    async with owned_worker_database_connections():
        yield


@pytest.fixture
def _owned_sync_test_worker_database_connections() -> Iterator[None]:
    # No db/transactional_db dependency and no event loop: see the helper.
    from djust.tests._worker_database import owned_worker_database_connections_sync

    with owned_worker_database_connections_sync():
        yield
