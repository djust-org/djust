"""``async def check_permissions`` is a first-class permission check.

A view may declare ``async def check_permissions(self, request)``. On the
async transports (WebSocket, SSE) djust awaits it on the event loop, inside
the turn, so a check that does I/O (re-validating a backend session) does not
hold asgiref's shared sync thread and queue every other user's work behind
it. Everywhere else (HTTP GET, the page-POST fallback, the API, embedded
children) the same check is awaited from the synchronous auth code.

Before this change an ``async def check_permissions`` was called and never
awaited: the coroutine is truthy, ``result is False`` never held, and the
check passed for everyone (fail open). These tests pin, for a sync and an
async check on every transport:

- a refusal blocks mount / handler (403 / ``permission_denied`` / close 4403
  for a signed-in user, a login redirect for an anonymous one);
- an exception in the check fails closed;
- with ``reauth_on_event`` the check runs before every event, and an event
  queued behind a slow async check cannot run before its own check resolves;
- ``permission_required`` (``AnyOf`` included) still applies;
- the async check runs on the event loop thread (on a mount, inside app
  turn middleware; the reauth re-check is the outer ``reauth`` built-in).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import sys
import threading
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.template import Context, Template, TemplateSyntaxError
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView, turns
from djust.auth import AnyOf
from djust.auth.core import (
    acheck_view_auth,
    acheck_view_auth_lightweight,
    arun_pre_mount_auth,
    check_view_auth,
)
from djust.config import config
from djust.decorators import event_handler, permission_required

pytest.importorskip("channels")

pytestmark = pytest.mark.django_db(transaction=True)

MOD = __name__

#: What the check answers: True / False / None, or an exception to raise.
VERDICT: Dict[str, Any] = {}
#: One entry per check call: (flavour, thread ident, turn context value).
CALLS: List[tuple] = []
#: Handlers / mounts that actually ran.
RAN: List[str] = []
#: An ``asyncio.Event`` the async check waits on, when set.
GATE: Dict[str, Any] = {}

#: Set by the turn middleware below; an async check reading it proves it ran
#: inside the turn's own context.
turn_probe: contextvars.ContextVar = contextvars.ContextVar("acp_turn_probe", default=None)


async def probe(turn, next):
    token = turn_probe.set("%s:%s" % (turn.kind, turn.transport))
    try:
        return await next(turn)
    finally:
        turn_probe.reset(token)


def _answer():
    value = VERDICT.get("value", True)
    if isinstance(value, BaseException):
        raise value
    return value


class _Base(LiveView):
    template = '<div dj-view="x" dj-id="0">n={{ n }}</div>'
    login_required = True
    tick_interval = None

    def mount(self, request, **kwargs):
        self.n = 0
        RAN.append("mount")

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1
        RAN.append("bump")

    @event_handler()
    @permission_required(AnyOf("acp.alpha", "acp.beta"))
    def guarded(self, **kwargs):
        RAN.append("guarded")


class SyncCheckView(_Base):
    def check_permissions(self, request):
        CALLS.append(("sync", threading.get_ident(), turn_probe.get()))
        return _answer()


class AsyncCheckView(_Base):
    async def check_permissions(self, request):
        CALLS.append(("async", threading.get_ident(), turn_probe.get()))
        gate = GATE.get("gate")
        if gate is not None:
            await gate.wait()
        await asyncio.sleep(0)  # real I/O yields to the loop
        return _answer()


class BridgedCheckView(_Base):
    """The pattern this feature replaces: a sync check bridging async I/O."""

    def check_permissions(self, request):
        CALLS.append(("bridged", threading.get_ident(), turn_probe.get()))

        async def _wait():
            gate = GATE.get("gate")
            if gate is not None:
                await gate.wait()

        async_to_sync(_wait)()
        return _answer()


class OpenSyncCheckView(SyncCheckView):
    login_required = False


class OpenAsyncCheckView(AsyncCheckView):
    login_required = False


class AnyOfSyncCheckView(SyncCheckView):
    permission_required = AnyOf("acp.alpha", "acp.beta")


class AnyOfAsyncCheckView(AsyncCheckView):
    permission_required = AnyOf("acp.alpha", "acp.beta")


VIEWS = (
    SyncCheckView,
    AsyncCheckView,
    BridgedCheckView,
    OpenSyncCheckView,
    OpenAsyncCheckView,
    AnyOfSyncCheckView,
    AnyOfAsyncCheckView,
)
for _cls in VIEWS:
    setattr(sys.modules[MOD], _cls.__name__, _cls)

urlpatterns = [path("acp/%s/" % cls.__name__, cls.as_view()) for cls in VIEWS]

FLAVOURS = {"sync": SyncCheckView, "async": AsyncCheckView}
OPEN = {"sync": OpenSyncCheckView, "async": OpenAsyncCheckView}
ANY_OF = {"sync": AnyOfSyncCheckView, "async": AnyOfAsyncCheckView}


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    is_superuser = False
    pk = 1

    def __init__(self, perms=("acp.alpha",)):
        self.perms = set(perms)

    def has_perm(self, perm, obj=None):
        return perm in self.perms

    def has_perms(self, perms, obj=None):
        return all(p in self.perms for p in perms)


@pytest.fixture(autouse=True)
def _clean():
    VERDICT.clear()
    CALLS.clear()
    RAN.clear()
    GATE.clear()
    turns.reset_chain()
    with override_settings(
        LIVEVIEW_MIDDLEWARE=[f"{MOD}.probe"],
        LIVEVIEW_ALLOWED_MODULES=[MOD],
        DJUST_LIVE_RENDER_ALLOWED_MODULES=[MOD],
        ROOT_URLCONF=MOD,
        LOGIN_URL="/login/",
        ALLOWED_HOSTS=["*"],
    ):
        yield
    turns.reset_chain()
    config.reset()


@pytest.fixture
def reauth_on():
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        config.reset()
        yield
    config.reset()


def _refusing(value):
    """Verdicts that refuse: False, or PermissionDenied raised."""
    return pytest.param(value, id=type(value).__name__ if isinstance(value, Exception) else "False")


REFUSALS = [_refusing(False), _refusing(PermissionDenied("no"))]


# --------------------------------------------------------------------------- #
# The check itself (every sync caller goes through check_view_auth)
# --------------------------------------------------------------------------- #


def _request(user=None):
    request = RequestFactory().get("/acp/")
    request.user = user if user is not None else _User()
    return request


class TestCheckViewAuth:
    """The regression: an unawaited coroutine used to pass every check."""

    @pytest.mark.parametrize("flavour", ["sync", "async"])
    def test_plain_sync_caller_refuses_signed_in(self, flavour):
        VERDICT["value"] = False
        with pytest.raises(PermissionDenied):
            check_view_auth(FLAVOURS[flavour](), _request())

    @pytest.mark.parametrize("flavour", ["sync", "async"])
    def test_plain_sync_caller_redirects_anonymous(self, flavour):
        VERDICT["value"] = False
        assert check_view_auth(OPEN[flavour](), _request(AnonymousUser())) == "/login/"

    @pytest.mark.parametrize("flavour", ["sync", "async"])
    @pytest.mark.parametrize("value", [True, None])
    def test_truthy_or_none_allows(self, flavour, value):
        VERDICT["value"] = value
        assert check_view_auth(FLAVOURS[flavour](), _request()) is None

    @pytest.mark.parametrize("flavour", ["sync", "async"])
    async def test_worker_thread_caller_refuses(self, flavour):
        """sync_to_async(check_view_auth): the shape of every in-thread caller."""
        VERDICT["value"] = False
        with pytest.raises(PermissionDenied):
            await sync_to_async(check_view_auth)(FLAVOURS[flavour](), _request())

    async def test_event_loop_thread_caller_fails_closed(self):
        """A sync caller on the loop thread cannot block on the check: it raises."""
        VERDICT["value"] = True
        with pytest.raises(RuntimeError):
            check_view_auth(AsyncCheckView(), _request())

    @pytest.mark.parametrize("flavour", ["sync", "async"])
    async def test_async_api_refuses_and_redirects(self, flavour):
        VERDICT["value"] = False
        with pytest.raises(PermissionDenied):
            await acheck_view_auth(FLAVOURS[flavour](), _request())
        assert await acheck_view_auth(OPEN[flavour](), _request(AnonymousUser())) == "/login/"
        assert await acheck_view_auth_lightweight(FLAVOURS[flavour](), _request()) is False
        with pytest.raises(PermissionDenied):
            await arun_pre_mount_auth(FLAVOURS[flavour](), _request())

    @pytest.mark.parametrize("flavour", ["sync", "async"])
    async def test_async_api_exception_propagates(self, flavour):
        VERDICT["value"] = RuntimeError("check backend down")
        with pytest.raises(RuntimeError):
            await acheck_view_auth(FLAVOURS[flavour](), _request())
        with pytest.raises(RuntimeError):
            await acheck_view_auth_lightweight(FLAVOURS[flavour](), _request())

    async def test_async_check_runs_on_the_loop_thread(self):
        VERDICT["value"] = True
        assert await acheck_view_auth(AsyncCheckView(), _request()) is None
        assert CALLS == [("async", threading.get_ident(), None)]

    async def test_sync_check_still_runs_in_a_worker_thread(self):
        VERDICT["value"] = True
        assert await acheck_view_auth(SyncCheckView(), _request()) is None
        assert CALLS[0][0] == "sync" and CALLS[0][1] != threading.get_ident()

    @pytest.mark.parametrize("flavour", ["sync", "async"])
    async def test_permission_required_any_of_runs_first(self, flavour):
        VERDICT["value"] = True
        with pytest.raises(PermissionDenied):
            await acheck_view_auth(ANY_OF[flavour](), _request(_User(perms=())))
        assert CALLS == []  # refused before the custom check
        assert await acheck_view_auth(ANY_OF[flavour](), _request(_User(("acp.beta",)))) is None


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


def _asgi(app, user):
    async def wrapped(scope, receive, send):
        scope = dict(scope)
        scope["user"] = user
        scope["session"] = {}
        return await app(scope, receive, send)

    return wrapped


async def _frames(comm, wait=0.3, *, deadline=3.0):
    out = []
    loop = asyncio.get_running_loop()
    stop = loop.time() + deadline
    while True:
        remaining = stop - loop.time()
        if remaining <= 0:
            return out
        try:
            msg = await asyncio.wait_for(comm.output_queue.get(), min(wait, remaining))
        except asyncio.TimeoutError:
            return out
        if msg["type"] == "websocket.close":
            out.append({"__close__": msg.get("code")})
            return out
        if msg["type"] == "websocket.send" and msg.get("text"):
            out.append(json.loads(msg["text"]))


async def _ws(view_cls, user=None):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    user = user if user is not None else _User()
    comm = WebsocketCommunicator(_asgi(LiveViewConsumer.as_asgi(), user), "/ws/")
    ok, _ = await comm.connect()
    assert ok
    await _frames(comm, 0.2)
    await comm.send_json_to(
        {
            "type": "mount",
            "view": f"{MOD}.{view_cls.__name__}",
            "url": "/acp/%s/" % view_cls.__name__,
        }
    )
    return comm, await _frames(comm, 0.5)


async def _close(comm):
    try:
        await comm.disconnect()
    except Exception:  # noqa: BLE001 - already closed by the server
        pass


def _mounted(frames):
    return any(f.get("type") in ("mount", "html_update", "patch") for f in frames)


def _denied(frames):
    return any(
        f.get("type") in ("error", "view_refused") and f.get("code") == "permission_denied"
        for f in frames
    )


def _event(name, ref):
    return {"type": "event", "event": name, "params": {}, "ref": ref}


@pytest.mark.parametrize("flavour", ["sync", "async"])
class TestWebSocket:
    async def test_mount_allowed(self, flavour):
        comm, frames = await _ws(FLAVOURS[flavour])
        await _close(comm)
        assert _mounted(frames), frames
        assert RAN == ["mount"]
        assert len(CALLS) == 1 and CALLS[0][0] == flavour

    @pytest.mark.parametrize("value", REFUSALS)
    async def test_mount_refused_signed_in_closes_4403(self, flavour, value):
        VERDICT["value"] = value
        comm, frames = await _ws(FLAVOURS[flavour])
        frames += await _frames(comm)
        await _close(comm)
        assert "mount" not in RAN
        assert _denied(frames), frames
        assert {"__close__": 4403} in frames

    async def test_mount_refused_anonymous_navigates_to_login(self, flavour):
        VERDICT["value"] = False
        comm, frames = await _ws(OPEN[flavour], user=AnonymousUser())
        frames += await _frames(comm)
        await _close(comm)
        assert "mount" not in RAN
        assert {"type": "navigate", "to": "/login/"} in frames
        assert {"__close__": 4403} in frames

    async def test_mount_check_exception_fails_closed(self, flavour):
        VERDICT["value"] = RuntimeError("SECRET-IN-CHECK")
        comm, frames = await _ws(FLAVOURS[flavour])
        frames += await _frames(comm)
        await _close(comm)
        assert "mount" not in RAN
        assert not _mounted(frames)
        assert any(f.get("type") == "error" for f in frames), frames
        assert "SECRET-IN-CHECK" not in json.dumps(frames)

    async def test_check_runs_in_the_turn_context(self, flavour):
        comm, _ = await _ws(FLAVOURS[flavour])
        await _close(comm)
        assert CALLS[0][2] == "mount:ws"
        if flavour == "async":
            # Awaited on the event loop: the test's own thread.
            assert CALLS[0][1] == threading.get_ident()
        else:
            assert CALLS[0][1] != threading.get_ident()

    async def test_reauth_runs_the_check_before_every_event(self, flavour, reauth_on):
        with patch("djust.runtime._stored_scope_user", AsyncMock(return_value=_User())):
            comm, _ = await _ws(FLAVOURS[flavour])
            try:
                for ref in (1, 2, 3):
                    await comm.send_json_to(_event("bump", ref))
                    await _frames(comm, 0.2)
            finally:
                await _close(comm)
        assert RAN == ["mount", "bump", "bump", "bump"]
        assert len(CALLS) == 4  # mount + one per event
        # The re-check is the ``reauth`` built-in, outside app middleware
        # (ADR-044 D4): it runs before the probe middleware sets its value.
        assert [c[2] for c in CALLS[1:]] == [None] * 3
        if flavour == "async":
            assert {c[1] for c in CALLS} == {threading.get_ident()}

    @pytest.mark.parametrize("value", REFUSALS)
    async def test_reauth_refusal_blocks_the_handler(self, flavour, reauth_on, value):
        with patch("djust.runtime._stored_scope_user", AsyncMock(return_value=_User())):
            comm, _ = await _ws(FLAVOURS[flavour])
            VERDICT["value"] = value
            try:
                await comm.send_json_to(_event("bump", 1))
                frames = await _frames(comm)
            finally:
                await _close(comm)
        assert "bump" not in RAN
        assert {"__close__": 4403} in frames

    async def test_reauth_exception_fails_closed(self, flavour, reauth_on):
        with patch("djust.runtime._stored_scope_user", AsyncMock(return_value=_User())):
            comm, _ = await _ws(FLAVOURS[flavour])
            VERDICT["value"] = RuntimeError("SECRET-IN-CHECK")
            try:
                await comm.send_json_to(_event("bump", 1))
                frames = await _frames(comm)
            finally:
                await _close(comm)
        assert "bump" not in RAN
        assert {"__close__": 4403} in frames
        assert "SECRET-IN-CHECK" not in json.dumps(frames)

    async def test_handler_permission_required_any_of_still_applies(self, flavour, reauth_on):
        with patch("djust.runtime._stored_scope_user", AsyncMock(return_value=_User(perms=()))):
            comm, _ = await _ws(FLAVOURS[flavour], user=_User(perms=()))
            try:
                await comm.send_json_to(_event("guarded", 1))
                frames = await _frames(comm)
            finally:
                await _close(comm)
        assert "guarded" not in RAN
        assert any(f.get("type") == "error" for f in frames), frames

    async def test_view_permission_required_any_of_still_applies(self, flavour):
        comm, frames = await _ws(ANY_OF[flavour], user=_User(perms=()))
        frames += await _frames(comm)
        await _close(comm)
        assert "mount" not in RAN
        assert CALLS == []
        assert {"__close__": 4403} in frames


class TestWebSocketSlowAsyncCheck:
    """An event queued behind a slow async check cannot run before its own check."""

    async def _two_events_behind_a_gate(self, final_verdict):
        gate = asyncio.Event()
        with patch("djust.runtime._stored_scope_user", AsyncMock(return_value=_User())):
            comm, _ = await _ws(AsyncCheckView)
            assert RAN == ["mount"]
            GATE["gate"] = gate
            try:
                await comm.send_json_to(_event("bump", 1))
                await comm.send_json_to(_event("bump", 2))
                held = await _frames(comm, 0.3)
                ran_while_held = list(RAN)
                VERDICT["value"] = final_verdict
                gate.set()
                frames = held + await _frames(comm, 0.4)
            finally:
                await _close(comm)
        return ran_while_held, frames

    async def test_nothing_runs_while_the_check_is_pending(self, reauth_on):
        ran_while_held, _ = await self._two_events_behind_a_gate(True)
        assert ran_while_held == ["mount"]

    async def test_refusal_after_the_wait_blocks_both_events(self, reauth_on):
        _, frames = await self._two_events_behind_a_gate(False)
        assert "bump" not in RAN
        assert {"__close__": 4403} in frames

    async def test_each_event_gets_its_own_check(self, reauth_on):
        await self._two_events_behind_a_gate(True)
        assert RAN == ["mount", "bump", "bump"]
        assert len(CALLS) == 3  # mount + one per event


async def _until(predicate, deadline=3.0):
    loop = asyncio.get_running_loop()
    stop = loop.time() + deadline
    while not predicate():
        assert loop.time() < stop, "timed out waiting"
        await asyncio.sleep(0.01)


async def _shared_thread_free(timeout=0.5):
    """Whether asgiref's shared sync thread can run work right now."""
    try:
        await asyncio.wait_for(sync_to_async(lambda: None)(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


class TestSharedSyncThreadStaysFree:
    """The point of the feature: a waiting async check holds no shared thread.

    Every sync handler, render and ``sync_to_async`` call for every
    connection runs on one thread. A check that waits there (the bridged
    form) stalls them all for as long as it waits; an async check waits on
    the loop. The bridged sibling proves the probe detects a blocked thread.
    """

    async def _mount_held(self, view_cls, reauth_event=False):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        gate = asyncio.Event()
        comm = WebsocketCommunicator(_asgi(LiveViewConsumer.as_asgi(), _User()), "/ws/")
        assert (await comm.connect())[0]
        try:
            await _frames(comm, 0.2)
            frame = {"type": "mount", "view": f"{MOD}.{view_cls.__name__}", "url": "/acp/"}
            if reauth_event:
                await comm.send_json_to(frame)
                await _frames(comm, 0.4)
                assert RAN == ["mount"]
                GATE["gate"] = gate
                await comm.send_json_to(_event("bump", 1))
                await _until(lambda: len(CALLS) == 2)
            else:
                GATE["gate"] = gate
                await comm.send_json_to(frame)
                await _until(lambda: len(CALLS) == 1)
            free = await _shared_thread_free()
            ran_while_held = list(RAN)
            gate.set()
            await _frames(comm, 0.4)
        finally:
            gate.set()
            await _close(comm)
        return free, ran_while_held

    async def test_async_mount_check_leaves_the_thread_free(self):
        free, ran = await self._mount_held(AsyncCheckView)
        assert free, "an async mount check must not hold the shared sync thread"
        assert ran == []  # and mount() has not run while it waits
        assert RAN == ["mount"]

    async def test_bridged_mount_check_blocks_the_thread(self):
        free, ran = await self._mount_held(BridgedCheckView)
        assert not free
        assert ran == []

    async def test_async_reauth_check_leaves_the_thread_free(self, reauth_on):
        with patch("djust.runtime._stored_scope_user", AsyncMock(return_value=_User())):
            free, ran = await self._mount_held(AsyncCheckView, reauth_event=True)
        assert free, "an async re-check must not hold the shared sync thread"
        assert ran == ["mount"]  # the handler waits for its check
        assert RAN == ["mount", "bump"]

    async def test_bridged_reauth_check_blocks_the_thread(self, reauth_on):
        with patch("djust.runtime._stored_scope_user", AsyncMock(return_value=_User())):
            free, ran = await self._mount_held(BridgedCheckView, reauth_event=True)
        assert not free
        assert ran == ["mount"]

    async def test_async_sse_mount_check_leaves_the_thread_free(self, sse_clean):
        gate = asyncio.Event()
        GATE["gate"] = gate
        task = asyncio.ensure_future(_sse_mount(AsyncCheckView))
        try:
            await _until(lambda: len(CALLS) == 1)
            assert await _shared_thread_free()
            assert RAN == []
        finally:
            gate.set()
            await task
        assert RAN == ["mount"]


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #

ORIGIN = "http://testserver"
SID = "41111111-1111-1111-1111-111111111111"


async def _sse_mount(view_cls, user=None):
    from djust.sse import DjustSSEStreamView, _sse_sessions

    request = RequestFactory().get(
        f"/djust/sse/{SID}/?view={MOD}.{view_cls.__name__}&_djust_url=/acp/{view_cls.__name__}/",
        HTTP_ORIGIN=ORIGIN,
    )
    request.user = user if user is not None else _User()
    try:
        await DjustSSEStreamView().get(request, session_id=SID)
    except Exception:  # noqa: BLE001 - a refused mount may answer with an error response
        pass
    return _sse_sessions.get(SID)


async def _sse_post(frame, user=None):
    from djust.sse import DjustSSEMessageView

    request = RequestFactory().post(
        f"/djust/sse/{SID}/message/",
        data=json.dumps(frame),
        content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
    )
    request.user = user if user is not None else _User()
    return await DjustSSEMessageView().post(request, session_id=SID)


def _drain(session):
    out = []
    while session is not None and not session.queue.empty():
        msg = session.queue.get_nowait()
        if msg is not None:
            out.append(msg)
    return out


@pytest.fixture
def sse_clean():
    from djust.sse import _sse_sessions

    _sse_sessions.pop(SID, None)
    yield
    session = _sse_sessions.pop(SID, None)
    if session is not None:
        session.shutdown()


@pytest.mark.parametrize("flavour", ["sync", "async"])
class TestSSE:
    async def test_mount_allowed_and_check_ran(self, flavour, sse_clean):
        session = await _sse_mount(FLAVOURS[flavour])
        assert session is not None and session.view_instance is not None
        assert RAN == ["mount"]
        assert CALLS[0][0] == flavour and CALLS[0][2] == "mount:sse"
        if flavour == "async":
            assert CALLS[0][1] == threading.get_ident()

    @pytest.mark.parametrize("value", REFUSALS)
    async def test_mount_refused(self, flavour, sse_clean, value):
        VERDICT["value"] = value
        session = await _sse_mount(FLAVOURS[flavour])
        assert "mount" not in RAN
        assert session is None or session.view_instance is None

    async def test_mount_exception_fails_closed(self, flavour, sse_clean):
        VERDICT["value"] = RuntimeError("SECRET-IN-CHECK")
        session = await _sse_mount(FLAVOURS[flavour])
        assert "mount" not in RAN
        assert session is None or session.view_instance is None

    async def test_reauth_runs_before_every_event_and_refuses(self, flavour, sse_clean, reauth_on):
        session = await _sse_mount(FLAVOURS[flavour])
        _drain(session)
        await _sse_post(_event("bump", 1))
        await _sse_post(_event("bump", 2))
        assert RAN == ["mount", "bump", "bump"]
        assert len(CALLS) == 3
        VERDICT["value"] = False
        await _sse_post(_event("bump", 3))
        frames = _drain(session)
        assert RAN.count("bump") == 2
        assert any(f.get("code") == "permission_denied" for f in frames), frames

    async def test_reauth_exception_fails_closed(self, flavour, sse_clean, reauth_on):
        session = await _sse_mount(FLAVOURS[flavour])
        _drain(session)
        VERDICT["value"] = RuntimeError("SECRET-IN-CHECK")
        await _sse_post(_event("bump", 1))
        assert "bump" not in RAN
        assert "SECRET-IN-CHECK" not in json.dumps(_drain(session))


# --------------------------------------------------------------------------- #
# HTTP GET, page-POST fallback, API, embedded children
# --------------------------------------------------------------------------- #


def _http(method, url, user, **kwargs):
    from django.contrib.sessions.middleware import SessionMiddleware

    request = getattr(RequestFactory(), method)(url, **kwargs)
    SessionMiddleware(lambda r: None).process_request(request)
    request.user = user
    return request


def _post_event(view_cls, user, name="bump"):
    request = _http(
        "post",
        "/acp/%s/" % view_cls.__name__,
        user,
        data=json.dumps({"event": name, "params": {}}),
        content_type="application/json",
    )
    return view_cls.as_view()(request)


@pytest.mark.parametrize("flavour", ["sync", "async"])
@pytest.mark.parametrize("chain", ["middleware", "no-middleware"])
class TestHTTP:
    @pytest.fixture(autouse=True)
    def _chain(self, chain):
        if chain == "middleware":
            yield
            return
        turns.reset_chain()
        with override_settings(LIVEVIEW_MIDDLEWARE=[]):
            yield
        turns.reset_chain()

    def test_get_allowed(self, flavour, chain):
        view_cls = FLAVOURS[flavour]
        response = view_cls.as_view()(_http("get", "/acp/x/", _User()))
        assert response.status_code == 200
        assert RAN == ["mount"] and CALLS[0][0] == flavour

    @pytest.mark.parametrize("value", REFUSALS)
    def test_get_refused_signed_in_is_403(self, flavour, chain, value):
        VERDICT["value"] = value
        with pytest.raises(PermissionDenied):
            FLAVOURS[flavour].as_view()(_http("get", "/acp/x/", _User()))
        assert "mount" not in RAN

    def test_get_refused_anonymous_redirects(self, flavour, chain):
        VERDICT["value"] = False
        response = OPEN[flavour].as_view()(_http("get", "/acp/x/", AnonymousUser()))
        assert response.status_code == 302
        assert response["Location"].startswith("/login/")
        assert "mount" not in RAN

    def test_get_exception_fails_closed(self, flavour, chain):
        VERDICT["value"] = RuntimeError("check backend down")
        with pytest.raises(RuntimeError):
            FLAVOURS[flavour].as_view()(_http("get", "/acp/x/", _User()))
        assert "mount" not in RAN

    def test_post_fallback_allowed_then_refused(self, flavour, chain):
        view_cls = FLAVOURS[flavour]
        response = _post_event(view_cls, _User())
        assert response.status_code == 200, response.content
        assert "bump" in RAN
        RAN.clear()
        VERDICT["value"] = False
        response = _post_event(view_cls, _User())
        assert response.status_code == 403
        assert "bump" not in RAN

    def test_post_fallback_exception_fails_closed(self, flavour, chain):
        VERDICT["value"] = RuntimeError("check backend down")
        try:
            response = _post_event(FLAVOURS[flavour], _User())
        except RuntimeError:
            response = None
        assert response is None or response.status_code >= 400
        assert "bump" not in RAN

    def test_post_fallback_view_any_of_still_applies(self, flavour, chain):
        response = _post_event(ANY_OF[flavour], _User(perms=()))
        assert response.status_code == 403
        assert "bump" not in RAN and CALLS == []


@pytest.mark.parametrize("flavour", ["sync", "async"])
def test_api_dispatch_applies_the_check(flavour):
    from django.contrib.auth import get_user_model

    from djust.api.dispatch import dispatch_api, reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry

    base = FLAVOURS[flavour]

    slug = "acp-api-%s" % flavour  # one slug per class: the registry scans subclasses

    class ApiView(base):
        api_name = slug

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            RAN.append("ping")
            return {"ok": True}

    reset_registry()
    reset_rate_buckets()
    register_api_view(slug, ApiView)
    try:
        request = _http(
            "post", "/djust/api/%s/ping/" % slug, None, data=b"{}", content_type="application/json"
        )
        request.user = get_user_model().objects.create_user(username="acp-api", password="pw")
        request._dont_enforce_csrf_checks = True
        response = dispatch_api(request, slug, "ping")
        assert response.status_code == 200, response.content
        assert RAN[-1] == "ping" and CALLS[-1][0] == flavour

        RAN.clear()
        VERDICT["value"] = False
        response = dispatch_api(request, slug, "ping")
        assert response.status_code == 403
        assert "ping" not in RAN

        VERDICT["value"] = RuntimeError("check backend down")
        try:
            response = dispatch_api(request, slug, "ping")
        except RuntimeError:
            response = None
        assert response is None or response.status_code >= 400
        assert "ping" not in RAN
    finally:
        reset_registry()
        reset_rate_buckets()


def _render_child(child_cls, user):
    parent = OpenSyncCheckView()
    request = RequestFactory().get("/acp/")
    request.user = user
    parent.request = request
    return Template(
        '{% load live_tags %}{% live_render "' + f"{MOD}.{child_cls.__name__}" + '" %}'
    ).render(Context({"view": parent, "request": request}))


@pytest.mark.parametrize("flavour", ["sync", "async"])
class TestEmbeddedChild:
    def test_child_allowed(self, flavour):
        _render_child(FLAVOURS[flavour], _User())
        assert ("sync" if flavour == "sync" else "async") in [c[0] for c in CALLS]
        assert "mount" in RAN

    def test_child_refused_signed_in(self, flavour):
        VERDICT["value"] = False
        with pytest.raises((PermissionDenied, TemplateSyntaxError)):
            _render_child(FLAVOURS[flavour], _User())
        assert "mount" not in RAN

    def test_child_refused_anonymous(self, flavour):
        VERDICT["value"] = False
        with pytest.raises(TemplateSyntaxError, match="denied"):
            _render_child(OPEN[flavour], AnonymousUser())
        assert "mount" not in RAN

    def test_child_exception_fails_closed(self, flavour):
        VERDICT["value"] = RuntimeError("check backend down")
        with pytest.raises(Exception):  # noqa: B017 - any failure; never a render
            _render_child(FLAVOURS[flavour], _User())
        assert "mount" not in RAN


# --------------------------------------------------------------------------- #
# Explicit exposure (ADR-038): the per-event authorization
# --------------------------------------------------------------------------- #


@pytest.fixture
def staged_explicit(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


@pytest.mark.parametrize("flavour", ["sync", "async"])
@pytest.mark.parametrize("verdict", ["allow", "deny", "raise"])
async def test_explicit_event_authorization(staged_explicit, monkeypatch, flavour, verdict):
    from djust.tests import test_exposure_runtime as rt

    request = await sync_to_async(rt.make_request)()
    runtime, transport = await rt.mount(request)
    view = runtime.view_instance
    assert view.count == 5
    VERDICT["value"] = {
        "allow": True,
        "deny": False,
        "raise": RuntimeError("AUTH_SECRET_SENTINEL"),
    }[verdict]

    if flavour == "async":

        async def check(self, request):
            CALLS.append(("async", threading.get_ident(), None))
            gate = GATE.get("gate")
            if gate is not None:
                await gate.wait()
            return _answer()

    else:

        def check(self, request):
            CALLS.append(("sync", threading.get_ident(), None))
            return _answer()

    monkeypatch.setattr(rt.RuntimeView, "check_permissions", check, raising=False)
    gate = asyncio.Event()
    if flavour == "async":
        GATE["gate"] = gate
    task = asyncio.ensure_future(
        runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    )
    try:
        await _until(lambda: len(CALLS) == 1)
        if flavour == "async":
            # Probed from outside the check: a check bridged from the shared
            # sync thread would hold that thread until the gate opens.
            assert await _shared_thread_free(), "the check must not hold the shared sync thread"
            assert view.count == 5  # the handler waits for its check
    finally:
        gate.set()
        await task
    assert CALLS[-1][0] == flavour
    if flavour == "async":
        assert CALLS[-1][1] == threading.get_ident()
    if verdict == "allow":
        assert view.count == 6
        assert runtime.view_instance is view
    else:
        assert view.count == 5
        assert runtime.view_instance is None
        assert transport.closed_with == 4403
        assert "AUTH_SECRET_SENTINEL" not in json.dumps(transport.sent)


async def _gated_async_check(monkeypatch, view_class):
    async def check(self, request):
        CALLS.append(("async", threading.get_ident(), None))
        gate = GATE.get("gate")
        if gate is not None:
            await gate.wait()
        return _answer()

    monkeypatch.setattr(view_class, "check_permissions", check, raising=False)


@pytest.mark.parametrize("site", ["server_turn", "released_event"])
@pytest.mark.parametrize("verdict", ["allow", "deny"])
async def test_explicit_server_side_authorization(staged_explicit, monkeypatch, site, verdict):
    """The two explicit authorizations with no inbound event: a server turn
    (``authorize_explicit_turn``) and a queued event released by ``db_notify``
    (``_authorize_released_explicit_event``)."""

    from djust.tests import test_exposure_runtime as rt
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(rt.make_request)()
    runtime, transport = await rt.mount(request)
    view = runtime.view_instance
    await _gated_async_check(monkeypatch, rt.RuntimeView)
    VERDICT["value"] = verdict == "allow"
    gate = asyncio.Event()
    GATE["gate"] = gate

    if site == "server_turn":

        async def call():
            try:
                await runtime.authorize_explicit_turn(view)
                return True
            except Exception:  # noqa: BLE001 - any failure is a denial
                return False

    else:
        consumer = LiveViewConsumer()
        consumer._runtime = runtime
        consumer.view_instance = view
        consumer.send_error = AsyncMock()
        consumer.close = AsyncMock()
        consumer._release_revoked_view = AsyncMock()

        async def call():
            return await consumer._authorize_released_explicit_event(view)

    task = asyncio.ensure_future(call())
    try:
        await _until(lambda: len(CALLS) == 1)
        assert await _shared_thread_free(), "the check must not hold the shared sync thread"
    finally:
        gate.set()
    allowed = await task
    assert allowed is (verdict == "allow")
    assert CALLS[0][1] == threading.get_ident()
