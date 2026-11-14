"""A pluggable store for saved view state (#4132).

``DJUST_STATE_STORE`` names where the state a view keeps between requests is
held. djust keeps the envelope and every check on it; a store only moves bytes
under a key djust builds, and the session keeps an index entry with no values.

What is proved here, on the page GET, the page-POST fallback, a socket and SSE:

* with a store, no view content rests in the session, and the state survives;
* with no setting, the session holds exactly what it held before;
* a store that hands back bytes written for another session, user, tenant,
  route or tab, old bytes or garbage yields a fresh ``mount()``;
* the sealed store refuses altered text and a wrong key, and rotates keys;
* a store that is down serves the response (and the strict setting refuses);
* store calls run inside ``sync_turn_wrapper`` as a ``"state_store"`` step.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import RequestFactory
from django.urls import path

from djust import LiveView, _automatic_state, event_handler, state_store
from djust.decorators import state
from djust.state_store import StateKey, ViewStateStore
from djust.tests import _per_turn_harness as harness

MODULE = __name__
PATH = harness.PATH
MARKERS = ("STORE_PUBLIC", "STORE_PRIVATE", "STORE_HANDLER")
MOUNTS: List[str] = []
EXTERNAL = MODULE + ".MemoryStore"
SEALED = "djust.state_store.SealedSessionViewStateStore"
KEY_1 = state_store.generate_key()
KEY_2 = state_store.generate_key()


class Page(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ probe }}</pre></div>"
    kept = state(0, persist="server")

    def mount(self, request, **kwargs):
        MOUNTS.append(type(self).__name__)
        self.count = 0
        self.note = "STORE_PUBLIC"
        self._hidden = "STORE_PRIVATE"

    def get_context_data(self, **kwargs):
        return {"probe": json.dumps({"count": self.count, "note": self.note, "kept": self.kept})}

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self.kept += 1
        self.note = "STORE_HANDLER"


class Other(Page):
    kept = state(0, persist="server")


urlpatterns = [
    path("per-turn/", Page.as_view()),
    path("other-route/", Page.as_view()),
]


class MemoryStore(ViewStateStore):
    """A store outside the session. ``ANSWER`` overrides what ``load`` returns
    (a store that hands back the wrong bytes); ``DOWN`` makes calls fail."""

    DATA: Dict[str, bytes] = {}
    CALLS: List[tuple] = []
    STEPS: List[Optional[str]] = []
    ANSWER: Any = None
    DOWN: set = set()

    @classmethod
    def reset(cls) -> None:
        cls.DATA.clear()
        cls.CALLS.clear()
        cls.STEPS.clear()
        cls.ANSWER = None
        cls.DOWN = set()

    def _called(self, name: str, key: Any) -> None:
        from djust.sync_turn import _active_step

        type(self).CALLS.append((name, key))
        type(self).STEPS.append(_active_step.get())
        if name in type(self).DOWN:
            raise ConnectionError("the store is down: STORE_SECRET_IN_ERROR")

    def load(self, key: StateKey) -> Optional[bytes]:
        self._called("load", key)
        if type(self).ANSWER is not None:
            return type(self).ANSWER
        return type(self).DATA.get(key.id)

    def save(self, key: StateKey, payload: bytes, *, expires: int) -> None:
        self._called("save", key)
        assert type(payload) is bytes and type(expires) is int
        type(self).DATA[key.id] = payload

    def delete(self, keys) -> None:
        keys = list(keys)
        self._called("delete", keys)
        for key in keys:
            type(self).DATA.pop(key.id, None)

    def stats(self) -> dict:
        self._called("stats", None)
        return {"states": len(type(self).DATA)}

    def clear(self, *, expired_only: bool) -> int:
        self._called("clear", None)
        if expired_only:
            return 0
        count = len(type(self).DATA)
        type(self).DATA.clear()
        return count


class SecondMemoryStore(MemoryStore):
    """Another store class over the very same bytes."""


class BareStore(ViewStateStore):
    """A store with no ``stats()`` and no ``clear()``."""

    def load(self, key):
        return None

    def save(self, key, payload, *, expires):
        return None

    def delete(self, keys):
        return None


@pytest.fixture(autouse=True)
def _each(monkeypatch, settings):
    MOUNTS.clear()
    MemoryStore.reset()
    _automatic_state._NOTICED.clear()
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)
    settings.ROOT_URLCONF = MODULE
    settings.DJUST_STATE_STORE_KEYS = [KEY_1]


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, pk: int) -> None:
        self.pk = pk
        self.id = pk


def _request(
    method: str,
    key: Optional[str],
    *,
    url: str = PATH,
    tab: str = harness.TAB,
    user: Any = None,
    tenant: Any = None,
):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url)
    else:
        request = factory.post(
            url,
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="bump",
            HTTP_X_DJUST_TAB=tab,
        )
    request.user = user if user is not None else AnonymousUser()
    request.tenant = tenant
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    return request


def _page(view_class: type = Page, **who: Any) -> str:
    first = _request("GET", who.pop("key", None), **who)
    response = view_class().get(first)
    assert response.status_code == 200
    return str(first.session.session_key)


def _post(key: str, view_class: type = Page, **who: Any):
    view = view_class()
    response = view.post(_request("POST", key, **who))
    return response, view


def _bump(key: str, **who: Any) -> int:
    response, view = _post(key, **who)
    assert response.status_code == 200, response.content
    return view.count


def _entries(key: str) -> Dict[str, Any]:
    return {
        name: value for name, value in SessionStore(key).items() if name.startswith("_djust_auto_")
    }


def _all_sessions_text() -> str:
    rows = [dict(SessionStore(row.session_key).items()) for row in Session.objects.all()]
    assert rows, "no session was stored: the sweep would prove nothing"
    return json.dumps(rows, default=str)


def _probe_of(markup: str) -> Dict[str, Any]:
    import html
    import re

    found = re.search(r"<pre[^>]*>(.*?)</pre>", markup, re.S)
    assert found, markup[:400]
    return json.loads(html.unescape(found.group(1)))


async def _socket_then_reconnect(events: int = 2) -> Dict[str, Any]:
    key = await sync_to_async(_page)()
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        comm, _mount = await harness.mount_view(Page, key)
        for _ in range(events):
            assert (await harness.event(comm, "bump"))["type"] != "error"
        await comm.disconnect()
        again, mount = await harness.mount_view(Page, key)
        await again.disconnect()
    return _probe_of(mount["html"])


async def _sse(key: str, events: int = 2) -> None:
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    def request_for(method: str, target: str, body: Any) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        made.user = AnonymousUser()
        made.tenant = None
        return made

    sid = str(uuid.uuid4())
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        first = request_for(
            "GET",
            f"/djust/sse/{sid}/",
            {"view": f"{MODULE}.Page", "_djust_url": PATH, "_djust_tab": harness.TAB},
        )
        response = await DjustSSEStreamView().get(first, session_id=sid)
        assert response.status_code == 200
        try:
            for _ in range(events):
                posted = request_for(
                    "POST",
                    f"/djust/sse/{sid}/message/",
                    {"type": "event", "event": "bump", "params": {}},
                )
                answer = await DjustSSEMessageView().post(posted, session_id=sid)
                assert answer.status_code in (200, 202, 204), answer.content
        finally:
            _sse_sessions.pop(sid, None)


STORES = [pytest.param(EXTERNAL, id="external"), pytest.param(SEALED, id="sealed")]


# ---------------------------------------------------------------------------
# With a store: the state survives and no content rests in the session
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("store", STORES)
def test_the_fallback_keeps_state_and_the_session_holds_no_content(store, settings):
    settings.DJUST_STATE_STORE = store
    key = _page()
    assert [_bump(key), _bump(key), _bump(key)] == [1, 2, 3]
    assert MOUNTS == ["Page"]  # the view is rebuilt from its saved state
    text = json.dumps(_entries(key))
    assert not [marker for marker in MARKERS if marker in text], text
    (entry,) = _entries(key).values()
    wanted = set(_automatic_state._INDEX)
    if store == SEALED:
        wanted |= {"sealed"}
        assert not MemoryStore.CALLS
    else:
        (payload,) = MemoryStore.DATA.values()
        assert b"STORE_HANDLER" in payload and b"STORE_PRIVATE" in payload
        # The size the per-view limit counts: the encoded state.
        assert entry["bytes"] == json.loads(payload)["state"]["bytes"]
    assert set(entry) == wanted
    assert "values" not in entry and "private" not in entry


@pytest.mark.django_db
@pytest.mark.parametrize("store", STORES)
def test_declared_server_fields_stay_in_their_own_session_envelope(store, settings):
    """Decision 2: ``persist="server"`` fields do not go through the store."""
    settings.DJUST_STATE_STORE = store
    key = _page()
    _bump(key)
    declared = [
        value for name, value in SessionStore(key).items() if name.startswith("_djust_explicit_")
    ]
    assert [item["state"]["values"]["kept"] for item in declared] == [1]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store", STORES)
async def test_a_socket_reconnect_restores_from_the_store(store, settings):
    settings.DJUST_STATE_STORE = store
    probe = await _socket_then_reconnect()
    assert probe == {"count": 2, "note": "STORE_HANDLER", "kept": 2}
    text = await sync_to_async(_all_sessions_text)()
    assert "STORE_HANDLER" not in text and "STORE_PRIVATE" not in text


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store", STORES)
async def test_sse_events_save_through_the_store(store, settings):
    settings.DJUST_STATE_STORE = store
    key = await sync_to_async(_page)()
    await _sse(key)
    text = await sync_to_async(_all_sessions_text)()
    assert "STORE_HANDLER" not in text and "STORE_PRIVATE" not in text
    # The next fallback request continues from what the SSE events saved.
    assert await sync_to_async(_bump)(key) == 3


@pytest.mark.django_db
def test_no_setting_is_todays_layout_and_calls_no_store(settings):
    """The default: the envelope in the session, exactly its keys, no store."""
    assert state_store.configured_store() is None
    key = _page()
    assert _bump(key) == 1
    (entry,) = _entries(key).values()
    assert set(entry) == set(_automatic_state._ENVELOPE)
    assert entry["values"]["note"] == "STORE_HANDLER"
    assert not MemoryStore.CALLS
    plain = {name: value for name, value in entry.items() if name not in ("created", "expires")}

    # Naming the default store is the same as naming nothing.
    settings.DJUST_STATE_STORE = "djust.state_store.SessionViewStateStore"
    assert state_store.configured_store() is None
    other = _page()
    assert _bump(other) == 1
    (named,) = _entries(other).values()
    assert {
        name: value
        for name, value in named.items()
        if name not in ("created", "expires", "binding")
    } == {name: value for name, value in plain.items() if name != "binding"}


@pytest.mark.django_db
def test_with_saving_off_no_store_is_called(settings):
    settings.DJUST_STATE_STORE = EXTERNAL
    settings.DJUST_AUTOMATIC_STATE = False
    key = _page()
    assert [_bump(key), _bump(key)] == [1, 1]
    assert not MemoryStore.CALLS and not _entries(key)


@pytest.mark.django_db
def test_switching_stores_reads_as_nothing_saved(settings):
    """An entry another store (or the session layout) wrote is never taken for
    this store's: one ``mount()`` per open page after a switch."""
    key = _page()
    assert _bump(key) == 1  # saved in the session, in the clear
    settings.DJUST_STATE_STORE = EXTERNAL
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]
    # Another store outside the session, even one that holds the same bytes:
    # the index entry names the store it was written for.
    settings.DJUST_STATE_STORE = MODULE + ".SecondMemoryStore"
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]
    settings.DJUST_STATE_STORE = SEALED
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]
    del settings.DJUST_STATE_STORE
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]


