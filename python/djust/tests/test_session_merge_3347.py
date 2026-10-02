"""#3347: the post-event session save merges its changes into the stored session.

``SessionBase.save()`` writes the in-memory dict in full, and djust loads a
session once at mount and reuses it for every event. A save therefore rolled
back what another tab, request, middleware or handler stored since, and wrote
back keys deleted since.

The unit cases drive :func:`djust._session_merge.save_merged` against the real
``db``, ``cache``, ``cached_db`` and ``file`` backends. The end-to-end cases
are the issue's table: a real ``LiveViewConsumer`` through
``WebsocketCommunicator`` and the SSE stream/event views, with a second
``SessionStore`` standing in for the other request.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import pickle
import uuid
from importlib import import_module

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.contrib.auth import SESSION_KEY, get_user
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView
from djust._late_save import LateSaveDropped, detached
from djust._session_merge import TrackedSessionData, is_tracked, save_merged, track_session
from djust.decorators import event_handler, state
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = pytest.mark.django_db(transaction=True)

DB = "django.contrib.sessions.backends.db"
CACHE = "django.contrib.sessions.backends.cache"
CACHED_DB = "django.contrib.sessions.backends.cached_db"
FILE = "django.contrib.sessions.backends.file"
SIGNED = "django.contrib.sessions.backends.signed_cookies"
ALL_BACKENDS = [DB, CACHE, CACHED_DB, FILE]
SERVER_BACKENDS = [DB, CACHE, CACHED_DB]
_MOD = __name__
_ALLOWLIST = override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD])


@pytest.fixture
def staged(monkeypatch):
    """Explicit views here are not registered through the contract checks."""
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


@pytest.fixture(autouse=True)
def _file_sessions(tmp_path, settings):
    settings.SESSION_FILE_PATH = str(tmp_path)


def _store(engine):
    return import_module(engine).SessionStore


def _persisted(engine, data=None):
    session = _store(engine)()
    session.update(data or {"idle_ts": "OLD", "victim": "alive"})
    session.create()
    return session


def _stored(engine, key):
    return _store(engine)(key).load()


def _elsewhere(engine, key, **changes):
    """Another request's store writes ``changes`` (``None`` deletes the key)."""
    other = _store(engine)(key)
    for name, value in changes.items():
        if value is None:
            other.pop(name, None)
        else:
            other[name] = value
    other.save()


def _tracked(engine, data=None):
    save = _store(engine)(_persisted(engine, data).session_key)
    assert track_session(save)
    return save


def _as_pool_save(fn):
    return detached(fn)()


# --------------------------------------------------------------------------- #
# The tracker
# --------------------------------------------------------------------------- #


def test_the_tracker_records_every_kind_of_write():
    data = TrackedSessionData({"a": 1, "b": 2, "c": 3, "d": 4})
    data["a"] = 10
    del data["b"]
    assert data.pop("c") == 3
    assert data.pop("absent", None) is None
    data.update({"e": 5}, f=6)
    data.setdefault("g", 7)
    data.setdefault("d", 99)  # present: not a write
    data |= {"h": 8}
    assert data.written == {"a", "e", "f", "g", "h"}
    assert data.removed == {"b", "c"}
    data["b"] = 20  # written again after its removal
    assert "b" in data.written and "b" not in data.removed
    data.clear()
    assert data.removed == {"a", "b", "c", "d", "e", "f", "g", "h"} and not data.written


def test_a_tracker_that_reaches_a_cache_is_read_back_as_a_plain_dict():
    """``cache`` and ``cached_db`` hand ``session._session`` to the cache as it
    is; a tracker coming out of it would make another session believe it was
    tracked."""
    data = TrackedSessionData({"a": [1]})
    data["b"] = 2
    for clone in (pickle.loads(pickle.dumps(data)), copy.copy(data), copy.deepcopy(data)):
        assert type(clone) is dict and clone == {"a": [1], "b": 2}


def test_a_signed_cookie_session_is_not_tracked():
    session = _store(SIGNED)()
    session["a"] = 1
    assert track_session(session) is False
    assert not is_tracked(session)


def test_tracking_is_idempotent_and_keeps_the_recorded_writes():
    session = _tracked(DB)
    session["x"] = 1
    assert track_session(session) is True
    assert session._session_cache.written == {"x"}


