"""Worker cleanup must neither grant DB access nor start loops for sync tests."""

from types import SimpleNamespace

from tests.worker_database import _SCOPE_FIXTURE, pytest_collection_modifyitems


def test_worker_scope_is_attached_only_to_existing_async_database_users():
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
    items = [marked, fixture_user, blocked, sync_db]
    pytest_collection_modifyitems(items)
    pytest_collection_modifyitems(items)
    assert marked.fixturenames == [_SCOPE_FIXTURE]
    assert fixture_user.fixturenames == ["db", _SCOPE_FIXTURE]
    assert blocked.fixturenames == []
    assert sync_db.fixturenames == []