# ---------------------------------------------------------------------------
# A store cannot cross sessions, users, tenants, routes or tabs
# ---------------------------------------------------------------------------


def _tenant(name: str) -> Any:
    from djust.tenants.resolvers import TenantInfo

    return TenantInfo(name)


CROSSINGS = [
    pytest.param({}, {"key": None}, id="another-session"),
    pytest.param({"user": _User(1)}, {"user": _User(2)}, id="another-user"),
    pytest.param({"tenant": "acme"}, {"tenant": "globex"}, id="another-tenant"),
    pytest.param({}, {"url": "/other-route/"}, id="another-route"),
    pytest.param({}, {"tab": "another-tab-0002"}, id="another-tab"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("victim, caller", CROSSINGS)
def test_a_store_that_returns_another_callers_bytes_yields_a_fresh_mount(
    victim, caller, settings, monkeypatch
):
    settings.DJUST_STATE_STORE = EXTERNAL
    victim = dict(victim)
    caller = {**victim, **caller}
    for who in (victim, caller):
        if "tenant" in who:
            who["tenant"] = _tenant(who["tenant"])
    # The victim: three events, so their state says count == 3.
    key = _page(**victim)
    for _ in range(3):
        _bump(key, **victim)
    (stolen,) = MemoryStore.DATA.values()
    assert b'"count":3' in stolen
    # The caller has a state of their own (count == 1), in the same store.
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: caller.get("tab", harness.TAB))
    if caller.get("key", key) is None:
        caller["key"] = _page(**{k: v for k, v in caller.items() if k != "key"})
    else:
        caller["key"] = _page(**{**caller, "key": key})
    assert _bump(**caller) == 1
    assert len(MemoryStore.DATA) == 2
    # Now the store answers every load with the victim's bytes, and the
    # caller's index entry carries the stamp of the victim's write (an index
    # entry copied along with them): only what the bytes themselves say they
    # were written for stands between the caller and the victim's state.
    MemoryStore.ANSWER = stolen
    for name, entry in _entries(caller["key"]).items():
        _rewrite(caller["key"], name, {**entry, "stamp": json.loads(stolen)["stamp"]})
    MOUNTS.clear()
    loads = len([call for call in MemoryStore.CALLS if call[0] == "load"])
    response, view = _post(**caller)
    assert response.status_code == 200
    assert len([call for call in MemoryStore.CALLS if call[0] == "load"]) == loads + 1
    # The caller got a fresh mount(), then their one event: never count == 4.
    assert MOUNTS == ["Page"]
    assert view.count == 1


@pytest.mark.django_db
def test_a_store_that_returns_an_older_write_of_the_same_key_is_refused(settings):
    """The index entry's stamp names the one write the session vouches for."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    _bump(key)
    (old,) = MemoryStore.DATA.values()
    assert [_bump(key), _bump(key)] == [2, 3]
    MemoryStore.ANSWER = old
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]


@pytest.mark.django_db
@pytest.mark.parametrize("answer", [b"", b"not json", b"[1,2]", b'{"for":"x"}', "text", 7])
def test_garbage_from_a_store_yields_a_fresh_mount(answer, settings, caplog):
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    MemoryStore.ANSWER = answer
    MOUNTS.clear()
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert _bump(key) == 1
    assert MOUNTS == ["Page"]
    if isinstance(answer, bytes):
        assert "was refused" in caplog.text
    else:
        # Not bytes at all: the message names the type the store returned.
        assert "returned a %s" % type(answer).__name__ in caplog.text
        assert "must return bytes" in caplog.text


@pytest.mark.django_db
def test_a_deeply_nested_document_from_a_store_is_refused_not_an_error(settings):
    """``json.loads`` raises RecursionError, not ValueError, for this one."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    MemoryStore.ANSWER = b"[" * 200000
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]


@pytest.mark.django_db
@pytest.mark.parametrize("kind", [bytearray, memoryview])
def test_bytes_like_values_from_a_store_are_accepted(kind, settings):
    """A driver that hands back a ``memoryview`` (psycopg's ``bytea``) or a
    ``bytearray`` is a working store."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    (real,) = MemoryStore.DATA.values()
    MemoryStore.ANSWER = kind(real)
    MOUNTS.clear()
    assert _bump(key) == 2 and MOUNTS == []


@pytest.mark.django_db
def test_an_expired_index_entry_is_not_loaded_from_the_store(settings):
    """The one thing the index entry decides alone: no store call for a state
    that has expired."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    name, entry = _sealed_entry(key)
    _rewrite(key, name, {**entry, "expires": 1})
    MemoryStore.CALLS.clear()
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]
    assert "load" not in [call[0] for call in MemoryStore.CALLS]


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["stamp", "state"])
def test_a_payload_that_names_this_key_but_lacks_a_part_is_refused_not_an_error(missing, settings):
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    (real,) = MemoryStore.DATA.values()
    partial = json.loads(real)
    del partial[missing]
    MemoryStore.ANSWER = json.dumps(partial).encode()
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]