# --------------------------------------------------------------------------- #
# save_merged against the real backends
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_value_another_request_stored_since_the_load_survives(engine):
    save = _tracked(engine)
    key = save.session_key
    _elsewhere(engine, key, idle_ts="NEW")

    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)

    stored = _stored(engine, key)
    assert stored["idle_ts"] == "NEW", "the save rolled the other request's write back"
    assert stored["liveview_/x/"] == {"count": 1}
    assert save["idle_ts"] == "NEW", "the in-memory session was not refreshed"


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_key_deleted_since_the_load_stays_deleted(engine):
    save = _tracked(engine)
    key = save.session_key
    _elsewhere(engine, key, victim=None)

    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)

    stored = _stored(engine, key)
    assert "victim" not in stored, "the save resurrected a deleted key"
    assert "victim" not in save


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_key_another_request_added_is_kept(engine):
    save = _tracked(engine)
    key = save.session_key
    _elsewhere(engine, key, brand_new="here")
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    assert _stored(engine, key)["brand_new"] == "here"


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_what_this_object_wrote_or_removed_is_stored(engine):
    """A handler's or ``check_permissions``' own ``request.session`` writes are
    part of the merge, and so are removals."""
    save = _tracked(engine, {"idle_ts": "OLD", "victim": "alive", "drop": 1})
    key = save.session_key
    save["from_handler"] = "yes"
    save.pop("drop")
    del save["victim"]
    save.setdefault("defaulted", 5)
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)

    stored = _stored(engine, key)
    assert stored["from_handler"] == "yes" and stored["defaulted"] == 5
    assert "drop" not in stored and "victim" not in stored
    assert stored["idle_ts"] == "OLD"


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_key_both_sides_wrote_takes_this_objects_value(engine):
    save = _tracked(engine)
    key = save.session_key
    _elsewhere(engine, key, **{"liveview_/x/": {"count": 99}})
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    assert _stored(engine, key)["liveview_/x/"] == {"count": 1}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_an_in_place_change_to_a_value_that_was_read_is_stored(engine):
    """Django's ``modified = True`` pattern: ``session["cart"].append(...)``."""
    save = _tracked(engine, {"cart": ["a"], "prefs": {"theme": "dark"}, "idle_ts": "OLD"})
    key = save.session_key
    save["cart"].append("b")  # read, then changed in place
    _ = save.get("prefs")  # read, left alone
    _elsewhere(engine, key, prefs={"theme": "light"}, idle_ts="NEW")

    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)

    stored = _stored(engine, key)
    assert stored["cart"] == ["a", "b"]
    assert stored["prefs"] == {"theme": "light"}, "a value that was only read was written back"
    assert stored["idle_ts"] == "NEW"


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_successful_save_leaves_nothing_pending(engine):
    save = _tracked(engine)
    key = save.session_key
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    assert not save._session_cache.written and not save._session_cache.removed
    _elsewhere(engine, key, **{"liveview_/x/": {"count": 5}})
    save_merged(save, key)  # nothing of this object's is pending now
    assert _stored(engine, key)["liveview_/x/"] == {"count": 5}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_failed_save_keeps_its_changes_for_the_retry(engine, monkeypatch):
    save = _tracked(engine)
    key = save.session_key
    save["liveview_/x/"] = {"count": 1}
    original = _store(engine).save
    calls = []

    def flaky(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("storage blip")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_store(engine), "save", flaky)
    with pytest.raises(RuntimeError):
        save_merged(save, key)
    _elsewhere(engine, key, idle_ts="NEW")
    save_merged(save, key)

    stored = _stored(engine, key)
    assert stored["liveview_/x/"] == {"count": 1}
    assert stored["idle_ts"] == "NEW"


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_login_rotating_the_tracked_session_still_saves(engine):
    """``login()`` in a handler: ``cycle_key()`` keeps the tracked dict, the key
    and the user are written to the new key."""
    save = _tracked(engine)
    old_key = save.session_key
    save.cycle_key()
    save[SESSION_KEY] = "7"
    new_key = save.session_key
    assert is_tracked(save) and new_key != old_key

    save["liveview_/x/"] = {"count": 1}
    save_merged(save, new_key)

    stored = _stored(engine, new_key)
    assert stored[SESSION_KEY] == "7" and stored["liveview_/x/"] == {"count": 1}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_flush_in_the_handler_falls_back_to_the_whole_save(engine):
    """``logout()`` replaces the data dict and clears the key: nothing to merge
    into, so the save is what it was before (a new session)."""
    save = _tracked(engine)
    old_key = save.session_key
    save.flush()
    assert not is_tracked(save)
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, old_key)
    assert save.session_key and save.session_key != old_key
    assert _stored(engine, save.session_key) == {"liveview_/x/": {"count": 1}}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_session_flushed_by_another_request_is_still_dropped_on_the_pool(engine):
    save = _tracked(engine, {SESSION_KEY: "1", "seed": 1})
    key = save.session_key
    _store(engine)(key).flush()
    save["liveview_/x/"] = {"count": 1}

    with pytest.raises(LateSaveDropped):
        _as_pool_save(lambda: save_merged(save, key))
    assert not _store(engine)().exists(key)


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_session_that_now_names_another_user_is_still_dropped_on_the_pool(engine):
    save = _tracked(engine, {SESSION_KEY: "1"})
    key = save.session_key
    _elsewhere(engine, key, **{SESSION_KEY: "2"})
    save["liveview_/x/"] = {"count": 1}
    with pytest.raises(LateSaveDropped):
        _as_pool_save(lambda: save_merged(save, key))
    assert "liveview_/x/" not in _stored(engine, key)


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_an_unreadable_store_logs_and_saves_the_whole_session(engine, monkeypatch, caplog):
    from djust import _session_merge

    save = _tracked(engine)
    key = save.session_key
    _elsewhere(engine, key, idle_ts="NEW")

    def broken(backend, name):
        raise OSError("storage down")

    monkeypatch.setattr(_session_merge, "_read_stored", broken)
    save["liveview_/x/"] = {"count": 1}
    with caplog.at_level(logging.WARNING, logger="djust._session_merge"):
        save_merged(save, key)

    assert any("could not be read" in r.getMessage() for r in caplog.records)
    stored = _stored(engine, key)
    assert stored["liveview_/x/"] == {"count": 1}
    assert stored["idle_ts"] == "OLD", "the fallback is the whole-session save of before"


