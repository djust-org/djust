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
