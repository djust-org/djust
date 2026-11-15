"""Save frequency, step 2: the holder fence.

Once a turn's save can come after the turn (a deferred save, a disconnect
flush), a connection the client has left can write AFTER another process
took the tab and moved on, replacing newer state with older. The fence:

* every taker of a tab whose saves may be deferred (a socket mount, found
  state or not; an HTTP fallback request) writes a new holder record, a
  random token, in the session (``_automatic_state._hold``);
* a socket's save is refused only when a DIFFERENT, unexpired token is
  stored: in the session merge (under the row lock on ``db`` and
  ``cached_db``), and before the write of a state store, which is written
  before the session's index entry. Whether or not a state entry is stored:
  evicted, deleted, expired or never written makes no difference. No record
  (wiped, evicted, expired) means the writer is the holder: it writes and
  records its own token again. The full table is in
  ``djust._automatic_state._hold``;
* a fallback request writes within its own request, as before: it fences
  out an older socket, not another fallback request (decision 4).

Every view-level test is a real two-writer race: a late writer must not
replace newer state. Each has a sibling with claims off (today's behaviour,
``holder_claims`` is False with no interval set), where the late write does
land: that is the gate-off of the claim, and the proof that by default
nothing changed.
"""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any, Dict, Optional

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import path as _path

from djust import LiveView, _automatic_state, _save_policy
from djust._session_merge import TrackedSessionData, holder_refuses, save_merged, track_session
from djust.decorators import event_handler
from djust.state_store import StateKey, StateSuperseded, ViewStateStore
from djust.tests import _per_turn_harness as harness
from djust.tests import _save_frequency as sf

pytestmark = pytest.mark.django_db(transaction=True)

MODULE = __name__
DB = "django.contrib.sessions.backends.db"
CACHE = "django.contrib.sessions.backends.cache"
CACHED_DB = "django.contrib.sessions.backends.cached_db"
FILE = "django.contrib.sessions.backends.file"
ENGINES = [DB, CACHE, CACHED_DB, FILE]
KEY = "_djust_auto_fence_test"
HOLDER = "_djust_hold_fence_test"
FAR = 4102444800  # 2100-01-01: an unexpired record


class Memory(ViewStateStore):
    """A state store outside the session."""

    DATA: Dict[str, bytes] = {}
    SAVES: list = []

    def load(self, key: StateKey) -> Optional[bytes]:
        return type(self).DATA.get(key.id)

    def save(self, key: StateKey, payload: bytes, *, expires: int) -> None:
        type(self).SAVES.append(key.id)
        type(self).DATA[key.id] = payload

    def delete(self, keys) -> None:
        for key in keys:
            type(self).DATA.pop(key.id, None)


EXTERNAL = MODULE + ".Memory"
SEALED = "djust.state_store.SealedSessionViewStateStore"
STORES = [None, EXTERNAL, SEALED]
STORE_IDS = ["session", "external", "sealed"]


@pytest.fixture(autouse=True)
def _each(monkeypatch, settings, tmp_path):
    Memory.DATA.clear()
    Memory.SAVES.clear()
    settings.SESSION_FILE_PATH = str(tmp_path)
    from djust import state_store

    settings.DJUST_STATE_STORE_KEYS = [state_store.generate_key()]
    sf.one_tab(monkeypatch)


@pytest.fixture
def claims(monkeypatch):
    """Takers claim the tab (what a view whose saves may be deferred does)."""
    monkeypatch.setattr(_save_policy, "holder_claims", lambda view: True)


# ---------------------------------------------------------------------------
# The merge: two session objects on one row
# ---------------------------------------------------------------------------


def _session(engine: str, key: Optional[str] = None) -> Any:
    session = import_module(engine).SessionStore(key)
    if key is None:
        session.create()
    else:
        assert track_session(session)
    return session


def _claim(session: Any, token: str, expires: int = FAR) -> None:
    session[HOLDER] = {"token": token, "expires": expires}
    save_merged(session, session.session_key)