def test_an_untracked_session_is_saved_whole_as_before():
    save = _store(DB)(_persisted(DB).session_key)
    key = save.session_key
    save.get("idle_ts")  # load it, as a mount does
    _elsewhere(DB, key, idle_ts="NEW")
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    assert _stored(DB, key)["idle_ts"] == "OLD"


def test_a_signed_cookie_session_saves_as_before():
    session = _store(SIGNED)()
    session["a"] = 1
    track_session(session)
    save_merged(session, None)  # no stored copy to merge into; must not raise


@pytest.mark.parametrize("engine", [DB, CACHED_DB])
def test_where_the_database_can_lock_a_row_the_read_and_write_share_a_transaction(
    engine, monkeypatch
):
    """The row lock (``SELECT ... FOR UPDATE``) is real only on PostgreSQL and
    MySQL; the test database cannot lock, so the capability is switched on and
    what is checked is the shape: the locked read, then the write, in one
    ``transaction.atomic``."""
    from django.db.models.query import QuerySet

    from djust import _session_merge

    monkeypatch.setattr(_session_merge, "_supports_row_lock", lambda backend: True)
    locked = []
    original_lock = QuerySet.select_for_update

    def spy_lock(self, *args, **kwargs):
        locked.append(connection.in_atomic_block)
        return original_lock(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", spy_lock)
    in_atomic = []
    original_save = _store(engine).save

    def spy_save(self, *args, **kwargs):
        in_atomic.append(connection.in_atomic_block)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(_store(engine), "save", spy_save)

    save = _tracked(engine)
    key = save.session_key
    _elsewhere(engine, key, idle_ts="NEW")
    save["liveview_/x/"] = {"count": 1}
    in_atomic.clear()  # the setup saved too
    with CaptureQueriesContext(connection) as queries:
        save_merged(save, key)

    assert locked == [True], "the stored session must be read with the row lock, in the transaction"
    assert in_atomic == [True], "the write must share the read's transaction"
    sql = [q["sql"] for q in queries if "django_session" in q["sql"]]
    assert len([s for s in sql if s.lstrip().upper().startswith("SELECT")]) == 1, sql
    assert len([s for s in sql if s.lstrip().upper().startswith("UPDATE")]) == 1, sql
    assert _stored(engine, key)["idle_ts"] == "NEW"


def test_without_a_row_lock_there_is_no_transaction_of_ours():
    """SQLite: no lock is possible, so holding a transaction open across the
    write would only block other connections."""
    save = _tracked(DB)
    key = save.session_key
    save["liveview_/x/"] = {"count": 1}
    in_atomic = []
    original = _store(DB).save

    def spy(self, *args, **kwargs):
        in_atomic.append(connection.in_atomic_block)
        return original(self, *args, **kwargs)

    _store(DB).save = spy
    try:
        save_merged(save, key)
    finally:
        _store(DB).save = original
    assert in_atomic == [False]


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_write_made_while_a_save_is_in_flight_is_kept_for_the_next_save(engine, monkeypatch):
    """A save that outruns its deadline overlaps the next event's handler. Its
    write is newer than the save's snapshot: it stays in memory and recorded."""
    save = _tracked(engine)
    key = save.session_key
    original = _store(engine).save
    fired = []

    def save_while_a_handler_writes(self, *args, **kwargs):
        if not fired:
            fired.append(1)
            save["during"] = "x"  # the next handler, on another thread
            save.pop("victim")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_store(engine), "save", save_while_a_handler_writes)
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)

    assert "during" not in _stored(engine, key), "the write is newer than this save"
    assert save["during"] == "x", "the live session lost the in-flight write"
    assert "victim" not in save
    assert save._session_cache.written == {"during"} and save._session_cache.removed == {"victim"}

    save_merged(save, key)
    stored = _stored(engine, key)
    assert stored["during"] == "x" and "victim" not in stored
    assert stored["liveview_/x/"] == {"count": 1}


