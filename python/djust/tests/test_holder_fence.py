"""Save frequency, step 2: the holder fence.

Once a turn's save can come after the turn (a deferred save, a disconnect
flush), a connection the client has left can write AFTER another process
restored the tab and moved on, replacing newer state with older. The fence:

* the saved state carries a holder epoch;
* a restore that may be followed by such a write claims the tab (one write,
  the epoch one higher) — gated by ``_save_policy.holder_claims``, which is
  False in this release, so by default nothing claims and nothing changes;
* a save that names a lower epoch than the stored one is refused, with
  nothing of it written: in the session merge (under the row lock on ``db``
  and ``cached_db``), and before the write of a state store, which is
  written before the session's index entry.

Every test here is a real two-writer race: a late writer must not replace
newer state. Each runs on every session engine the suite can run, and the
view-level ones through the session, an external store and the sealed store.
Each has a sibling with claims off (today's behaviour), where the late write
does land: that is the gate-off of the claim, and the proof that by default
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

from djust import LiveView, _save_policy
from djust._session_merge import (
    TrackedSessionData,
    fence_refuses,
    save_merged,
    stored_epoch,
    track_session,
)
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
    """Restores claim the tab (what a deferring view will do)."""
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


def _write(session: Any, value: str, epoch: int, *, claim: bool = False) -> None:
    session[KEY] = {"epoch": epoch, "value": value}
    data = session._session_cache
    assert type(data) is TrackedSessionData
    data.fence(KEY, epoch, claim=claim)
    save_merged(session, session.session_key)


def _stored(engine: str, key: str) -> dict:
    return dict(import_module(engine).SessionStore(key).load())


@pytest.mark.parametrize("engine", ENGINES)
def test_a_late_writer_does_not_replace_what_a_newer_holder_wrote(engine):
    key = _session(engine).session_key
    holder_a = _session(engine, key)
    _write(holder_a, "a1", 0)  # A holds the tab at epoch 0
    holder_b = _session(engine, key)
    _write(holder_b, "b-restored", 1, claim=True)  # B restores it: claims epoch 1
    _write(holder_b, "b2", 1)  # and moves on
    holder_a["cart"] = "kept"  # A's own unrelated write rides along
    with pytest.raises(StateSuperseded):
        _write(holder_a, "a-late", 0)
    stored = _stored(engine, key)
    assert stored[KEY] == {"epoch": 1, "value": "b2"}
    assert stored["cart"] == "kept"  # the rest of the snapshot was written
    # A's live session took B's entry back, unrecorded: nothing re-writes A's.
    assert dict.__getitem__(holder_a._session_cache, KEY) == {"epoch": 1, "value": "b2"}
    holder_a["other"] = 1
    save_merged(holder_a, key)
    assert _stored(engine, key)[KEY] == {"epoch": 1, "value": "b2"}


@pytest.mark.parametrize("engine", ENGINES)
def test_an_equal_epoch_refuses_a_claim_not_a_save(engine):
    key = _session(engine).session_key
    first = _session(engine, key)
    _write(first, "first", 1, claim=True)
    second = _session(engine, key)
    with pytest.raises(StateSuperseded):
        _write(second, "second", 1, claim=True)  # two claims of one epoch: one wins
    _write(first, "first-again", 1)  # the holder itself writes at its own epoch
    assert _stored(engine, key)[KEY]["value"] == "first-again"


@pytest.mark.parametrize("engine", ENGINES)
def test_without_a_fence_the_late_write_lands_as_before(engine):
    """The gate-off: the same race with no fence registered overwrites."""
    key = _session(engine).session_key
    holder_a = _session(engine, key)
    holder_a[KEY] = {"epoch": 0, "value": "a1"}
    save_merged(holder_a, key)
    holder_b = _session(engine, key)
    _write(holder_b, "b2", 1, claim=True)
    holder_a[KEY] = {"epoch": 0, "value": "a-late"}
    save_merged(holder_a, key)  # no fence: today's merge
    assert _stored(engine, key)[KEY]["value"] == "a-late"


def test_an_entry_written_before_the_fence_fences_nothing():
    assert stored_epoch({"value": 1}) is None
    assert stored_epoch({"epoch": True}) is None  # a bool is not an epoch
    assert stored_epoch({"epoch": -1}) is None
    assert not fence_refuses({"value": 1}, 0, True)
    assert fence_refuses({"epoch": 2}, 1, False)
    assert not fence_refuses({"epoch": 1}, 1, False)
    assert fence_refuses({"epoch": 1}, 1, True)


# ---------------------------------------------------------------------------
# A view: a socket or request that restored the tab writes late
# ---------------------------------------------------------------------------

LATE: Dict[str, Any] = {}


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
        """Set a value, then let another request restore the tab and save
        before this request's own save runs (its handler is still running)."""
        self.count = 99
        LATE["other"]()

    def get_context_data(self, **kwargs):
        return {"count": self.count}


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


def _get(engine: str) -> str:
    request = _request(engine, None)
    assert FenceCounter().get(request).status_code == 200
    return str(request.session.session_key)


def _post(engine: str, key: str, event: str, params=None) -> Any:
    return FenceCounter().post(_request(engine, key, "POST", event, params))


def _restored_count(engine: str, key: str) -> int:
    """What the next request restores (``late`` is not a handler of a read)."""
    view = FenceCounter()
    view.post(_request(engine, key, "POST", "no_such_event"))
    return view.count


def _race(engine: str) -> tuple:
    """A restores the tab and runs ``late``; inside it B restores, sets 7 and saves."""
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