def _write(session: Any, value: str, token: str) -> None:
    session[KEY] = {"value": value}
    session[HOLDER] = {"token": token, "expires": FAR}
    data = session._session_cache
    assert type(data) is TrackedSessionData
    data.fence(KEY, HOLDER, token)
    save_merged(session, session.session_key)


def _stored(engine: str, key: str) -> dict:
    return dict(import_module(engine).SessionStore(key).load())


@pytest.mark.parametrize("engine", ENGINES)
def test_a_late_writer_does_not_replace_what_a_newer_holder_wrote(engine):
    key = _session(engine).session_key
    holder_a = _session(engine, key)
    _claim(holder_a, "token-a")
    _write(holder_a, "a1", "token-a")
    holder_b = _session(engine, key)
    _claim(holder_b, "token-b")  # B takes the tab
    _write(holder_b, "b2", "token-b")  # and moves on
    holder_a["cart"] = "kept"  # A's own unrelated write rides along
    with pytest.raises(StateSuperseded):
        _write(holder_a, "a-late", "token-a")
    stored = _stored(engine, key)
    assert stored[KEY] == {"value": "b2"}
    assert stored[HOLDER]["token"] == "token-b"
    assert stored["cart"] == "kept"  # the rest of the snapshot was written
    # A's live session took B's entries back, unrecorded: nothing re-writes A's.
    assert dict.__getitem__(holder_a._session_cache, KEY) == {"value": "b2"}
    holder_a["other"] = 1
    save_merged(holder_a, key)
    assert _stored(engine, key)[KEY] == {"value": "b2"}


@pytest.mark.parametrize("engine", ENGINES)
def test_another_holders_token_refuses_with_no_entry_stored(engine):
    """Nothing stored for the tab (evicted, deleted, expired, never written):
    the record alone decides."""
    key = _session(engine).session_key
    holder_a = _session(engine, key)
    _claim(holder_a, "token-a")
    _claim(_session(engine, key), "token-b")
    assert KEY not in _stored(engine, key)
    with pytest.raises(StateSuperseded):
        _write(holder_a, "a-late", "token-a")
    stored = _stored(engine, key)
    assert KEY not in stored and stored[HOLDER]["token"] == "token-b"
    assert KEY not in dict(holder_a._session_cache)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("record", ["absent", "own_expired", "other_expired"])
def test_with_no_live_record_of_another_the_writer_holds_and_re_records(engine, record):
    """Wiped, evicted or expired: no other holder is known, so the writer is
    the holder. It writes, and its own token is recorded again, unexpired."""
    key = _session(engine).session_key
    holder_a = _session(engine, key)
    _claim(holder_a, "token-a")
    other = _session(engine, key)
    if record == "absent":
        del other[HOLDER]
        save_merged(other, key)
    elif record == "own_expired":
        _claim(other, "token-a", expires=1)
    else:
        _claim(other, "token-b", expires=1)
    _write(holder_a, "a2", "token-a")
    stored = _stored(engine, key)
    assert stored[KEY] == {"value": "a2"}
    assert stored[HOLDER] == {"token": "token-a", "expires": FAR}


@pytest.mark.parametrize("engine", ENGINES)
def test_without_a_fence_the_late_write_lands_as_before(engine):
    """The gate-off: the same race with no fence registered overwrites."""
    key = _session(engine).session_key
    holder_a = _session(engine, key)
    holder_a[KEY] = {"value": "a1"}
    save_merged(holder_a, key)
    holder_b = _session(engine, key)
    _claim(holder_b, "token-b")
    _write(holder_b, "b2", "token-b")
    holder_a[KEY] = {"value": "a-late"}
    save_merged(holder_a, key)  # no fence: today's merge
    assert _stored(engine, key)[KEY]["value"] == "a-late"


def test_only_another_unexpired_token_refuses():
    assert holder_refuses({"token": "u", "expires": FAR}, "t")
    assert holder_refuses({"token": "u", "expires": 6}, "t", now=5)
    assert not holder_refuses({"token": "u", "expires": 5}, "t", now=5)  # expired
    assert not holder_refuses({"token": "t", "expires": FAR}, "t")
    assert not holder_refuses({"token": "t", "expires": 1}, "t")  # its own, expired
    assert not holder_refuses(None, "t")
    assert not holder_refuses({"token": "u"}, "t")  # unreadable
    assert not holder_refuses({"token": 5, "expires": FAR}, "t")


