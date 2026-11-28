"""``DatabaseViewStateStore``: view state in djust's own table, out of the session.

The end-to-end behaviour (state survives, the session holds no content, a
socket restores) is covered with every store in ``test_state_store_4132.py``;
these pin the store's own contract: load, upsert, delete, the expiry sweep,
the stats, the database alias, and the fallback where the backend has no
targeted upsert.
"""

from __future__ import annotations

import time

import pytest

from djust.models import ViewState
from djust.state_store import DatabaseViewStateStore, StateKey, build_store

pytestmark = pytest.mark.django_db


def _key(tab: str = "t1") -> StateKey:
    return StateKey(
        session="s" * 32,
        binding="b" * 64,
        page="app.views.Page|/",
        tab=tab,
        user_id="anonymous",
        tenant_id="none",
    )


def test_it_is_a_store_the_setting_can_name():
    assert isinstance(build_store("djust.state_store.DatabaseViewStateStore"), DatabaseViewStateStore)


def test_save_load_replace_delete():
    store = DatabaseViewStateStore()
    key, other = _key("t1"), _key("t2")
    assert store.load(key) is None
    store.save(key, b"first", expires=int(time.time()) + 60)
    assert store.load(key) == b"first"
    store.save(key, b"second\x00bytes", expires=int(time.time()) + 90)
    assert store.load(key) == b"second\x00bytes"
    assert ViewState.objects.count() == 1
    assert ViewState.objects.get(pk=key.id).payload == b"second\x00bytes"
    store.save(other, b"x", expires=int(time.time()) + 60)
    store.delete([key, _key("unknown")])
    assert store.load(key) is None
    assert store.load(other) == b"x"
    store.delete([])


def test_the_sweep_and_the_stats():
    store = DatabaseViewStateStore()
    now = int(time.time())
    store.save(_key("old"), b"o", expires=now - 5)
    store.save(_key("new"), b"n", expires=now + 600)
    assert store.stats() == {"rows": 2, "expired": 1}
    assert store.clear(expired_only=True) == 1
    assert store.load(_key("new")) == b"n"
    assert store.clear(expired_only=False) == 1
    assert store.stats() == {"rows": 0, "expired": 0}


def test_without_a_targeted_upsert_it_updates_or_creates(monkeypatch):
    from django.db import connection

    monkeypatch.setattr(connection.features, "supports_update_conflicts_with_target", False)
    store = DatabaseViewStateStore()
    key = _key()
    store.save(key, b"a", expires=int(time.time()) + 60)
    store.save(key, b"b", expires=int(time.time()) + 60)
    assert store.load(key) == b"b"
    assert ViewState.objects.count() == 1


def test_the_database_alias_setting_is_used(settings):
    settings.DJUST_STATE_STORE_DATABASE = "default"
    store = DatabaseViewStateStore()
    assert store._rows().db == "default"
    store.save(_key(), b"z", expires=int(time.time()) + 60)
    assert store.load(_key()) == b"z"