@pytest.mark.django_db
def test_an_index_entry_alone_restores_nothing(settings):
    """The session vouches for which state exists; the store for its bytes."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    MemoryStore.DATA.clear()
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]
    # ... and bytes alone, with the index entry gone, are not read at all.
    assert _bump(key) == 2
    session = SessionStore(key)
    for name in list(_entries(key)):
        del session[name]
    session.save()
    MemoryStore.CALLS.clear()
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]
    assert "load" not in [call[0] for call in MemoryStore.CALLS]


# ---------------------------------------------------------------------------
# The sealed store
# ---------------------------------------------------------------------------


def _sealed_entry(key: str):
    ((name, entry),) = _entries(key).items()
    return name, entry


def _rewrite(key: str, name: str, entry: Any) -> None:
    session = SessionStore(key)
    session[name] = entry
    session.save()


@pytest.mark.django_db
def test_sealed_text_that_was_altered_is_refused(settings):
    settings.DJUST_STATE_STORE = SEALED
    key = _page()
    assert [_bump(key), _bump(key)] == [1, 2]
    name, entry = _sealed_entry(key)
    sealed = entry["sealed"]
    swapped = sealed[:-6] + ("A" if sealed[-6] != "A" else "B") + sealed[-5:]
    _rewrite(key, name, {**entry, "sealed": swapped})
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]


@pytest.mark.django_db
def test_sealed_text_under_the_wrong_key_is_refused(settings, caplog):
    settings.DJUST_STATE_STORE = SEALED
    key = _page()
    assert [_bump(key), _bump(key)] == [1, 2]
    settings.DJUST_STATE_STORE_KEYS = [KEY_2]
    MOUNTS.clear()
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert _bump(key) == 1
    assert MOUNTS == ["Page"]
    assert "was refused" in caplog.text
    assert not [marker for marker in MARKERS if marker in caplog.text]


@pytest.mark.django_db
def test_sealed_text_moved_to_another_tab_does_not_open(settings, monkeypatch):
    """The state's key is the associated data: one tab's sealed state does not
    open as another's, even inside one session."""
    settings.DJUST_STATE_STORE = SEALED
    key = _page()
    for _ in range(3):
        _bump(key)
    _name, first = _sealed_entry(key)
    other_tab = "another-tab-0002"
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: other_tab)
    _page(key=key)
    assert _bump(key, tab=other_tab) == 1
    (second_name,) = [name for name in _entries(key) if name != _name]
    second = _entries(key)[second_name]
    # The first tab's sealed text and stamp, under the second tab's entry.
    _rewrite(key, second_name, {**second, "sealed": first["sealed"], "stamp": first["stamp"]})
    MOUNTS.clear()
    response, view = _post(key, tab=other_tab)
    assert MOUNTS == ["Page"] and view.count == 1


def test_the_sealed_store_opens_only_for_the_key_it_sealed_for(settings):
    store = state_store.SealedSessionViewStateStore()
    mine = StateKey("s", "b", "p", "tab-one-0001", "anonymous", "none")
    text = store.seal(mine, b"payload")
    assert store.open(mine, text) == b"payload"
    assert "payload" not in text
    assert store.seal(mine, b"payload") != text  # a random nonce per write
    for other in (
        StateKey("s", "b2", "p", "tab-one-0001", "anonymous", "none"),
        StateKey("s", "b", "p2", "tab-one-0001", "anonymous", "none"),
        StateKey("s", "b", "p", "tab-two-0002", "anonymous", "none"),
    ):
        assert store.open(other, text) is None
    for garbage in ("", "s1.", "s1.!!!!", "s2." + text[3:], text[:-4], None, 5):
        assert store.open(mine, garbage) is None


@pytest.mark.django_db
def test_key_rotation_the_first_key_seals_and_every_key_opens(settings):
    settings.DJUST_STATE_STORE = SEALED
    settings.DJUST_STATE_STORE_KEYS = [KEY_1]
    key = _page()
    assert _bump(key) == 1
    # The new key goes first: what the old key sealed still opens.
    settings.DJUST_STATE_STORE_KEYS = [KEY_2, KEY_1]
    MOUNTS.clear()
    assert _bump(key) == 2 and MOUNTS == []
    # ... and that save was sealed with the new key: the old one can go.
    settings.DJUST_STATE_STORE_KEYS = [KEY_2]
    assert _bump(key) == 3 and MOUNTS == []
    # The old key alone no longer opens it.
    settings.DJUST_STATE_STORE_KEYS = [KEY_1]
    assert _bump(key) == 1 and MOUNTS == ["Page"]


def test_keys_may_be_bytes_text_or_a_callable_and_never_secret_key(settings):
    import base64

    raw = base64.urlsafe_b64decode(KEY_1)
    for configured in ([raw], [KEY_1], KEY_1, lambda: [raw], (KEY_1, KEY_2)):
        settings.DJUST_STATE_STORE_KEYS = configured
        assert state_store.sealing_keys()[0] == raw
    for wrong in (None, [], [b"short"], ["not base64 !!"], [5], 5):
        settings.DJUST_STATE_STORE_KEYS = wrong
        with pytest.raises(state_store.StateStoreConfigurationError) as raised:
            state_store.sealing_keys()
        assert "short" not in str(raised.value) and "not base64 !!" not in str(raised.value)
    del settings.DJUST_STATE_STORE_KEYS
    settings.SECRET_KEY = "x" * 50  # noqa: S105 - a test value
    with pytest.raises(state_store.StateStoreConfigurationError, match="no fallback to SECRET_KEY"):
        state_store.sealing_keys()