# ---------------------------------------------------------------------------
# Views: a socket that no longer holds the tab writes late
# ---------------------------------------------------------------------------


class FenceCounter(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def set_to(self, value: int = 0, **kwargs):
        self.count = int(value)

    @event_handler()
    def nothing(self, **kwargs):
        pass

    @event_handler()
    def late(self, **kwargs):
        """Set a value, then let another request take the tab and save before
        this request's own save runs (its handler is still running)."""
        self.count = 99
        LATE["other"]()

    def get_context_data(self, **kwargs):
        return {"count": self.count}


LATE: Dict[str, Any] = {}
urlpatterns = [_path(sf.PATH.strip("/") + "/", FenceCounter.as_view())]


def _request(engine: str, key: Optional[str], method: str = "GET", event: str = "", params=None):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(sf.PATH)
    else:
        request = factory.post(
            sf.PATH,
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=sf.TAB,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = import_module(engine).SessionStore(key)
    if key is None:
        request.session.create()
    return request


def _get(engine: str, key: Optional[str] = None) -> str:
    request = _request(engine, key)
    assert FenceCounter().get(request).status_code == 200
    return str(request.session.session_key)


def _post(engine: str, key: str, event: str, params=None) -> Any:
    return FenceCounter().post(_request(engine, key, "POST", event, params))


def _restored_count(engine: str, key: str) -> int:
    """What the next request restores (an unknown event changes nothing)."""
    view = FenceCounter()
    view.post(_request(engine, key, "POST", "no_such_event"))
    return view.count


async def _mount(engine: str, key: str) -> Any:
    """A socket on ``engine``'s session, through the real consumer."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = await sync_to_async(import_module(engine).SessionStore)(key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    await comm.send_json_to(
        {"type": "mount", "view": f"{MODULE}.FenceCounter", "url": sf.PATH, "tab": sf.TAB}
    )
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm


async def _reply_or_error(comm: Any, name: str, params: Any) -> dict:
    """The frame that answers one event: its reply, or the error frame a
    refused save sends instead (which carries no ``ref``)."""
    from djust.tests._ws_frames import receive_until

    await comm.send_json_to({"type": "event", "event": name, "params": params, "ref": 77})

    def done(frames: list) -> bool:
        return any(frame.get("type") == "error" or frame.get("ref") == 77 for frame in frames)

    frames = await receive_until(comm, done, what="the answer to %r" % name)
    return frames[-1]


def _drop_tab_state(engine: str, key: str) -> None:
    """``djust clear``: the tab's saved state goes (its holder record stays)."""
    session = import_module(engine).SessionStore(key)
    for name in [n for n in session.keys() if n.startswith("_djust_auto_")]:
        del session[name]
    session.save()


def _expire_tab_state(engine: str, key: str) -> None:
    session = import_module(engine).SessionStore(key)
    for name in [n for n in session.keys() if n.startswith("_djust_auto_")]:
        entry = dict(session[name])
        entry["created"], entry["expires"] = 1, 2
        session[name] = entry
    session.save()


def _evict_with_eight_more_tabs(engine: str, key: str, monkeypatch: Any) -> None:
    """Eight more page loads of the page in the same browser session: the
    tab cap (``MAX_TABS``) evicts the oldest tab's state."""
    import itertools

    ids = ("other-tab-%d" % n for n in itertools.count())
    with monkeypatch.context() as patch:
        patch.setattr(_automatic_state, "new_tab_id", lambda: next(ids))
        for _ in range(_automatic_state.MAX_TABS):
            _get(engine, key)
    session = import_module(engine).SessionStore(key)
    tabs = [n for n in session.keys() if n.startswith("_djust_auto_")]
    assert len(tabs) == _automatic_state.MAX_TABS


VARIANTS = ["restored", "cleared", "evicted", "expired", "fresh_socket"]


async def _newer_holder(variant: str, engine: str, key: str, monkeypatch: Any) -> int:
    """B takes the tab after the given loss of its stored state, and saves.
    Returns the value B left."""
    if variant == "cleared":
        await sync_to_async(_drop_tab_state)(engine, key)
    elif variant == "evicted":
        await sync_to_async(_evict_with_eight_more_tabs)(engine, key, monkeypatch)
    elif variant == "expired":
        await sync_to_async(_expire_tab_state)(engine, key)
    if variant == "fresh_socket":
        await sync_to_async(_drop_tab_state)(engine, key)
        comm_b = await _mount(engine, key)  # starts from mount(): nothing to restore
        try:
            assert (await harness.event(comm_b, "set_to", {"value": 3}))["type"] != "error"
        finally:
            await comm_b.disconnect()
        return 3
    newer = await sync_to_async(_post)(engine, key, "set_to", {"value": 7})
    assert newer.status_code == 200, newer.content
    return 7


async def _socket_race(engine: str, variant: str, monkeypatch: Any) -> tuple:
    key = await sync_to_async(_get)(engine)
    comm = await _mount(engine, key)  # A takes the tab
    try:
        assert (await harness.event(comm, "set_to", {"value": 5}))["type"] != "error"
        left = await _newer_holder(variant, engine, key, monkeypatch)
        reply = await _reply_or_error(comm, "set_to", {"value": 1})  # A writes late
    finally:
        await comm.disconnect()
    return reply, left, await sync_to_async(_restored_count)(engine, key)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("engine", ENGINES)
async def test_a_socket_that_no_longer_holds_the_tab_cannot_write_it(
    engine, variant, settings, claims, monkeypatch
):
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, SESSION_ENGINE=engine):
        reply, left, restored = await _socket_race(engine, variant, monkeypatch)
    assert reply["type"] == "error" and reply.get("code") == "state_error", reply
    assert restored == left


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("store", STORES, ids=STORE_IDS)
async def test_the_same_through_each_state_store(store, variant, settings, claims, monkeypatch):
    settings.DJUST_STATE_STORE = store
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        reply, left, restored = await _socket_race(DB, variant, monkeypatch)
    assert reply.get("code") == "state_error", reply
    assert restored == left


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
async def test_without_claims_the_late_socket_write_lands_as_it_does_today(variant, monkeypatch):
    """Default behaviour: nothing claims, the late write wins (unchanged)."""
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        reply, _left, restored = await _socket_race(DB, variant, monkeypatch)
    assert reply["type"] != "error", reply
    assert restored == 1


@pytest.mark.asyncio
async def test_of_two_fresh_sockets_the_later_one_holds_the_tab(claims):
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        await sync_to_async(_drop_tab_state)(DB, key)
        first = await _mount(DB, key)
        second = await _mount(DB, key)
        try:
            refused = await _reply_or_error(first, "set_to", {"value": 1})
            kept = await _reply_or_error(second, "set_to", {"value": 2})
        finally:
            await first.disconnect()
            await second.disconnect()
        restored = await sync_to_async(_restored_count)(DB, key)
    assert refused.get("code") == "state_error", refused
    assert kept["type"] != "error", kept
    assert restored == 2


@pytest.mark.asyncio
async def test_an_external_store_is_not_written_by_a_superseded_socket(settings, claims):
    """The store is written before the index: the check must come first, or
    the newer holder's bytes are replaced and its index stamp then refuses
    them (its state would be lost, not just stale)."""
    settings.DJUST_STATE_STORE = EXTERNAL
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm = await _mount(DB, key)
        try:
            assert (await harness.event(comm, "set_to", {"value": 5}))["type"] != "error"
            assert (await sync_to_async(_post)(DB, key, "set_to", {"value": 7})).status_code == 200
            saves_before = len(Memory.SAVES)
            reply = await _reply_or_error(comm, "set_to", {"value": 1})
            assert len(Memory.SAVES) == saves_before  # the late holder wrote nothing
        finally:
            await comm.disconnect()
        restored = await sync_to_async(_restored_count)(DB, key)
    assert reply.get("code") == "state_error", reply
    assert restored == 7


@pytest.mark.asyncio
async def test_a_superseded_socket_is_refused_even_by_a_turn_that_changes_nothing(claims):
    """Its view no longer matches the store (the newer holder's state), so no
    success frame may vouch for it, written or not."""
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm = await _mount(DB, key)
        try:
            assert (await harness.event(comm, "set_to", {"value": 5}))["type"] != "error"
            assert (await sync_to_async(_post)(DB, key, "set_to", {"value": 7})).status_code == 200
            first = await _reply_or_error(comm, "set_to", {"value": 1})
            again = await _reply_or_error(comm, "nothing", {})
        finally:
            await comm.disconnect()
        restored = await sync_to_async(_restored_count)(DB, key)
    assert first.get("code") == again.get("code") == "state_error", (first, again)
    assert restored == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("claiming", [True, False], ids=["claims", "today"])
async def test_an_sse_holder_superseded_by_a_fallback_request(monkeypatch, claiming):
    from djust.sse import _sse_sessions

    monkeypatch.setattr(_save_policy, "holder_claims", lambda view: claiming)
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        before = set(_sse_sessions)
        await sf.sse_run(FenceCounter, key, [("set_to", {"value": 5})], MODULE, keep=True)
        (sid,) = set(_sse_sessions) - before
        try:
            newer = await sync_to_async(_post)(DB, key, "set_to", {"value": 7})
            await sf.sse_post(sid, key, "set_to", {"value": 1})
            view = _sse_sessions[sid].runtime.view_instance
        finally:
            _sse_sessions.pop(sid, None)
        restored = await sync_to_async(_restored_count)(DB, key)
    assert newer.status_code == 200
    if claiming:
        assert view.__dict__.get("_djust_auto_superseded") is True
        assert restored == 7
    else:
        assert not view.__dict__.get("_djust_auto_superseded")
        assert restored == 1


# ---------------------------------------------------------------------------
# Decision 4: the fallback writes within its own request, as before
# ---------------------------------------------------------------------------


def _overlap(engine: str) -> tuple:
    """Request A loads the tab and runs ``late``; inside it request B loads,
    sets 7 and saves; then A saves 99. The lost update main has too."""
    key = _get(engine)
    assert _post(engine, key, "set_to", {"value": 5}).status_code == 200
    answers = {}

    def other() -> None:
        answers["b"] = _post(engine, key, "set_to", {"value": 7})

    LATE["other"] = other
    try:
        answers["a"] = _post(engine, key, "late")
    finally:
        LATE.clear()
    return key, answers["a"], answers["b"]


@pytest.mark.parametrize("claiming", [True, False], ids=["claims", "today"])
@pytest.mark.parametrize("store", STORES, ids=STORE_IDS)
@pytest.mark.parametrize("engine", ENGINES)
def test_overlapping_fallback_requests_answer_as_they_always_did(
    engine, store, claiming, settings, monkeypatch
):
    """A fallback request rebuilds the view and saves within itself: it is
    no late writer, and it is not fenced against another fallback request.
    The later save wins, with or without the fence, as on main."""
    monkeypatch.setattr(_save_policy, "holder_claims", lambda view: claiming)
    settings.SESSION_ENGINE = engine
    settings.ROOT_URLCONF = MODULE
    settings.DJUST_STATE_STORE = store
    key, late, newer = _overlap(engine)
    assert newer.status_code == late.status_code == 200, late.content
    assert _restored_count(engine, key) == 99


def test_without_claims_a_restore_writes_nothing(settings):
    """The claim is the only new write, and it is off by default."""
    from django.contrib.sessions.models import Session

    settings.ROOT_URLCONF = MODULE
    key = _get(DB)
    assert _post(DB, key, "set_to", {"value": 3}).status_code == 200
    row = Session.objects.get(session_key=key).session_data
    view = FenceCounter()
    view.post(_request(DB, key, "POST", "no_such_event"))
    assert Session.objects.get(session_key=key).session_data == row
    stored = import_module(DB).SessionStore(key)
    assert not [n for n in stored.keys() if n.startswith(_automatic_state._HOLDER_PREFIX)]


def test_holder_records_are_bounded(claims, settings):
    settings.ROOT_URLCONF = MODULE
    key = _get(DB)
    session = _session(DB, key)
    for n in range(_automatic_state.MAX_HOLDERS + 5):
        session["%sx%d" % (_automatic_state._HOLDER_PREFIX, n)] = {"token": "t", "expires": FAR - n}
    session["%sold" % _automatic_state._HOLDER_PREFIX] = {"token": "t", "expires": 1}
    save_merged(session, key)
    assert _post(DB, key, "inc").status_code == 200  # a taker prunes
    stored = import_module(DB).SessionStore(key)
    held = [n for n in stored.keys() if n.startswith(_automatic_state._HOLDER_PREFIX)]
    assert len(held) == _automatic_state.MAX_HOLDERS
    assert "%sold" % _automatic_state._HOLDER_PREFIX not in held


# ---------------------------------------------------------------------------
# A legitimate sole holder keeps holding (the second review)
# ---------------------------------------------------------------------------


def _holders(engine: str, key: str) -> dict:
    session = import_module(engine).SessionStore(key)
    return {
        n: v for n, v in session.load().items() if n.startswith(_automatic_state._HOLDER_PREFIX)
    }


def _plain_django_request_saves_its_old_copy(engine: str, key: str, before: dict) -> None:
    """A plain Django request that read the session before the mount saves
    its whole dict after it (``SessionMiddleware``): the record is gone."""
    session = import_module(engine).SessionStore(key)
    session._session_cache = dict(before)
    session.modified = True
    session.save()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ENGINES)
