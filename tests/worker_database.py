"""Own shared-worker database handles at the async test that opens them."""

from collections.abc import AsyncIterator
from inspect import iscoroutinefunction
from typing import Any

import pytest

_SCOPE_FIXTURE = "_owned_test_worker_database_connections"
_DB_FIXTURES = frozenset({"db", "transactional_db", "_django_db_helper"})


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """Attach cleanup only to async tests that already request database access.

    Adding an autouse async fixture would create loops for synchronous tests.
    A DB fixture dependency would grant access to otherwise blocked tests.
    This hook does neither: it adds only ownership teardown to existing DB users.
    """
    for item in items:
        if not iscoroutinefunction(getattr(item, "obj", None)):
            continue
        if item.get_closest_marker("django_db") is None and not _DB_FIXTURES.intersection(
            item.fixturenames
        ):
            continue
        if _SCOPE_FIXTURE not in item.fixturenames:
            item.fixturenames.append(_SCOPE_FIXTURE)


@pytest.fixture
async def _owned_test_worker_database_connections() -> AsyncIterator[None]:
    # No db/transactional_db dependency: this fixture grants no permissions.
    from djust.tests._worker_database import owned_worker_database_connections

    async with owned_worker_database_connections():
        yield
