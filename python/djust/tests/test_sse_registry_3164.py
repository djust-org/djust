"""#3164 — the SSE session registry in ``python/djust/sse.py``.

1. A reused ``session_id`` must not replace a LIVE session owned by someone
   else (the victim's POSTs would hit the new session: 403/404, a denial of
   service). A session whose stream has closed or is closing is replaceable,
   so a tab whose owner changed (a login or logout elsewhere) can reconnect.
   A stream's ``finally`` pops only its OWN session.
2. The per-client and global caps (Finding #25) counted, awaited the mount,
   then registered, so concurrent GETs overshot the cap. They now reserve a
   slot under a ``threading.Lock`` before the mount and release it on every
   exit.
3. A client that disconnects right after the ``sse_connect`` ack, or before
   the stream starts, must not leave its session registered (a loop of those
   filled the global cap).

The concurrency tests are deterministic: the first GET's mount blocks on an
``asyncio.Event`` the test controls, so the second GET always runs inside the
first one's check-then-register window.

Each test keeps the responses of the streams it treats as open in ``_open``,
as Django does while it serves them.
"""

import asyncio
import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.sessions.backends.cache import SessionStore
from django.http import StreamingHttpResponse
from django.test import RequestFactory, override_settings

from djust import sse
from djust.sse import DjustSSEStreamView, _sse_sessions

from .test_sse_session_binding_f24_f25 import (
    ALLOWED_ORIGIN,
    _anon_user,
    _auth_user,
    _fake_mount_fail,
    _fake_mount_ok,
)

_open: list = []


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    _sse_sessions.clear()
    _open.clear()
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    yield
    _open.clear()
    _sse_sessions.clear()
    # A leaked reservation would silently shrink every later test's caps.
    assert sse._sse_reserved == {}
    assert sse._sse_reserved_total == 0


def _get(sid, user, session_key=None):
    request = RequestFactory().get(
        f"/djust/sse/{sid}/", {"view": "demo.View"}, HTTP_ORIGIN=ALLOWED_ORIGIN
    )
    request.user = user
    request.session = SessionStore(session_key=session_key)
    return request


async def _open_stream(view, sid, user, **kw):
    response = await view.get(_get(sid, user, **kw), session_id=sid)
    _open.append(response)
    return response


def _stream(response):
    """The stream generator Django iterates (what the ASGI handler pulls)."""
    return response._iterator


async def _close(response, session):
    """Run a stream's generator to the end, as a client disconnect would."""
    session.shutdown()
    async for _chunk in response.streaming_content:
        pass


# ---------------------------------------------------------------------------
# 1. session_id reuse
# ---------------------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_reused_id_with_a_different_owner_is_refused():
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        await _open_stream(view, sid, _auth_user(7))
        victim = _sse_sessions[sid]
        resp = await view.get(_get(sid, _auth_user(8)), session_id=sid)

    assert resp.status_code == 409
    assert _sse_sessions[sid] is victim
    assert victim._owner_user_pk == 7


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_reused_id_refused_when_the_other_owner_registers_during_the_mount():
    """Both GETs pass the early check; the owner that registers second loses."""
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    mount, entered, gate = _blocking_first_mount()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=mount):
        first = asyncio.ensure_future(view.get(_get(sid, _auth_user(8)), session_id=sid))
        await entered.wait()
        await _open_stream(view, sid, _auth_user(7))  # registers
        victim = _sse_sessions[sid]
        gate.set()
        late = await first

    assert late.status_code == 409
    assert _sse_sessions[sid] is victim


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_reused_id_with_the_same_owner_replaces():
    """EventSource auto-reconnect re-requests the same URL (same id, same owner)."""
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        await _open_stream(view, sid, _auth_user(7))
        old = _sse_sessions[sid]
        resp = await _open_stream(view, sid, _auth_user(7))

    assert isinstance(resp, StreamingHttpResponse)
    assert _sse_sessions[sid] is not old


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_owner_changed_reconnect_replaces_a_closing_session(monkeypatch):
    """Review finding 1: a tab's anonymous stream dropped; the user logged in in
    another tab; EventSource reconnects with the same id under the new owner.
    The old stream is closing (in its linger window), so the reconnect wins."""
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0.2)
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        old_resp = await _open_stream(view, sid, _anon_user(), session_key="k" * 32)
        old = _sse_sessions[sid]
        # The old stream starts and sends its ack; then the client drops. Its
        # ``finally`` runs and lingers with the session still registered.
        agen = _stream(old_resp)
        await agen.__anext__()
        closing = asyncio.ensure_future(agen.aclose())
        for _ in range(50):
            if old._stream_closed:
                break
            await asyncio.sleep(0.001)
        assert _sse_sessions.get(sid) is old  # still registered, lingering
        resp = await _open_stream(view, sid, _auth_user(42))

    assert isinstance(resp, StreamingHttpResponse)
    new = _sse_sessions[sid]
    assert new is not old and new._owner_user_pk == 42
    # The old stream's finally, when its linger ends, must not pop the new one.
    await closing
    assert _sse_sessions.get(sid) is new


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_owner_changed_reconnect_replaces_a_shut_down_session():
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        await _open_stream(view, sid, _anon_user(), session_key="k" * 32)
        _sse_sessions[sid].active = False  # shut down, still registered
        resp = await _open_stream(view, sid, _auth_user(42))
    assert isinstance(resp, StreamingHttpResponse)
    assert _sse_sessions[sid]._owner_user_pk == 42


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_closing_a_replaced_stream_does_not_pop_the_new_session():
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        old_resp = await _open_stream(view, sid, _auth_user(7))
        old = _sse_sessions[sid]
        await _open_stream(view, sid, _auth_user(7))
        new = _sse_sessions[sid]

    await _close(old_resp, old)
    assert _sse_sessions.get(sid) is new


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_closing_a_stream_pops_its_own_session():
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, sid, _auth_user(7))
    await _close(resp, _sse_sessions[sid])
    assert sid not in _sse_sessions