@pytest.mark.django_db
@pytest.mark.parametrize("broken", ["no-keys", "no-cryptography", "bad-path"])
def test_an_unusable_store_serves_the_page_and_writes_nothing_in_the_clear(
    broken, settings, monkeypatch, caplog
):
    """A store that cannot be used never degrades to plaintext in the session."""
    settings.DJUST_STATE_STORE = SEALED
    if broken == "no-keys":
        del settings.DJUST_STATE_STORE_KEYS
    elif broken == "no-cryptography":

        def missing():
            raise state_store.StateStoreConfigurationError(state_store._INSTALL)

        monkeypatch.setattr(state_store, "_aesgcm", missing)
    else:
        settings.DJUST_STATE_STORE = "no.such.module.Store"
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        key = _page()
        assert [_bump(key), _bump(key)] == [1, 1]
    assert not _entries(key)
    assert "STORE_PUBLIC" not in _all_sessions_text()
    assert "could not save" in caplog.text


# ---------------------------------------------------------------------------
# A store that is down
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_store_down_at_the_page_load_still_serves_the_page(settings, caplog):
    settings.DJUST_STATE_STORE = EXTERNAL
    MemoryStore.DOWN = {"save"}
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        key = _page()
    assert not _entries(key)
    assert [r.levelname for r in caplog.records if "could not save" in r.message] == ["WARNING"]
    assert "STORE_SECRET_IN_ERROR" not in caplog.text
    MemoryStore.DOWN = set()
    assert [_bump(key), _bump(key)] == [1, 2]