async def test_a_sole_holder_whose_record_was_wiped_keeps_saving(engine, claims):
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, SESSION_ENGINE=engine):
        key = await sync_to_async(_get)(engine)
        before = await sync_to_async(lambda: dict(import_module(engine).SessionStore(key).load()))()
        comm = await _mount(engine, key)
        try:
            await sync_to_async(_plain_django_request_saves_its_old_copy)(engine, key, before)
            assert not await sync_to_async(_holders)(engine, key)
            reply = await _reply_or_error(comm, "set_to", {"value": 4})
            assert reply["type"] != "error", reply
            assert len(await sync_to_async(_holders)(engine, key)) == 1  # re-recorded
        finally:
            await comm.disconnect()
        assert await sync_to_async(_restored_count)(engine, key) == 4


@pytest.mark.asyncio
async def test_the_oldest_of_thirty_three_tabs_keeps_saving(claims, monkeypatch):
    import itertools

    ids = ("tab-%04d" % n for n in itertools.count())
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        victim = await _mount(DB, key)
        others = []
        try:
            with monkeypatch.context() as patch:
                patch.setattr(_automatic_state, "new_tab_id", lambda: next(ids))
                for _ in range(_automatic_state.MAX_HOLDERS):
                    tab = next(ids)

                    def page(tab=tab):
                        request = _request(DB, key)
                        assert FenceCounter().get(request).status_code == 200

                    await sync_to_async(page)()
                    other = await _mount_tab(DB, key, tab)
                    others.append(other)
            holders = await sync_to_async(_holders)(DB, key)
            assert len(holders) == _automatic_state.MAX_HOLDERS  # the victim's record went
            reply = await _reply_or_error(victim, "set_to", {"value": 6})
            assert reply["type"] != "error", reply
        finally:
            for comm in [victim, *others]:
                await comm.disconnect()
        assert await sync_to_async(_restored_count)(DB, key) == 6