# ---------------------------------------------------------------------------
# 2. caps reserved before the mount
# ---------------------------------------------------------------------------


def _blocking_first_mount():
    """A dispatch_mount stand-in whose FIRST call blocks until ``gate`` is set."""
    entered, gate = asyncio.Event(), asyncio.Event()
    calls = []

    async def mount(self, data):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            await gate.wait()
        self.view_instance = MagicMock()

    return mount, entered, gate


@override_settings(ALLOWED_HOSTS=["example.com"], DJUST_SSE_MAX_SESSIONS_PER_CLIENT=1)
@pytest.mark.asyncio
async def test_per_client_cap_holds_while_a_mount_is_in_flight():
    mount, entered, gate = _blocking_first_mount()
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=mount):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        first = asyncio.ensure_future(view.get(_get(a, _auth_user(7)), session_id=a))
        await entered.wait()
        second = await view.get(_get(b, _auth_user(7)), session_id=b)
        gate.set()
        _open.append(await first)

    assert second.status_code == 429
    assert list(_sse_sessions) == [a]


@override_settings(ALLOWED_HOSTS=["example.com"], DJUST_SSE_MAX_SESSIONS_TOTAL=1)
@pytest.mark.asyncio
async def test_global_cap_holds_while_a_mount_is_in_flight(caplog):
    mount, entered, gate = _blocking_first_mount()
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=mount):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        first = asyncio.ensure_future(view.get(_get(a, _auth_user(7)), session_id=a))
        await entered.wait()
        with caplog.at_level(logging.WARNING, logger="djust.sse"):
            second = await view.get(_get(b, _auth_user(9)), session_id=b)
        gate.set()
        _open.append(await first)

    assert second.status_code == 503
    assert list(_sse_sessions) == [a]
    # The capacity log counts the in-flight reservation (nothing registered yet).
    assert "global session cap reached (1 registered or mounting)" in caplog.text


@override_settings(ALLOWED_HOSTS=["example.com"], DJUST_SSE_MAX_SESSIONS_PER_CLIENT=1)
@pytest.mark.asyncio
async def test_failed_mount_releases_its_reservation():
    view = DjustSSEStreamView()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_fail):
        await view.get(_get(a, _auth_user(7)), session_id=a)
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, b, _auth_user(7))
    assert isinstance(resp, StreamingHttpResponse)
    assert list(_sse_sessions) == [b]


@override_settings(ALLOWED_HOSTS=["example.com"], DJUST_SSE_MAX_SESSIONS_PER_CLIENT=1)
@pytest.mark.asyncio
async def test_raising_mount_releases_its_reservation():
    async def boom(self, data):
        raise RuntimeError("mount exploded")

    view = DjustSSEStreamView()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=boom):
        with pytest.raises(RuntimeError, match="mount exploded"):
            await view.get(_get(a, _auth_user(7)), session_id=a)
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, b, _auth_user(7))
    assert isinstance(resp, StreamingHttpResponse)