@pytest.mark.django_db
def test_a_store_down_while_an_event_saves_serves_and_starts_fresh_next_time(settings, caplog):
    """Decision 3: serve the response, log an error, start fresh next time."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert [_bump(key), _bump(key)] == [1, 2]
    MemoryStore.DOWN = {"save"}
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        response, view = _post(key)
    assert response.status_code == 200 and view.count == 3
    errors = [r for r in caplog.records if "could not save" in r.message]
    assert [r.levelname for r in errors] == ["ERROR"]
    assert "STORE_SECRET_IN_ERROR" not in caplog.text  # an explicit view: no exception text
    assert not [marker for marker in MARKERS if marker in caplog.text]
    # Nothing vouches for the older state the store still holds (count == 2).
    assert not _entries(key)
    MemoryStore.DOWN = set()
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]
    # The declared field's own envelope was still written by the failed turn.
    assert view.kept == 3


@pytest.mark.django_db
def test_strict_refuses_the_event_and_keeps_the_last_saved_state(settings, caplog):
    settings.DJUST_STATE_STORE = EXTERNAL
    settings.DJUST_STATE_STORE_STRICT = True
    key = _page()
    assert [_bump(key), _bump(key)] == [1, 2]
    before = _entries(key)
    declared_before = SessionStore(key).items()
    MemoryStore.DOWN = {"save"}
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        response, _view = _post(key)
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body == {"error": "State unavailable. Please reload the page.", "code": "state_error"}
    assert "STORE_SECRET_IN_ERROR" not in caplog.text
    # Nothing was written: not the index entry, not the declared envelope.
    assert _entries(key) == before
    assert dict(SessionStore(key).items()) == dict(declared_before)
    MemoryStore.DOWN = set()
    MOUNTS.clear()
    assert _bump(key) == 3 and MOUNTS == []


@pytest.mark.django_db
def test_strict_still_serves_a_page_load(settings):
    settings.DJUST_STATE_STORE = EXTERNAL
    settings.DJUST_STATE_STORE_STRICT = True
    MemoryStore.DOWN = {"save"}
    key = _page()
    assert not _entries(key)


@pytest.mark.django_db
def test_a_store_down_on_load_serves_and_mounts(settings, caplog):
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert [_bump(key), _bump(key)] == [1, 2]
    MemoryStore.DOWN = {"load"}
    MOUNTS.clear()
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert [_bump(key), _bump(key)] == [1, 1]  # served; nothing can be read back
    assert MOUNTS == ["Page", "Page"]
    assert "could not load" in caplog.text and "ConnectionError" in caplog.text
    assert "STORE_SECRET_IN_ERROR" not in caplog.text
    # Only this store failure is reported, not a second "evicted" guess.
    assert "evicted" not in caplog.text
    MemoryStore.DOWN = set()
    MOUNTS.clear()
    assert _bump(key) == 2 and MOUNTS == []  # what the last request saved


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("strict", [False, True])
async def test_a_store_down_during_a_socket_event(strict, settings):
    settings.DJUST_STATE_STORE = EXTERNAL
    settings.DJUST_STATE_STORE_STRICT = strict
    key = await sync_to_async(_page)()
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        comm, _mount = await harness.mount_view(Page, key)
        assert (await harness.event(comm, "bump"))["type"] != "error"
        MemoryStore.DOWN = {"save"}
        if strict:
            from djust.tests._ws_frames import has_frame, receive_until

            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 9})
            frame = (await receive_until(comm, has_frame("error"), what="the refusal"))[-1]
        else:
            frame = await harness.event(comm, "bump")
        await comm.disconnect()
    if strict:
        assert frame["type"] == "error" and frame.get("code") == "state_error", frame
        assert len(await sync_to_async(_entries)(key)) == 1  # the last saved state stays
    else:
        assert frame["type"] != "error", frame
        assert not await sync_to_async(_entries)(key)


@pytest.mark.django_db
def test_a_store_that_fails_to_delete_does_not_stop_the_save(settings, monkeypatch):
    settings.DJUST_STATE_STORE = EXTERNAL
    monkeypatch.setattr(_automatic_state, "MAX_TABS", 1)
    key = _page()
    assert _bump(key) == 1
    MemoryStore.DOWN = {"delete"}
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: "another-tab-0002")
    _page(key=key)  # evicts the first tab: the delete fails, the save goes on
    assert _bump(key, tab="another-tab-0002") == 1
    assert len(_entries(key)) == 1


# ---------------------------------------------------------------------------
# Budgets work from the index; evicted states leave the store
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_tab_cap_evicts_from_the_index_and_deletes_from_the_store(settings, monkeypatch):
    settings.DJUST_STATE_STORE = EXTERNAL
    key = None
    tabs = ["tab-number-%04d" % number for number in range(_automatic_state.MAX_TABS + 2)]
    clock = [1_700_000_000]
    monkeypatch.setattr(_automatic_state.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        "djust._exposure_sessions.written_in_the_future", lambda created, now, what: False
    )
    for tab in tabs:
        clock[0] += 1
        monkeypatch.setattr(_automatic_state, "new_tab_id", lambda tab=tab: tab)
        key = _page(key=key)
    assert len(_entries(key)) == _automatic_state.MAX_TABS
    assert len(MemoryStore.DATA) == _automatic_state.MAX_TABS
    deleted = [k.tab for call in MemoryStore.CALLS if call[0] == "delete" for k in call[1]]
    assert deleted == tabs[:2]  # the oldest two, never listed by the store
    kept = {entry["tab"] for entry in _entries(key).values()}
    assert kept == set(tabs[2:])


# ---------------------------------------------------------------------------
# The per-turn wrapper
# ---------------------------------------------------------------------------

WRAPPED: List[str] = []


def recording_wrapper(view, step, turn):
    from contextlib import contextmanager

    @contextmanager
    def scope():
        WRAPPED.append(step)
        yield

    return scope()


@pytest.mark.django_db
def test_store_calls_run_inside_the_sync_turn_wrapper(settings, monkeypatch):
    from djust.config import config as djust_config
    from djust.sync_turn import SYNC_STEPS

    assert "state_store" in SYNC_STEPS
    settings.DJUST_STATE_STORE = EXTERNAL
    settings.LIVEVIEW_CONFIG = {
        **getattr(settings, "LIVEVIEW_CONFIG", {}),
        "sync_turn_wrapper": MODULE + ".recording_wrapper",
    }
    djust_config.reset()
    monkeypatch.setattr(_automatic_state, "MAX_TABS", 1)
    WRAPPED.clear()
    try:
        key = _page()
        assert _bump(key) == 1
        monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: "another-tab-0002")
        _page(key=key)  # evicts the first tab's state: a delete
    finally:
        djust_config.reset()
    names = [call[0] for call in MemoryStore.CALLS]
    assert {"save", "load", "delete"} <= set(names), names
    # Every call into the store ran with the wrapper entered for this step.
    assert MemoryStore.STEPS == ["state_store"] * len(names), MemoryStore.STEPS
    assert WRAPPED.count("state_store") == len(names)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_store_calls_on_a_socket_run_inside_the_wrapper_too(settings):
    settings.DJUST_STATE_STORE = EXTERNAL
    WRAPPED.clear()
    key = await sync_to_async(_page)()
    liveview = {"sync_turn_wrapper": MODULE + ".recording_wrapper"}
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        from django.conf import settings as live

        from djust.config import config as djust_config

        live.LIVEVIEW_CONFIG = {**live.LIVEVIEW_CONFIG, **liveview}
        djust_config.reset()
        MemoryStore.CALLS.clear()
        MemoryStore.STEPS.clear()
        comm, _mount = await harness.mount_view(Page, key)
        assert (await harness.event(comm, "bump"))["type"] != "error"
        await comm.disconnect()
    names = [call[0] for call in MemoryStore.CALLS]
    assert "load" in names and "save" in names, names
    assert MemoryStore.STEPS == ["state_store"] * len(names), MemoryStore.STEPS


# ---------------------------------------------------------------------------
# ``djust clear`` and ``djust stats``
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_clearing_a_session_deletes_its_states_from_the_store(settings):
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    other = _page()
    assert len(MemoryStore.DATA) == 2
    stats = _automatic_state.saved_state_stats(SessionStore(key))
    held = json.loads(MemoryStore.DATA[_entry_key(key).id])["state"]["bytes"]
    assert stats["entries"] == 2 and stats["store_bytes"] == held
    removed = _automatic_state.clear_saved_state(SessionStore(key))
    assert removed == 2  # the index entry and the declared envelope
    assert list(MemoryStore.DATA) == [_entry_key(other).id]


def _entry_key(key: str) -> StateKey:
    (entry,) = _entries(key).values()
    return StateKey(
        key, entry["binding"], entry["page"], entry["tab"], entry["user"], entry["tenant"]
    )


@pytest.mark.django_db
def test_clearing_expired_states_deletes_them_from_the_store(settings):
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    name, entry = _sealed_entry(key)
    assert _automatic_state.clear_expired_saved_state(SessionStore(key)) == 0
    assert len(MemoryStore.DATA) == 1
    _rewrite(key, name, {**entry, "expires": 1})
    assert _automatic_state.clear_expired_saved_state(SessionStore(key)) == 1
    assert not MemoryStore.DATA


@pytest.mark.django_db
def test_the_cli_reports_and_clears_the_store(settings, capsys):
    from djust.cli import cmd_clear, cmd_stats

    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    MemoryStore.DATA["orphan"] = b"x"  # bytes no session points at
    cmd_stats(argparse.Namespace())
    out = capsys.readouterr().out
    assert "Store: %s" % EXTERNAL in out
    assert "states: 2" in out and "State held for the sessions above" in out
    cmd_clear(argparse.Namespace(all=True, force=True))
    out = capsys.readouterr().out
    assert "Removed 1 view state(s) from the state store" in out, out
    assert not MemoryStore.DATA and not _entries(key)


@pytest.mark.django_db
def test_the_cli_says_when_a_store_cannot_list_or_clear_itself(settings, capsys):
    from djust.cli import cmd_clear, cmd_stats

    settings.DJUST_STATE_STORE = MODULE + ".BareStore"
    cmd_stats(argparse.Namespace())
    assert "Not listed" in capsys.readouterr().out
    cmd_clear(argparse.Namespace(all=True, force=True))
    assert "cannot clear itself" in capsys.readouterr().out


@pytest.mark.django_db
def test_the_cli_with_the_sealed_store_and_with_none(settings, capsys):
    from djust.cli import cmd_stats

    cmd_stats(argparse.Namespace())
    assert "djust State Store" not in capsys.readouterr().out
    settings.DJUST_STATE_STORE = SEALED
    cmd_stats(argparse.Namespace())
    assert "rest in the sessions above" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# System checks
# ---------------------------------------------------------------------------


def _check_ids() -> List[str]:
    from djust.checks.configuration import _check_state_store

    found: list = []
    _check_state_store(found)
    return [message.id for message in found]


def test_checks_are_silent_without_a_store_and_with_a_usable_one(settings):
    assert _check_ids() == []
    for usable in (EXTERNAL, SEALED, "djust.state_store.SessionViewStateStore"):
        settings.DJUST_STATE_STORE = usable
        assert _check_ids() == []


@pytest.mark.parametrize("wrong", ["no.such.module.Store", "json.dumps", "json.JSONDecoder", 5])
def test_c036_reports_a_store_that_cannot_be_used(wrong, settings):
    settings.DJUST_STATE_STORE = wrong
    assert _check_ids() == ["djust.C036"]


def test_c037_reports_the_sealed_store_without_keys_or_cryptography(settings, monkeypatch):
    settings.DJUST_STATE_STORE = SEALED
    settings.DJUST_STATE_STORE_KEYS = []
    assert _check_ids() == ["djust.C037"]
    settings.DJUST_STATE_STORE_KEYS = ["tooshort"]
    assert _check_ids() == ["djust.C037"]
    settings.DJUST_STATE_STORE_KEYS = [KEY_1]
    assert _check_ids() == []

    def missing():
        raise state_store.StateStoreConfigurationError(state_store._INSTALL)

    monkeypatch.setattr(state_store, "_aesgcm", missing)
    assert _check_ids() == ["djust.C037"]


def test_the_state_key_id_joins_binding_page_and_tab():
    one = StateKey("s", "b", "p", "t", "anonymous", "none")
    assert len(one.id) == 64
    assert one.id == StateKey("other-session", "b", "p", "t", "user:int:1", "x").id
    assert len({one.id, StateKey("s", "b2", "p", "t", "a", "n").id}) == 2
    assert len({one.id, StateKey("s", "b", "p2", "t", "a", "n").id}) == 2
    assert len({one.id, StateKey("s", "b", "p", "t2", "a", "n").id}) == 2


@pytest.mark.django_db
def test_switching_saving_off_removes_the_tabs_state_from_the_store_too(settings):
    """#4128's first-contact removal reaches the store, not only the index."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    assert len(MemoryStore.DATA) == 1 and len(_entries(key)) == 1
    settings.DJUST_AUTOMATIC_STATE = False
    assert _bump(key) == 1
    assert not _entries(key) and not MemoryStore.DATA