async def _mount_tab(engine: str, key: str, tab: str) -> Any:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = await sync_to_async(import_module(engine).SessionStore)(key)
    comm.scope["user"] = AnonymousUser()
    assert (await comm.connect())[0]
    await comm.receive_json_from()
    await comm.send_json_to(
        {"type": "mount", "view": f"{MODULE}.FenceCounter", "url": sf.PATH, "tab": tab}
    )
    assert (await comm.receive_json_from())["type"] == "mount"
    return comm


def _age_every_record(key: str) -> None:
    """As if the tab had been idle past ``DJUST_SERVER_STATE_MAX_AGE``."""
    session = import_module(DB).SessionStore(key)
    for name in list(session.keys()):
        if name.startswith(_automatic_state._HOLDER_PREFIX):
            session[name] = {**session[name], "expires": 1}
    session.save()


@pytest.mark.asyncio
async def test_a_holder_idle_past_the_lifetime_keeps_saving(claims):
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm = await _mount(DB, key)
        try:
            await sync_to_async(_age_every_record)(key)
            reply = await _reply_or_error(comm, "set_to", {"value": 8})
            assert reply["type"] != "error", reply
        finally:
            await comm.disconnect()
        assert await sync_to_async(_restored_count)(DB, key) == 8


@pytest.mark.asyncio
async def test_every_save_refreshes_the_record_lifetime(claims):
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm = await _mount(DB, key)
        try:
            await sync_to_async(_age_every_record)(key)
            assert (await harness.event(comm, "set_to", {"value": 2}))["type"] != "error"
            (record,) = (await sync_to_async(_holders)(DB, key)).values()
            assert record["expires"] > 1
            # And the refreshed record keeps an older holder out again.
            assert holder_refuses(record, "anything-else")
        finally:
            await comm.disconnect()