@override_settings(ALLOWED_HOSTS=["example.com"], DJUST_SSE_MAX_SESSIONS_PER_CLIENT=1)
@pytest.mark.asyncio
async def test_refused_reuse_releases_its_reservation():
    """The id-conflict refusal after the mount must not leak the slot."""
    sid = str(uuid.uuid4())
    mount, entered, gate = _blocking_first_mount()
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=mount):
        first = asyncio.ensure_future(view.get(_get(sid, _auth_user(8)), session_id=sid))
        await entered.wait()
        await _open_stream(view, sid, _auth_user(7))
        gate.set()
        assert (await first).status_code == 409
    # The fixture asserts no reservation is left behind.


def test_double_release_is_logged_not_clamped(caplog):
    """Review minor: a second release of one reservation is an error, logged,
    and never drives a counter negative or frees another GET's slot."""
    assert sse._reserve_sse_slot("user:1", str(uuid.uuid4()), ("user", 1)) is None
    assert sse._reserve_sse_slot("user:2", str(uuid.uuid4()), ("user", 2)) is None
    sse._release_sse_slot("user:1")
    with caplog.at_level(logging.ERROR, logger="djust.sse"):
        sse._release_sse_slot("user:1")
    assert "released twice" in caplog.text
    assert sse._sse_reserved == {"user:2": 1}
    assert sse._sse_reserved_total == 1
    sse._release_sse_slot("user:2")


# ---------------------------------------------------------------------------
# 3. a client that disconnects early does not leak its registration
# ---------------------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_disconnect_right_after_the_ack_unregisters():
    """The review's probe: one ``__anext__()`` (the ack) then ``aclose()``."""
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, sid, _auth_user(7))
    agen = _stream(resp)
    first = await agen.__anext__()
    assert '"sse_connect"' in first
    await agen.aclose()
    assert sid not in _sse_sessions


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_disconnect_before_the_first_ack_unregisters_on_close(monkeypatch):
    """The response is closed before Django ever iterates it (the client left
    while the view was running): the session must not stay registered.

    The start deadline is pushed out of reach, so this pins ``close()``."""
    monkeypatch.setattr(sse, "_STREAM_START_DEADLINE_S", 3600)
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, sid, _auth_user(7))
    assert sid in _sse_sessions
    resp.close()
    assert sid not in _sse_sessions
    # A stream closed before it started yields nothing, not even the ack.
    assert [c async for c in _stream(resp)] == []


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_stream_that_never_starts_is_dropped_at_the_start_deadline(monkeypatch):
    """The ASGI handler was cancelled before it could close the response, so
    neither the stream nor ``close()`` ever runs: the start deadline drops it."""
    monkeypatch.setattr(sse, "_STREAM_START_DEADLINE_S", 0.01)
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, sid, _auth_user(7))
    assert sid in _sse_sessions
    for _ in range(100):
        if sid not in _sse_sessions:
            break
        await asyncio.sleep(0.01)
    assert sid not in _sse_sessions
    assert [c async for c in _stream(resp)] == []


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_start_deadline_leaves_a_started_stream_alone(monkeypatch):
    monkeypatch.setattr(sse, "_STREAM_START_DEADLINE_S", 0.01)
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, sid, _auth_user(7))
    agen = _stream(resp)
    await agen.__anext__()
    await asyncio.sleep(0.05)
    assert sid in _sse_sessions
    await agen.aclose()
    assert sid not in _sse_sessions


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_close_after_a_started_stream_leaves_cleanup_to_the_stream():
    """``response.close()`` after streaming began must not pull the session out
    from under the stream's own linger (in-flight POSTs still need it)."""
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await _open_stream(view, sid, _auth_user(7))
    agen = _stream(resp)
    await agen.__anext__()
    resp.close()
    assert sid in _sse_sessions
    await agen.aclose()
    assert sid not in _sse_sessions


@override_settings(
    ALLOWED_HOSTS=["example.com"],
    DJUST_SSE_MAX_SESSIONS_TOTAL=3,
    DJUST_SSE_MAX_SESSIONS_PER_CLIENT=3,
)
@pytest.mark.asyncio
async def test_repeated_ack_then_disconnect_does_not_grow_the_registry():
    """A loop of open-read-ack-drop GETs used to fill the global cap and turn
    every other client away with 503. Forty rounds against a cap of 3."""
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        for _ in range(40):
            sid = str(uuid.uuid4())
            resp = await view.get(_get(sid, _auth_user(7)), session_id=sid)
            assert isinstance(resp, StreamingHttpResponse), resp.status_code
            agen = _stream(resp)
            await agen.__anext__()
            await agen.aclose()
            assert len(_sse_sessions) == 0
        other = await _open_stream(view, str(uuid.uuid4()), _auth_user(9))
    assert isinstance(other, StreamingHttpResponse)