# ---------------------------------------------------------------------------
# The other road into the session: a lazy legacy child over the fallback
# ---------------------------------------------------------------------------

LAZY = '<div dj-view="' + MODULE + '.LazyChild" dj-lazy></div>'


class LazyChild(LiveView):
    exposure_policy = "legacy"
    template = '<div><span class="n">{{ count }}</span><i>{{ note }}</i></div>'

    def mount(self, request, **kwargs):
        self.count = 0
        self.note = "STORE_LAZY"

    @event_handler
    def inc(self, **kwargs):
        self.count += 1


class LazyPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><section>" + LAZY + "</section></div>"


urlpatterns += [path("lazy/", LazyPage.as_view())]


def _lazy_flow(client: Any = None) -> List[int]:
    import re

    from django.conf import settings
    from django.test import Client

    client = client or Client(enforce_csrf_checks=True)
    page = client.get("/lazy/").content.decode()
    (view_id,) = re.findall(r'data-djust-lazy-id="([^"]+)"', page)
    counts = []
    for name in ("djust_lazy_mount", "inc", "inc"):
        response = client.post(
            "/lazy/",
            json.dumps({"view_id": view_id}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=name,
            HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        assert response.status_code == 200, response.content
        found = re.search(r'class="n"[^>]*>(\d+)</span>', json.loads(response.content)["html"])
        counts.append(int(found.group(1)))
    return counts


@pytest.mark.django_db
@pytest.mark.parametrize("store", STORES)
def test_a_lazy_legacy_childs_state_is_not_written_in_the_clear_with_a_store(store, settings):
    """Its state takes its own road into the session and does not go through
    the store, so with a store named it is not saved at all."""
    settings.DJUST_STATE_STORE = store
    assert _lazy_flow() == [0, 1, 1]  # each request mounts the child again
    assert "STORE_LAZY" not in _all_sessions_text()


@pytest.mark.django_db
def test_control_a_lazy_legacy_child_is_saved_in_the_session_without_a_store(settings):
    assert _lazy_flow() == [0, 1, 2]
    assert "STORE_LAZY" in _all_sessions_text()


# ---------------------------------------------------------------------------
# Review round 2 (#4132)
# ---------------------------------------------------------------------------

BROKEN_MODULE = "djust.tests._broken_store_module_4132.Store"


@pytest.mark.django_db
def test_a_store_module_that_raises_at_import_is_an_unusable_store(settings, caplog):
    """Not only ImportError: the page is served, nothing is saved, C036 fires
    and names the path and the error."""
    settings.DJUST_STATE_STORE = BROKEN_MODULE
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        key = _page()
        assert [_bump(key), _bump(key)] == [1, 1]
    assert not _entries(key)
    from djust.checks.configuration import _check_state_store

    found: list = []
    _check_state_store(found)
    assert [message.id for message in found] == ["djust.C036"]
    text = found[0].msg
    assert BROKEN_MODULE in text and "RuntimeError" in text and "boom at import" in text


def test_c036_names_the_path_and_the_import_error(settings):
    from djust.checks.configuration import _check_state_store

    settings.DJUST_STATE_STORE = "no.such.module.Store"
    found: list = []
    _check_state_store(found)
    assert "no.such.module.Store" in found[0].msg
    assert "ModuleNotFoundError" in found[0].msg and "No module named" in found[0].msg


def test_c037_reports_a_keys_callable_that_raises(settings):
    def keys():
        raise RuntimeError("KEY_MATERIAL_IN_MESSAGE")

    settings.DJUST_STATE_STORE = SEALED
    settings.DJUST_STATE_STORE_KEYS = keys
    from djust.checks.configuration import _check_state_store

    found: list = []
    _check_state_store(found)
    assert [message.id for message in found] == ["djust.C037"]
    assert "RuntimeError" in found[0].msg and "KEY_MATERIAL_IN_MESSAGE" not in found[0].msg


@pytest.mark.parametrize("value", [1, 0, "true", "false", None])
def test_c038_reports_a_strict_setting_that_is_not_a_bool(value, settings):
    settings.DJUST_STATE_STORE_STRICT = value
    assert _check_ids() == ["djust.C038"]
    assert state_store.strict() is False  # only True is strict


@pytest.mark.parametrize("value", [True, False])
def test_c038_is_silent_for_a_bool(value, settings):
    settings.DJUST_STATE_STORE_STRICT = value
    assert _check_ids() == []


class Plain(LiveView):
    """No declared field: its save takes the path with no declared adapter."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


urlpatterns += [path("plain/", Plain.as_view())]


@pytest.mark.django_db
@pytest.mark.parametrize("view_class, url", [(Page, PATH), (Plain, "/plain/")])
def test_the_store_is_written_before_the_session(view_class, url, settings, monkeypatch):
    """Store first, index second, on both save paths (with and without a
    declared field): never an index entry vouching for bytes not yet stored."""
    from djust import _session_merge

    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page(view_class, url=url)
    order: List[str] = []
    real = _session_merge.save_merged

    def recording(session, expected_key=None):
        order.append("session")
        return real(session, expected_key)

    monkeypatch.setattr(_session_merge, "save_merged", recording)
    MemoryStore.CALLS.clear()
    stamps_at_save: List[Any] = []
    real_save = MemoryStore.save

    def save(self, key_, payload, *, expires):
        order.append("store")
        # What the stored session vouches for while the store is written.
        stamps_at_save.append(
            [entry["stamp"] for entry in _entries(key).values()] == [json.loads(payload)["stamp"]]
        )
        return real_save(self, key_, payload, expires=expires)

    monkeypatch.setattr(MemoryStore, "save", save)
    response, _view = _post(key, view_class, url=url)
    assert response.status_code == 200
    assert order[:2] == ["store", "session"], order
    assert stamps_at_save == [False]  # the index entry is not written yet


def _mounted(key: str, tab: str = harness.TAB):
    """A mounted view and its request, for driving ``stage`` directly."""
    request = _request("POST", key, tab=tab)
    view = Plain()
    view.__dict__["_djust_tab"] = tab
    view.mount(request)
    return view, request


@pytest.mark.django_db
def test_a_save_in_flight_keeps_its_owed_store_write_when_the_next_turn_stages(settings):
    """The owed write rides on what ``stage`` returned, not on the view."""
    settings.DJUST_STATE_STORE = EXTERNAL
    key = harness.new_session_key()
    view, request = _mounted(key)
    first = _automatic_state.stage(view, request)
    assert first is not None and not MemoryStore.DATA
    view.count = 5
    second = _automatic_state.stage(view, request)  # the next turn stages
    assert second is not None and second is not first
    _automatic_state.write_pending(view, first)  # the earlier save lands now
    (payload,) = MemoryStore.DATA.values()
    assert json.loads(payload)["state"]["values"]["count"] == 0
    _automatic_state.write_pending(view, second)
    (payload,) = MemoryStore.DATA.values()
    assert json.loads(payload)["state"]["values"]["count"] == 5


def _ids_in_index(key: str) -> set:
    return {
        StateKey(key, e["binding"], e["page"], e["tab"], e["user"], e["tenant"]).id
        for e in _entries(key).values()
    }


@pytest.mark.django_db
def test_a_failed_save_that_also_evicted_leaves_no_row_without_an_index_entry(
    settings, monkeypatch
):
    settings.DJUST_STATE_STORE = EXTERNAL
    monkeypatch.setattr(_automatic_state, "MAX_TABS", 1)
    key = _page()
    assert _bump(key) == 1
    # A second tab's page load evicts the first, and its save fails.
    other = "another-tab-0002"
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: other)
    MemoryStore.DOWN = {"save"}
    _page(key=key)
    # The first tab's row is still in the store, so its index entry is too.
    assert set(MemoryStore.DATA) == _ids_in_index(key) and len(MemoryStore.DATA) == 1
    MemoryStore.DOWN = set()
    _page(key=key)  # now the eviction goes through, and deletes the row
    assert set(MemoryStore.DATA) == _ids_in_index(key) and len(MemoryStore.DATA) == 1
    assert {entry["tab"] for entry in _entries(key).values()} == {other}


@pytest.mark.django_db
def test_a_refused_save_puts_back_the_entries_it_evicted(settings, monkeypatch):
    """The over-limit refusal runs the undo: nothing was written, so the tabs
    the staged write evicted keep their index entries and their rows."""
    settings.DJUST_STATE_STORE = EXTERNAL
    monkeypatch.setattr(_automatic_state, "MAX_TABS", 1)
    key = _page(Plain, url="/plain/")
    before = _entries(key)
    assert len(before) == 1 and len(MemoryStore.DATA) == 1
    request = _request("POST", key, url="/plain/", tab="another-tab-0002")
    view = Plain()
    view.__dict__["_djust_tab"] = "another-tab-0002"
    view.mount(request)
    undo = _automatic_state.stage(view, request)
    assert len([k for k in request.session.keys() if k.startswith("_djust_auto_")]) == 1
    undo()
    held = {k: v for k, v in request.session.items() if k.startswith("_djust_auto_")}
    assert held == before
    assert len(MemoryStore.DATA) == 1 and "delete" not in [c[0] for c in MemoryStore.CALLS]


@pytest.mark.django_db
def test_the_clis_store_calls_run_inside_the_sync_turn_wrapper(settings, capsys):
    from djust.cli import cmd_clear, cmd_stats
    from djust.config import config as djust_config

    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    settings.LIVEVIEW_CONFIG = {
        **getattr(settings, "LIVEVIEW_CONFIG", {}),
        "sync_turn_wrapper": MODULE + ".recording_wrapper",
    }
    djust_config.reset()
    MemoryStore.CALLS.clear()
    MemoryStore.STEPS.clear()
    try:
        cmd_stats(argparse.Namespace())
        cmd_clear(argparse.Namespace(all=True, force=True))
    finally:
        djust_config.reset()
    capsys.readouterr()
    names = [call[0] for call in MemoryStore.CALLS]
    assert names == ["stats", "delete", "clear"], names
    assert MemoryStore.STEPS == ["state_store"] * 3, MemoryStore.STEPS


class Sized(LiveView):
    """A view whose state is about 45% of its own limit."""

    exposure_policy = "explicit"
    automatic_state_max_kb = 8
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.blob = "x" * 3600

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


urlpatterns += [path("sized/", Sized.as_view())]


@pytest.mark.django_db
@pytest.mark.parametrize("store", [None, EXTERNAL, SEALED])
def test_two_tabs_under_the_page_limit_both_survive_with_any_store(store, settings, monkeypatch):
    """Eviction counts the size the per-view limit counts (the encoded state),
    not the sealed size: two tabs at 45% of the limit fit, as with no store."""
    if store is not None:
        settings.DJUST_STATE_STORE = store
    tabs = ["tab-number-0001", "tab-number-0002"]
    key = None
    for tab in tabs:
        monkeypatch.setattr(_automatic_state, "new_tab_id", lambda tab=tab: tab)
        key = _page(Sized, key=key, url="/sized/")
    entries = _entries(key)
    assert len(entries) == 2, "a sibling tab was evicted"
    sizes = {entry["bytes"] for entry in entries.values()}
    assert len(sizes) == 1 and 3600 < sizes.pop() < 4096  # under half of 8 KB
    if store == SEALED:
        # ... though the sealed text itself is well over half the limit.
        assert all(len(entry["sealed"]) > 4096 for entry in entries.values())
    for tab in tabs:
        response, view = _post(key, Sized, url="/sized/", tab=tab)
        assert response.status_code == 200 and view.count == 1
    assert len(_entries(key)) == 2


@pytest.mark.django_db
@pytest.mark.parametrize("store", STORES)
@pytest.mark.parametrize("contact", ["event", "page-load"])
def test_plaintext_saved_before_the_store_is_purged_on_first_contact(
    store, contact, settings, monkeypatch
):
    """Enabling a store must not leave earlier envelopes in the clear: this
    tab's, and every other tab's of the session."""
    key = _page()
    assert _bump(key) == 1
    other = "another-tab-0002"
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: other)
    _page(key=key)
    assert "STORE_PUBLIC" in json.dumps(_entries(key)) and len(_entries(key)) == 2
    settings.DJUST_STATE_STORE = store
    if contact == "event":
        assert _bump(key) == 1  # this tab's old state is not read
    else:
        monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: "a-third-tab-0003")
        _page(key=key)
    text = json.dumps(_entries(key))
    assert not [marker for marker in MARKERS if marker in text], text
    assert all("values" not in entry for entry in _entries(key).values())


@pytest.mark.django_db
def test_a_lazy_childs_plaintext_is_removed_on_first_contact_and_a_warning_names_it(
    settings, caplog
):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    assert _lazy_flow(client) == [0, 1, 2]
    assert "STORE_LAZY" in _all_sessions_text()
    settings.DJUST_STATE_STORE = EXTERNAL
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert _lazy_flow(client) == [0, 1, 1]
    assert "STORE_LAZY" not in _all_sessions_text()
    named = [r for r in caplog.records if "LazyChild" in r.getMessage()]
    assert len(named) == 1 and "DJUST_STATE_STORE" in named[0].getMessage()


@pytest.mark.django_db
def test_djust_clear_all_removes_a_lazy_childs_entry_and_plain_clear_keeps_it(capsys):
    from djust.cli import cmd_clear

    assert _lazy_flow() == [0, 1, 2]
    cmd_clear(argparse.Namespace(all=False, force=True))  # expired only
    assert "STORE_LAZY" in _all_sessions_text()
    cmd_clear(argparse.Namespace(all=True, force=True))
    capsys.readouterr()
    assert "STORE_LAZY" not in _all_sessions_text()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_strict_refusal_on_a_socket_is_logged_once(settings, caplog):
    from djust.tests._ws_frames import has_frame, receive_until

    settings.DJUST_STATE_STORE = EXTERNAL
    settings.DJUST_STATE_STORE_STRICT = True
    key = await sync_to_async(_page)()
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        comm, _mount = await harness.mount_view(Page, key)
        MemoryStore.DOWN = {"save"}
        with caplog.at_level(logging.WARNING):
            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 9})
            await receive_until(comm, has_frame("error"), what="the refusal")
        await comm.disconnect()
    about_it = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and ("state store" in r.getMessage() or "Protected view operation" in r.getMessage())
    ]
    assert [r.levelname for r in about_it] == ["ERROR"], [r.getMessage() for r in about_it]


@pytest.mark.django_db
def test_plaintext_is_purged_on_first_contact_even_when_that_requests_save_is_refused(settings):
    """The purge at the read is written through at once: a first contact
    whose own save never happens (here: refused) still clears the session."""
    key = _page()
    assert _bump(key) == 1
    assert "STORE_HANDLER" in json.dumps(_entries(key))
    settings.DJUST_STATE_STORE = EXTERNAL
    settings.DJUST_STATE_STORE_STRICT = True
    MemoryStore.DOWN = {"save"}
    response, _view = _post(key)
    assert response.status_code == 503
    assert not _entries(key)


# ---------------------------------------------------------------------------
# Review round 3 (#4132)
# ---------------------------------------------------------------------------


def _connection(key: str, tab: str):
    """A second connection of the session: its own tracked copy of it, and a
    mounted view on another tab of the same page."""
    from djust._session_merge import track_session

    request = _request("POST", key, tab=tab)
    assert track_session(request.session)
    view = Page()
    view.__dict__["_djust_tab"] = tab
    view.mount(request)
    return view, request


@pytest.mark.django_db
def test_a_failed_save_does_not_bring_back_another_tabs_older_state(settings, monkeypatch):
    """Two connections share a session, tab cap 1. A's turn evicts tab X in A's
    copy; before A's store write lands, X has an event with the store down (it
    is served, and its entry is dropped: the next request mounts fresh). Then
    A's write fails and its undo puts X's entry back IN A'S COPY ONLY: the
    stored session keeps what X did, so X never reads its older state."""
    from djust._session_merge import save_merged

    settings.DJUST_STATE_STORE = EXTERNAL
    monkeypatch.setattr(_automatic_state, "MAX_TABS", 1)
    key = _page()
    assert _bump(key) == 1  # tab X: count 1, and the store's row says so
    (x_name,) = _entries(key)
    view_a, request_a = _connection(key, "another-tab-0002")
    staged = _automatic_state.stage(view_a, request_a)
    assert x_name not in request_a.session  # evicted in A's copy
    MemoryStore.DOWN = {"save"}
    response, view_x = _post(key)
    assert response.status_code == 200 and view_x.count == 2
    assert x_name not in _entries(key)  # X was promised a fresh mount
    _automatic_state.write_pending(view_a, staged)  # A's write fails too
    assert x_name in request_a.session  # back in A's copy ...
    save_merged(request_a.session, key)
    assert x_name not in _entries(key)  # ... and not written over what X did
    MemoryStore.DOWN = set()
    MOUNTS.clear()
    assert _bump(key) == 1 and MOUNTS == ["Page"]  # fresh, never the older count


@pytest.mark.django_db
def test_a_refused_save_does_not_write_an_evicted_tabs_older_entry_back(settings, monkeypatch):
    """The same on the refused-save path: while A's staged write waits, X saves
    a newer state; A's undo must not store X's older entry over it."""
    from djust._session_merge import save_merged

    settings.DJUST_STATE_STORE = EXTERNAL
    monkeypatch.setattr(_automatic_state, "MAX_TABS", 1)
    key = _page()
    assert _bump(key) == 1
    (x_name,) = _entries(key)
    view_a, request_a = _connection(key, "another-tab-0002")
    undo = _automatic_state.stage(view_a, request_a)
    assert _bump(key) == 2  # X saves meanwhile: a new stamp in the stored session
    newer = _entries(key)[x_name]
    undo()  # A's save is refused
    save_merged(request_a.session, key)
    assert _entries(key)[x_name] == newer
    MOUNTS.clear()
    assert _bump(key) == 3 and MOUNTS == []


def guide_like_wrapper(view, step, turn):
    """A wrapper that reads the view, as an RLS scope does: it fails for the
    command line's ``view=None``."""
    from contextlib import nullcontext

    getattr(view.request, "user", None)
    return nullcontext()


@pytest.mark.django_db
@pytest.mark.parametrize("everything", [True, False])
def test_the_cli_reports_a_wrapper_failure_and_leaves_the_session_as_it_was(
    everything, settings, capsys
):
    from djust.cli import cmd_clear, cmd_stats
    from djust.config import config as djust_config

    settings.DJUST_STATE_STORE = EXTERNAL
    key = _page()
    assert _bump(key) == 1
    name, entry = _sealed_entry(key)
    if not everything:
        _rewrite(key, name, {**entry, "expires": 1})  # expired: plain `djust clear` takes it
    before = dict(SessionStore(key).items())
    settings.LIVEVIEW_CONFIG = {
        **getattr(settings, "LIVEVIEW_CONFIG", {}),
        "sync_turn_wrapper": MODULE + ".guide_like_wrapper",
    }
    djust_config.reset()
    try:
        with pytest.raises(SystemExit) as stopped:
            cmd_clear(argparse.Namespace(all=everything, force=True))
        out = capsys.readouterr().out
        with pytest.raises(SystemExit) as stats_stopped:
            cmd_stats(argparse.Namespace())
        stats_out = capsys.readouterr().out
    finally:
        djust_config.reset()
    assert stopped.value.code == 1 and stats_stopped.value.code == 1
    for text in (out, stats_out):
        assert "guide_like_wrapper" in text and "view=None" in text, text
        assert "AttributeError" in text
    # No index entry left the session while its row is still in the store.
    assert dict(SessionStore(key).items()) == before
    assert len(MemoryStore.DATA) == 1


urlpatterns += [path("sized-%d/" % number, Sized.as_view()) for number in range(4)]


@pytest.mark.django_db
@pytest.mark.parametrize("store", [EXTERNAL, SEALED])
def test_the_session_budget_counts_what_the_session_holds(store, settings, monkeypatch):
    """Four pages of 3.6 KB of state: 14.6 KB encoded, under a 16 KB budget. A
    store outside the session keeps all four; sealed into the session they
    would take about 21 KB, so the oldest go until the session is under it."""
    settings.DJUST_STATE_STORE = store
    monkeypatch.setattr(_automatic_state, "SESSION_BUDGET_KB", 16)
    key = None
    for number in range(4):
        key = _page(Sized, key=key, url="/sized-%d/" % number)
    entries = list(_entries(key).values())
    assert sum(entry["bytes"] for entry in entries) <= 16 * 1024
    if store == EXTERNAL:
        assert len(entries) == 4
    else:
        held = sum(len(entry["sealed"]) for entry in entries)
        assert held <= 16 * 1024, held
        assert len(entries) == 3  # the oldest went; three sealed states fit


@pytest.mark.parametrize("value", [1, "true"])
def test_a_strict_setting_that_is_not_a_bool_warns_once_per_process(
    value, settings, caplog, monkeypatch
):
    monkeypatch.setattr(state_store, "_STRICT_WARNED", set())
    settings.DJUST_STATE_STORE_STRICT = value
    with caplog.at_level(logging.WARNING, logger="djust.state_store"):
        assert [state_store.strict() for _ in range(3)] == [False, False, False]
    said = [r.getMessage() for r in caplog.records if "DJUST_STATE_STORE_STRICT" in r.getMessage()]
    assert len(said) == 1 and "NOT strict" in said[0] and type(value).__name__ in said[0]


@pytest.mark.parametrize("value", [True, False])
def test_a_bool_strict_setting_does_not_warn(value, settings, caplog, monkeypatch):
    monkeypatch.setattr(state_store, "_STRICT_WARNED", set())
    settings.DJUST_STATE_STORE_STRICT = value
    with caplog.at_level(logging.WARNING, logger="djust.state_store"):
        assert state_store.strict() is value
    assert not caplog.records