@pytest.mark.asyncio
async def test_a_superseded_socket_stays_superseded_after_its_record_is_wiped(claims):
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm = await _mount(DB, key)
        try:
            assert (await sync_to_async(_post)(DB, key, "set_to", {"value": 7})).status_code == 200
            first = await _reply_or_error(comm, "set_to", {"value": 1})
            assert first.get("code") == "state_error", first
            await sync_to_async(_plain_django_request_saves_its_old_copy)(DB, key, {})
            again = await _reply_or_error(comm, "set_to", {"value": 2})
        finally:
            await comm.disconnect()
    assert again.get("code") == "state_error", again


@pytest.mark.asyncio
async def test_a_wipe_after_a_takeover_lets_the_older_holder_through(claims):
    """The residual the docs state: a takeover AND a session wipe inside one
    interval. Pinned as it is, so a change to it is a decision."""
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm = await _mount(DB, key)
        try:
            assert (await sync_to_async(_post)(DB, key, "set_to", {"value": 7})).status_code == 200
            await sync_to_async(_plain_django_request_saves_its_old_copy)(DB, key, {})
            reply = await _reply_or_error(comm, "set_to", {"value": 1})
        finally:
            await comm.disconnect()
    assert reply["type"] != "error", reply


# ---------------------------------------------------------------------------
# What the record costs (the second review, finding 8)
# ---------------------------------------------------------------------------