def test_recording_and_snapshots_do_not_race_across_threads():
    import threading

    data = TrackedSessionData({})
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            data["k%d" % (i % 50)] = i
            if i % 3 == 0:
                data.pop("k%d" % ((i + 7) % 50), None)
            i += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(400):
            try:
                snap = data.snapshot()
                data.reconcile(dict(snap.changed), snap)
                data.settle(snap)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                break
    finally:
        stop.set()
        thread.join()
    assert not errors, errors


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_reference_held_across_saves_stays_attached(engine):
    """``cart = session.setdefault("cart", [])`` in ``mount()``, ``cart.append``
    in handlers, no reassignment: every event's change is saved."""
    save = _tracked(engine, {"idle_ts": "OLD"})
    key = save.session_key
    cart = save.setdefault("cart", [])
    stored_carts = []
    for n in (1, 2, 3):
        cart.append(n)
        _elsewhere(engine, key, idle_ts="NEW%d" % n)
        save["liveview_/x/"] = {"count": n}
        save_merged(save, key)
        stored = _stored(engine, key)
        stored_carts.append(stored["cart"])
        assert stored["idle_ts"] == "NEW%d" % n
        # (``dict.__getitem__``: a tracked read would itself start watching the list)
        held = dict.__getitem__(save._session_cache, "cart")
        assert held is cart, "the session replaced the object the view holds"
    assert stored_carts == [[1], [1, 2], [1, 2, 3]]


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_held_value_another_request_changed_is_replaced_not_overwritten(engine):
    save = _tracked(engine, {"prefs": {"theme": "dark"}})
    key = save.session_key
    held = save["prefs"]  # read, left alone
    _elsewhere(engine, key, prefs={"theme": "light"})
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    assert _stored(engine, key)["prefs"] == {"theme": "light"}
    assert save["prefs"] == {"theme": "light"} and held == {"theme": "dark"}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_whole_session_save_settles_what_it_wrote(engine):
    """The fallback save (here: the key moved since the save began) writes
    everything recorded, so the next merge must not apply it again."""
    save = _tracked(engine, {"idle_ts": "OLD", "gone": 1})
    key = save.session_key
    save.pop("gone")
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, "another-key")  # key != expected_key: the whole-session save
    assert not save._session_cache.written and not save._session_cache.removed
    assert is_tracked(save)

    _elsewhere(engine, key, gone="back again")
    save["liveview_/x/"] = {"count": 2}
    save_merged(save, key)
    stored = _stored(engine, key)
    assert stored["gone"] == "back again", "an old removal was applied a second time"
    assert stored["liveview_/x/"] == {"count": 2}


@pytest.mark.parametrize("how", ["clear", "flush"])
@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_tracking_resumes_after_a_clear_or_flush_in_a_handler(engine, how):
    save = _tracked(engine)
    key = save.session_key
    getattr(save, how)()
    assert not is_tracked(save)
    save["after"] = "y"
    save_merged(save, save.session_key if how == "clear" else None)
    assert is_tracked(save), "every later save of this connection would be whole"
    new_key = save.session_key
    _elsewhere(engine, new_key, idle_ts="NEW")
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, new_key)
    stored = _stored(engine, new_key)
    assert stored["idle_ts"] == "NEW" and stored["after"] == "y"
    if how == "flush":
        assert not _store(engine)().exists(key)


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_key_rotation_in_a_handler_is_saved_and_tracking_continues(engine):
    save = _tracked(engine)
    old_key = save.session_key
    save.cycle_key()
    new_key = save.session_key
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, new_key)
    assert is_tracked(save) and not _store(engine)().exists(old_key)
    _elsewhere(engine, new_key, idle_ts="NEW")
    save["liveview_/x/"] = {"count": 2}
    save_merged(save, new_key)
    stored = _stored(engine, new_key)
    assert stored["idle_ts"] == "NEW" and stored["liveview_/x/"] == {"count": 2}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_value_only_read_follows_the_store_across_two_saves(engine):
    """V0 is read and never written; another request stores V1, then V2, with a
    save in between: the second save must not write V1 back over V2."""
    save = _tracked(engine, {"prefs": {"theme": "v0"}})
    key = save.session_key
    held = save["prefs"]  # read once, never written
    assert held == {"theme": "v0"}
    _elsewhere(engine, key, prefs={"theme": "v1"})
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    assert _stored(engine, key)["prefs"] == {"theme": "v1"}

    _elsewhere(engine, key, prefs={"theme": "v2"})
    save["liveview_/x/"] = {"count": 2}
    save_merged(save, key)
    assert _stored(engine, key)["prefs"] == {"theme": "v2"}, "rolled back to the previous value"


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_key_another_request_added_is_followed_too(engine):
    save = _tracked(engine)
    key = save.session_key
    _elsewhere(engine, key, prefs={"theme": "v1"})
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)  # takes ``prefs`` over from the store
    _ = save["prefs"]
    _elsewhere(engine, key, prefs={"theme": "v2"})
    save["liveview_/x/"] = {"count": 2}
    save_merged(save, key)
    assert _stored(engine, key)["prefs"] == {"theme": "v2"}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_an_in_place_change_made_while_a_save_is_in_flight_is_kept(engine, monkeypatch):
    save = _tracked(engine)
    key = save.session_key
    cart = save.setdefault("cart", [])
    cart.append(1)
    save_merged(save, key)
    assert _stored(engine, key)["cart"] == [1]

    original = _store(engine).save
    fired = []

    def append_while_saving(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if not fired:
            fired.append(1)
            cart.append(2)  # the next handler, on another thread: in place, no stamp
        return result

    monkeypatch.setattr(_store(engine), "save", append_while_saving)
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    monkeypatch.setattr(_store(engine), "save", original)

    assert dict.__getitem__(save._session_cache, "cart") is cart, "the held list was replaced"
    assert cart == [1, 2]
    assert _stored(engine, key)["cart"] == [1]
    save["liveview_/x/"] = {"count": 2}
    save_merged(save, key)
    assert _stored(engine, key)["cart"] == [1, 2]


def test_attributes_a_session_subclass_keeps_on_the_instance_reach_its_save():
    """A ``db.SessionStore`` subclass whose middleware sets ``ip`` on the
    instance (the django-user-sessions shape): ``save()`` still sees it."""
    from django.contrib.sessions.backends import db as db_backend

    seen = []

    class IpStore(db_backend.SessionStore):
        def save(self, must_create=False):
            seen.append(self.__dict__.get("ip"))
            return super().save(must_create)

    base = IpStore()  # the class name is part of the signing salt: rows of its own
    base.update({"idle_ts": "OLD"})
    base.create()
    key = base.session_key
    save = IpStore(key)
    save.ip = "10.0.0.1"
    assert track_session(save)
    seen.clear()
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)
    assert seen == ["10.0.0.1"]
    stored = IpStore(key).load()
    assert stored["liveview_/x/"] == {"count": 1} and stored["idle_ts"] == "OLD"