@pytest.mark.parametrize("store", STORES, ids=["session", "external", "sealed"])
@pytest.mark.parametrize("engine", ENGINES)
def test_a_late_request_save_is_refused_and_the_newer_state_stays(engine, store, settings, claims):
    settings.SESSION_ENGINE = engine
    settings.ROOT_URLCONF = MODULE
    settings.DJUST_STATE_STORE = store
    key, late, newer = _race(engine)
    assert newer.status_code == 200
    assert late.status_code == 503, late.content
    assert json.loads(late.content)["code"] == "state_error"
    assert _restored_count(engine, key) == 7


@pytest.mark.parametrize("store", STORES, ids=["session", "external", "sealed"])
@pytest.mark.parametrize("engine", ENGINES)
def test_without_claims_the_late_save_lands_as_it_does_today(engine, store, settings):
    """Default behaviour: nothing claims, the late write wins (unchanged)."""
    settings.SESSION_ENGINE = engine
    settings.ROOT_URLCONF = MODULE
    settings.DJUST_STATE_STORE = store
    key, late, newer = _race(engine)
    assert newer.status_code == late.status_code == 200
    assert _restored_count(engine, key) == 99


def test_an_external_store_is_not_written_by_a_superseded_holder(settings, claims):
    """The store is written before the index: the check must come first, or
    the newer holder's bytes are replaced and its index stamp then refuses
    them (its state would be lost, not just stale)."""
    settings.SESSION_ENGINE = DB
    settings.ROOT_URLCONF = MODULE
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _get(DB)
    assert _post(DB, key, "set_to", {"value": 5}).status_code == 200
    saves_before = {}

    def other() -> None:
        assert _post(DB, key, "set_to", {"value": 7}).status_code == 200
        saves_before["n"] = len(Memory.SAVES)

    LATE["other"] = other
    try:
        assert _post(DB, key, "late").status_code == 503
    finally:
        LATE.clear()
    assert len(Memory.SAVES) == saves_before["n"]  # the late holder wrote nothing
    assert _restored_count(DB, key) == 7


def test_a_page_load_starts_the_tab_at_epoch_zero_and_is_never_fenced(settings, claims):
    settings.ROOT_URLCONF = MODULE
    key = _get(DB)
    assert _post(DB, key, "inc").status_code == 200  # a restore: claims epoch 1
    assert stored_epoch(sf.only_envelope(key)) == 1
    _get_same_session = _request(DB, key)
    assert FenceCounter().get(_get_same_session).status_code == 200
    assert stored_epoch(sf.only_envelope(key)) == 0


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
    assert stored_epoch(sf.only_envelope(key)) == 0


# ---------------------------------------------------------------------------
# A socket that is no longer the holder: WebSocket and SSE alike
# ---------------------------------------------------------------------------


async def _reply_or_error(comm: Any, name: str, params: Any) -> dict:
    """The frame that answers one event: its reply, or the error frame a
    refused save sends instead (which carries no ``ref``)."""
    from djust.tests._ws_frames import receive_until

    await comm.send_json_to({"type": "event", "event": name, "params": params, "ref": 77})

    def done(frames: list) -> bool:
        return any(frame.get("type") == "error" or frame.get("ref") == 77 for frame in frames)

    frames = await receive_until(comm, done, what="the answer to %r" % name)
    return frames[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("claiming", [True, False], ids=["claims", "today"])
async def test_a_websocket_holder_superseded_by_a_fallback_request(monkeypatch, claiming):
    monkeypatch.setattr(_save_policy, "holder_claims", lambda view: claiming)
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm, _mount = await harness.mount_view(FenceCounter, key)  # A restores
        try:
            assert (await harness.event(comm, "set_to", {"value": 5}))["type"] != "error"
            # B: the same tab over the HTTP fallback (another process, say).
            newer = await sync_to_async(_post)(DB, key, "set_to", {"value": 7})
            assert newer.status_code == 200
            reply = await _reply_or_error(comm, "set_to", {"value": 1})  # A writes late
        finally:
            await comm.disconnect()
        restored = await sync_to_async(_restored_count)(DB, key)
    if claiming:
        assert reply["type"] == "error" and reply.get("code") == "state_error", reply
        assert restored == 7
    else:
        assert reply["type"] != "error", reply
        assert restored == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("claiming", [True, False], ids=["claims", "today"])
async def test_an_sse_holder_superseded_by_a_fallback_request(monkeypatch, claiming):
    from djust.sse import _sse_sessions

    monkeypatch.setattr(_save_policy, "holder_claims", lambda view: claiming)
    seen = {}

    async def b_then_a():
        newer = await sync_to_async(_post)(DB, key, "set_to", {"value": 7})
        seen["b"] = newer.status_code

    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        before = set(_sse_sessions)
        await sf.sse_run(FenceCounter, key, [("set_to", {"value": 5})], MODULE, keep=True)
        (sid,) = set(_sse_sessions) - before
        try:
            await b_then_a()
            await sf.sse_post(sid, key, "set_to", {"value": 1})
            runtime = _sse_sessions[sid].runtime
            view = runtime.view_instance
        finally:
            _sse_sessions.pop(sid, None)
        restored = await sync_to_async(_restored_count)(DB, key)
    assert seen["b"] == 200
    if claiming:
        assert view.__dict__.get("_djust_auto_superseded") is True
        assert restored == 7
    else:
        assert not view.__dict__.get("_djust_auto_superseded")
        assert restored == 1


@pytest.mark.asyncio
async def test_a_superseded_socket_is_refused_even_by_a_turn_that_changes_nothing(claims):
    """Its view no longer matches the store (the newer holder's state), so no
    success frame may vouch for it, written or not."""
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        key = await sync_to_async(_get)(DB)
        comm, _mount = await harness.mount_view(FenceCounter, key)
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