def _counting_saves(monkeypatch) -> dict:
    from django.contrib.sessions.backends.db import SessionStore

    count = {"n": 0}
    real = SessionStore.save

    def save(self, *args, **kwargs):
        count["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", save)
    return count


@pytest.mark.asyncio
@pytest.mark.parametrize("claiming", [True, False], ids=["claims", "today"])
async def test_session_saves_per_mount_and_fallback_request(claiming, monkeypatch):
    """With nothing stored a socket mount saves once; a fallback request that
    changes state saves once; claims or not. A restoring socket mount is the
    one place the record costs a write of its own."""
    monkeypatch.setattr(_save_policy, "holder_claims", lambda view: claiming)
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        await sync_to_async(_drop_tab_state)(DB, key)
        count = _counting_saves(monkeypatch)
        fresh = await _mount(DB, key)  # nothing stored: mount() and one save
        await fresh.disconnect()
        fresh_saves, count["n"] = count["n"], 0
        restoring = await _mount(DB, key)
        await restoring.disconnect()
        restoring_saves, count["n"] = count["n"], 0
        assert (await sync_to_async(_post)(DB, key, "set_to", {"value": 2})).status_code == 200
        post_saves = count["n"]
    assert fresh_saves == 1
    assert post_saves == 1
    assert restoring_saves == (1 if claiming else 0)