def test_a_value_that_cannot_be_compared_counts_as_changed():
    """An object whose ``==`` is identity (pickle serializers only) is written
    on every save, as the whole-session save did, and never silently dropped."""

    class Opaque:
        pass

    data = TrackedSessionData({"k": {"o": Opaque()}, "plain": {"a": 1}})
    data["k"]
    data["plain"]
    snap = data.snapshot()
    assert "k" in snap.changed and "plain" not in snap.changed
    data.settle(snap)
    assert "k" in data.snapshot().changed, "still compared by identity after a save"


def test_non_string_keys_are_tolerated():
    """``PickleSerializer`` sessions can carry any key."""
    data = TrackedSessionData({1: [1], "a": [2]})
    assert data[1] == [1] and data.get(1) == [1]
    data[1].append(2)
    assert data.snapshot().changed == {}  # an int key is not watched; no error


def test_cached_dbs_cache_write_runs_after_the_transaction(monkeypatch):
    """A hung cache write must not hold the row lock: the cache is written once
    the transaction that holds it is over."""
    from django.conf import settings
    from django.core.cache import caches

    from djust import _session_merge

    monkeypatch.setattr(_session_merge, "_supports_row_lock", lambda backend: True)
    cache = caches[settings.SESSION_CACHE_ALIAS]
    seen = []
    original = cache.set

    def spy(key, value, *args, **kwargs):
        seen.append((connection.in_atomic_block, type(value)))
        return original(key, value, *args, **kwargs)

    save = _tracked(CACHED_DB)
    key = save.session_key
    monkeypatch.setattr(cache, "set", spy)
    _elsewhere(CACHED_DB, key, idle_ts="NEW")
    seen.clear()
    save["liveview_/x/"] = {"count": 1}
    save_merged(save, key)

    assert seen and all(in_atomic is False for in_atomic, _ in seen), seen
    assert all(kind is dict for _, kind in seen), "a tracker reached the cache"
    cached = original(save.cache_key) if False else cache.get(save.cache_key_prefix + key)
    assert cached["idle_ts"] == "NEW" and cached["liveview_/x/"] == {"count": 1}


# --------------------------------------------------------------------------- #
# End to end over a WebSocket (the issue's table)
# --------------------------------------------------------------------------- #


