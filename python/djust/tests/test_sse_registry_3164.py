"""#3164 — the SSE session registry in ``python/djust/sse.py``.

1. A reused ``session_id`` must not replace a live session owned by someone
   else (the victim's POSTs would hit the attacker's session: 403/404, a
   denial of service), and a stream's ``finally`` pops only its OWN session,
   never a newer one registered under the same id.
2. The per-client and global caps (Finding #25) counted, awaited the mount,
   then registered, so concurrent GETs overshot the cap. They now reserve a
   slot under a ``threading.Lock`` before the mount and release it on every
   exit.

The concurrency tests are deterministic: the first GET's mount blocks on an
``asyncio.Event`` the test controls, so the second GET always runs inside the
first one's check-then-register window.
"""

import asyncio
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
    _auth_user,
    _fake_mount_fail,
    _fake_mount_ok,
)


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    _sse_sessions.clear()
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    yield
    _sse_sessions.clear()
    # A leaked reservation would silently shrink every later test's caps.
    assert sse._sse_reserved == {}
    assert sse._sse_reserved_total == 0


def _get(sid, user):
    request = RequestFactory().get(
        f"/djust/sse/{sid}/", {"view": "demo.View"}, HTTP_ORIGIN=ALLOWED_ORIGIN
    )
    request.user = user
    request.session = SessionStore()
    return request


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
        await view.get(_get(sid, _auth_user(7)), session_id=sid)
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
    entered, gate = asyncio.Event(), asyncio.Event()
    calls = []

    async def mount(self, data):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            await gate.wait()
        self.view_instance = MagicMock()

    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=mount):
        first = asyncio.ensure_future(view.get(_get(sid, _auth_user(8)), session_id=sid))
        await entered.wait()
        await view.get(_get(sid, _auth_user(7)), session_id=sid)  # registers
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
        await view.get(_get(sid, _auth_user(7)), session_id=sid)
        old = _sse_sessions[sid]
        resp = await view.get(_get(sid, _auth_user(7)), session_id=sid)

    assert isinstance(resp, StreamingHttpResponse)
    assert _sse_sessions[sid] is not old


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_closing_a_replaced_stream_does_not_pop_the_new_session():
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        old_resp = await view.get(_get(sid, _auth_user(7)), session_id=sid)
        old = _sse_sessions[sid]
        await view.get(_get(sid, _auth_user(7)), session_id=sid)
        new = _sse_sessions[sid]

    await _close(old_resp, old)
    assert _sse_sessions.get(sid) is new


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.asyncio
async def test_closing_a_stream_pops_its_own_session():
    sid = str(uuid.uuid4())
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await view.get(_get(sid, _auth_user(7)), session_id=sid)
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
        await first

    assert second.status_code == 429
    assert list(_sse_sessions) == [a]


@override_settings(ALLOWED_HOSTS=["example.com"], DJUST_SSE_MAX_SESSIONS_TOTAL=1)
@pytest.mark.asyncio
async def test_global_cap_holds_while_a_mount_is_in_flight():
    mount, entered, gate = _blocking_first_mount()
    view = DjustSSEStreamView()
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=mount):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        first = asyncio.ensure_future(view.get(_get(a, _auth_user(7)), session_id=a))
        await entered.wait()
        second = await view.get(_get(b, _auth_user(9)), session_id=b)
        gate.set()
        await first

    assert second.status_code == 503
    assert list(_sse_sessions) == [a]


@override_settings(ALLOWED_HOSTS=["example.com"], DJUST_SSE_MAX_SESSIONS_PER_CLIENT=1)
@pytest.mark.asyncio
async def test_failed_mount_releases_its_reservation():
    view = DjustSSEStreamView()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_fail):
        await view.get(_get(a, _auth_user(7)), session_id=a)
    with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
        resp = await view.get(_get(b, _auth_user(7)), session_id=b)
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
        resp = await view.get(_get(b, _auth_user(7)), session_id=b)
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
        await view.get(_get(sid, _auth_user(7)), session_id=sid)
        gate.set()
        assert (await first).status_code == 409
    # The fixture asserts no reservation is left behind.