class Legacy3347Page(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = '<div dj-root dj-view="%s.Legacy3347Page" dj-id="0">n={{ n }}</div>' % _MOD

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    @event_handler()
    def remember(self, **kwargs):
        """A handler writing through the session object the save writes."""
        self.request.session["from_handler"] = "kept"
        self.request.session.setdefault("cart", []).append("item")
        self.n += 1


class Explicit3347Page(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-view="%s.Explicit3347Page" dj-id="0">n={{ n }}</div>' % _MOD
    n = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    @event_handler()
    def through_a_separate_store(self, **kwargs):
        """The handler writes ``idle_ts`` through a store of its own mid-turn."""
        request = self.request
        other = type(request.session)(request.session.session_key)
        other["idle_ts"] = "NEW"
        other.save()
        self.n += 1

    @event_handler()
    def through_the_request_session(self, **kwargs):
        self.request.session["from_handler"] = "kept"
        self.n += 1


#: A test's slow save sets ``in_flight``; ``Handlers3347Page.stamp`` reports it.
FLIGHT: dict = {}


class Handlers3347Page(LiveView):
    """Handlers that rotate, clear or flush the session, stamp it, or keep a
    reference to a list that lives in it."""

    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = '<div dj-root dj-view="%s.Handlers3347Page" dj-id="0">n={{ n }}</div>' % _MOD

    def mount(self, request, **kwargs):
        self.n = 0
        self._cart = request.session.setdefault("cart", [])
        self._prefs = request.session.get("prefs")  # read, never written

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    @event_handler()
    def stamp(self, **kwargs):
        self.n += 1
        FLIGHT.setdefault("handled_while_in_flight", []).append(FLIGHT.get("in_flight", False))
        self.request.session["w%d" % self.n] = 1

    @event_handler()
    def add_to_cart(self, **kwargs):
        """Appends in place to the list ``mount()`` took from the session."""
        self.n += 1
        FLIGHT.setdefault("handled_while_in_flight", []).append(FLIGHT.get("in_flight", False))
        self._cart.append(self.n)

    @event_handler()
    def rotate(self, **kwargs):
        self.n += 1
        self.request.session.cycle_key()

    @event_handler()
    def wipe(self, **kwargs):
        self.n += 1
        self.request.session.clear()
        self.request.session["after_clear"] = "y"

    @event_handler()
    def flush(self, **kwargs):
        self.n += 1
        self.request.session.flush()


async def _connect(engine):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    key = (await sync_to_async(_persisted)(engine)).session_key
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _store(engine)(key)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator, key


async def _until(communicator, pred):
    with _ALLOWLIST:
        for _ in range(12):
            msg = await communicator.receive_json_from(timeout=3)
            if pred(msg):
                return msg
    raise AssertionError("expected frame never arrived")


async def _mount(communicator, view, url):
    with _ALLOWLIST:
        await communicator.send_json_to({"type": "mount", "view": view, "url": url})
    return await _until(communicator, lambda m: m.get("type") == "mount")


async def _event(communicator, name):
    with _ALLOWLIST:
        await communicator.send_json_to({"type": "event", "event": name, "params": {}})
    return await _until(
        communicator, lambda m: m.get("type") in ("patch", "html_update", "noop", "error")
    )


async def _settled(communicator):
    """Disconnect: teardown waits for the runtime's saves."""
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
async def test_ws_legacy_event_keeps_what_another_request_stored(engine):
    """Legacy over WS: another store sets ``idle_ts=NEW`` and deletes ``victim``
    after mount, then an event."""
    url = "/legacy-3347/"
    with override_settings(SESSION_ENGINE=engine):
        communicator, key = await _connect(engine)
        try:
            await _mount(communicator, f"{_MOD}.Legacy3347Page", url)
            await sync_to_async(_elsewhere)(engine, key, idle_ts="NEW", victim=None)
            frame = await _event(communicator, "bump")
            assert frame["type"] in ("patch", "html_update"), frame
        finally:
            await _settled(communicator)
        stored = await sync_to_async(_stored)(engine, key)
    assert stored[f"liveview_{url}"] == {"n": 1}, "the event's state was not saved; vacuous"
    assert stored["idle_ts"] == "NEW", "idle_ts was rolled back to its mount-time value"
    assert "victim" not in stored, "a deleted key came back"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
async def test_ws_legacy_handler_session_writes_still_reach_the_store(engine):
    url = "/legacy-3347-handler/"
    with override_settings(SESSION_ENGINE=engine):
        communicator, key = await _connect(engine)
        try:
            await _mount(communicator, f"{_MOD}.Legacy3347Page", url)
            await sync_to_async(_elsewhere)(engine, key, idle_ts="NEW")
            frame = await _event(communicator, "remember")
            assert frame["type"] in ("patch", "html_update"), frame
        finally:
            await _settled(communicator)
        stored = await sync_to_async(_stored)(engine, key)
    assert stored["from_handler"] == "kept"
    assert stored["cart"] == ["item"]
    assert stored["idle_ts"] == "NEW"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
async def test_ws_explicit_event_keeps_a_write_made_through_a_separate_store(engine, staged):
    """Explicit ``persist="server"``: the handler writes ``idle_ts`` through a
    store of its own during the turn."""
    url = "/explicit-3347/"
    with override_settings(SESSION_ENGINE=engine, DEBUG=False):
        communicator, key = await _connect(engine)
        try:
            await _mount(communicator, f"{_MOD}.Explicit3347Page", url)
            await sync_to_async(_elsewhere)(engine, key, victim=None)
            frame = await _event(communicator, "through_a_separate_store")
            assert frame["type"] in ("patch", "html_update"), frame
        finally:
            await _settled(communicator)
        stored = await sync_to_async(_stored)(engine, key)
    assert any(k.startswith("_djust_explicit_") for k in stored), "no explicit state saved"
    assert stored["idle_ts"] == "NEW", "the handler's separate-store write was rolled back"
    assert "victim" not in stored


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
async def test_ws_explicit_handler_session_writes_still_reach_the_store(engine, staged):
    url = "/explicit-3347-request/"
    with override_settings(SESSION_ENGINE=engine, DEBUG=False):
        communicator, key = await _connect(engine)
        try:
            await _mount(communicator, f"{_MOD}.Explicit3347Page", url)
            await sync_to_async(_elsewhere)(engine, key, idle_ts="NEW")
            frame = await _event(communicator, "through_the_request_session")
            assert frame["type"] in ("patch", "html_update"), frame
        finally:
            await _settled(communicator)
        stored = await sync_to_async(_stored)(engine, key)
    assert stored["from_handler"] == "kept"
    assert stored["idle_ts"] == "NEW"


# --------------------------------------------------------------------------- #
# End to end over SSE
# --------------------------------------------------------------------------- #

urlpatterns = [
    path("sse-handlers-3347/", Handlers3347Page.as_view()),
    path("sse-legacy-3347/", Legacy3347Page.as_view()),
    path("sse-explicit-3347/", Explicit3347Page.as_view()),
]


def _request(engine, method, url, body, key):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = _store(engine)(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_sse_event_keeps_what_another_request_stored(engine, policy, staged):
    page = {"legacy": "Legacy3347Page", "explicit": "Explicit3347Page"}[policy]
    url = f"/sse-{policy}-3347/"
    with override_settings(
        ROOT_URLCONF=_MOD,
        LIVEVIEW_ALLOWED_MODULES=[_MOD],
        DEBUG=False,
        SESSION_ENGINE=engine,
    ):
        key = (await sync_to_async(_persisted)(engine)).session_key
        sid = str(uuid.uuid4())
        get = await sync_to_async(_request)(
            engine, "GET", f"/djust/sse/{sid}/", {"view": f"{_MOD}.{page}", "_djust_url": url}, key
        )
        try:
            assert (await DjustSSEStreamView().get(get, session_id=sid)).status_code == 200
            await sync_to_async(_elsewhere)(engine, key, idle_ts="NEW", victim=None)
            post = await sync_to_async(_request)(
                engine,
                "POST",
                f"/djust/sse/{sid}/message/",
                {"type": "event", "event": "bump", "params": {}},
                key,
            )
            async with ThreadSensitiveContext():
                response = await DjustSSEMessageView().post(post, session_id=sid)
            assert response.status_code == 200
            runtime = _sse_sessions[sid].runtime
            await runtime.finish_state_saves()
            pending = runtime._explicit_save_pending
            if pending is not None:
                await asyncio.wait_for(asyncio.shield(pending), 10)
            stored = await sync_to_async(_stored)(engine, key)
        finally:
            _sse_sessions.clear()
    if policy == "legacy":
        assert stored[f"liveview_{url}"] == {"n": 1}, "the event's state was not saved; vacuous"
    else:
        assert any(k.startswith("_djust_explicit_") for k in stored), stored
    assert stored["idle_ts"] == "NEW", "idle_ts was rolled back to its connect-time value"
    assert "victim" not in stored, "a deleted key came back"


# --------------------------------------------------------------------------- #
# End to end: handlers, held references, a write while a save is in flight
# --------------------------------------------------------------------------- #

_HANDLERS = f"{_MOD}.Handlers3347Page"


def _session_keys():
    from django.contrib.sessions.models import Session

    return set(Session.objects.values_list("session_key", flat=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
async def test_ws_a_list_held_by_the_view_keeps_being_saved(engine):
    url = "/handlers-cart-3347/"
    carts = []
    with override_settings(SESSION_ENGINE=engine):
        communicator, key = await _connect(engine)
        try:
            await _mount(communicator, _HANDLERS, url)
            for _ in range(3):
                frame = await _event(communicator, "add_to_cart")
                assert frame["type"] in ("patch", "html_update"), frame
                carts.append((await sync_to_async(_stored)(engine, key))["cart"])
        finally:
            await _settled(communicator)
    assert carts == [[1], [1, 2], [1, 2, 3]]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
async def test_ws_a_value_the_view_only_read_follows_the_store_across_events(engine):
    """``mount()`` reads ``prefs`` and no handler writes it; another request
    changes it between events 1 and 2, and again between 2 and 3."""
    url = "/handlers-prefs-3347/"
    with override_settings(SESSION_ENGINE=engine):
        communicator, key = await _connect(engine)
        try:
            await sync_to_async(_elsewhere)(engine, key, prefs={"theme": "v0"})
            await _mount(communicator, _HANDLERS, url)
            for version in ("v1", "v2", "v3"):
                await sync_to_async(_elsewhere)(engine, key, prefs={"theme": version})
                frame = await _event(communicator, "stamp")
                assert frame["type"] in ("patch", "html_update"), frame
                stored = await sync_to_async(_stored)(engine, key)
                assert stored["prefs"] == {"theme": version}, (version, stored["prefs"])
        finally:
            await _settled(communicator)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", SERVER_BACKENDS)
async def test_ws_a_handler_cycling_the_key_keeps_the_state_and_later_merges(engine):
    url = "/handlers-rotate-3347/"
    with override_settings(SESSION_ENGINE=engine):
        communicator, key = await _connect(engine)
        try:
            await _mount(communicator, _HANDLERS, url)
            frame = await _event(communicator, "rotate")
            assert frame["type"] in ("patch", "html_update"), frame
            assert not await sync_to_async(_store(engine)().exists)(key), "old key survived"
            if engine == DB:
                (new_key,) = await sync_to_async(_session_keys)()
                await sync_to_async(_elsewhere)(engine, new_key, idle_ts="NEW")
                frame = await _event(communicator, "stamp")
                assert frame["type"] in ("patch", "html_update"), frame
                stored = await sync_to_async(_stored)(engine, new_key)
                assert stored[f"liveview_{url}"]["n"] == 2
                assert stored["w2"] == 1 and stored["idle_ts"] == "NEW"
        finally:
            await _settled(communicator)


@pytest.mark.asyncio
async def test_ws_a_handler_clearing_the_session_does_not_end_merging():
    url = "/handlers-wipe-3347/"
    communicator, key = await _connect(DB)
    try:
        await _mount(communicator, _HANDLERS, url)
        frame = await _event(communicator, "wipe")
        assert frame["type"] in ("patch", "html_update"), frame
        stored = await sync_to_async(_stored)(DB, key)
        assert stored["after_clear"] == "y" and "victim" not in stored
        assert stored[f"liveview_{url}"]["n"] == 1

        await sync_to_async(_elsewhere)(DB, key, idle_ts="NEW")
        frame = await _event(communicator, "stamp")
        assert frame["type"] in ("patch", "html_update"), frame
        stored = await sync_to_async(_stored)(DB, key)
        assert stored["idle_ts"] == "NEW", "the session stayed untracked after clear()"
        assert stored["w2"] == 1 and stored["after_clear"] == "y"
    finally:
        await _settled(communicator)


@pytest.mark.asyncio
async def test_ws_a_handler_flushing_the_session_is_not_resurrected():
    url = "/handlers-flush-3347/"
    communicator, key = await _connect(DB)
    try:
        await _mount(communicator, _HANDLERS, url)
        frame = await _event(communicator, "flush")
        assert frame["type"] in ("patch", "html_update"), frame
    finally:
        await _settled(communicator)
    assert not await sync_to_async(_store(DB)().exists)(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("events", [("stamp", "stamp"), ("stamp", "add_to_cart")])
async def test_sse_a_handler_write_made_while_a_save_is_in_flight_is_kept(events, staged):
    """A save past its deadline keeps running on the save pool while the next
    event's handler runs. That handler's ``request.session`` write (an
    assignment, or an in-place append to a list the view holds) must reach the
    store."""
    import threading

    url = "/sse-handlers-3347/"
    release = threading.Event()
    blocked = []
    FLIGHT.clear()
    original = _store(DB).save

    def slow_first_pool_save(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if not blocked and threading.current_thread().name.startswith("djust-state-save"):
            # Stored and not yet finished: the window after the write.
            blocked.append(1)
            FLIGHT["in_flight"] = True
            release.wait(timeout=10)
            FLIGHT["in_flight"] = False
        return result

    with override_settings(
        ROOT_URLCONF=_MOD, LIVEVIEW_ALLOWED_MODULES=[_MOD], DEBUG=False, SESSION_ENGINE=DB
    ):
        key = (await sync_to_async(_persisted)(DB)).session_key
        sid = str(uuid.uuid4())
        get = await sync_to_async(_request)(
            DB, "GET", f"/djust/sse/{sid}/", {"view": _HANDLERS, "_djust_url": url}, key
        )

        async def post_event(event):
            post = await sync_to_async(_request)(
                DB,
                "POST",
                f"/djust/sse/{sid}/message/",
                {"type": "event", "event": event, "params": {}},
                key,
            )
            async with ThreadSensitiveContext():
                response = await DjustSSEMessageView().post(post, session_id=sid)
            assert response.status_code == 200

        try:
            assert (await DjustSSEStreamView().get(get, session_id=sid)).status_code == 200
            runtime = _sse_sessions[sid].runtime
            warm = events[1] == "add_to_cart"
            if warm:
                # One settled save first, so the list's mount-time write is not
                # part of the snapshot of the save that is then held.
                await post_event("stamp")
                if runtime._explicit_save_pending is not None:
                    await asyncio.wait_for(asyncio.shield(runtime._explicit_save_pending), 10)
            _store(DB).save = slow_first_pool_save
            try:
                for event in events:
                    await post_event(event)
                assert blocked, "the first save never blocked on the pool; vacuous"
                assert FLIGHT["handled_while_in_flight"] == [False] * warm + [False, True], (
                    "the second handler must run while the first save is in flight; vacuous"
                )
            finally:
                release.set()
                _store(DB).save = original
            await runtime.finish_state_saves()
            pending = runtime._explicit_save_pending
            if pending is not None:
                await asyncio.wait_for(asyncio.shield(pending), 10)
            stored = await sync_to_async(_stored)(DB, key)
        finally:
            _sse_sessions.clear()
    assert stored[f"liveview_{url}"]["n"] == 2 + warm
    if events[1] == "stamp":
        assert stored.get("w1") == 1, stored
        assert stored.get("w2") == 1, "the write made while the first save was in flight was lost"
    else:
        # The second handler appends in place to the list ``mount()`` holds,
        # while the first save (which did not touch it) is in flight.
        assert stored["cart"] == [3], "the in-place change made during the save was lost"
