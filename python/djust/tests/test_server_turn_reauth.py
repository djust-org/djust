"""``reauth_on_event`` also guards server-originated turns.

``reauth_on_event`` re-checked the principal before every client event, but
tick, ``server_push`` / ``push_to_view``, ``db_notify`` and presence frames were
delivered to a socket whose user had been logged out or lost a permission. The
consumer now runs the same fresh-principal check at the top of each such turn
(under the render lock), throttled per connection, and refuses with
navigate-to-login + close 4403.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from unittest.mock import AsyncMock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

pytest.importorskip("channels")

from djust import LiveView  # noqa: E402
from djust.config import config  # noqa: E402
from djust.websocket import LiveViewConsumer  # noqa: E402

pytestmark = pytest.mark.django_db(transaction=True)

SECRET = "TOP-SECRET-PUSHED-AFTER-REVOCATION"
MOD = __name__


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def has_perms(self, perms):
        return True


class PushView(LiveView):
    login_required = True
    tick_interval = 30
    template = '<div dj-view="x" dj-id="0">v={{ secret }}</div>'

    def mount(self, request, **kw):
        self.secret = "initial"

    def handle_refresh(self, **kw):
        self.secret = SECRET

    def handle_tick(self):
        self.secret = SECRET

    def handle_info(self, message):
        self.secret = SECRET


class OpenView(PushView):
    login_required = False


class QuietView(PushView):
    """Same as PushView but never ticks (so the other turns are deterministic)."""

    tick_interval = None


for _cls in (PushView, OpenView, QuietView):
    setattr(sys.modules[__name__], _cls.__name__, _cls)


class _Principal:
    """Mutable stand-in for the session: what ``channels.auth.get_user`` returns."""

    def __init__(self):
        self.user = _User()
        self.calls = 0

    async def get_user(self, scope):
        self.calls += 1
        if isinstance(self.user, Exception):
            raise self.user
        return self.user


def _mw(app):
    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _User()
        scope["session"] = {}
        return await app(scope, receive, send)

    return mw


async def _drain(comm, wait=0.4):
    outs = []
    while True:
        try:
            outs.append(await asyncio.wait_for(comm.output_queue.get(), wait))
        except asyncio.TimeoutError:
            return outs
        if outs[-1]["type"] == "websocket.close":
            return outs


async def _until(comm, done, timeout=10.0):
    """Frames until ``done(frames)`` holds, a close arrives, or ``timeout`` s pass."""
    outs = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            outs.append(await asyncio.wait_for(comm.output_queue.get(), 0.5))
        except asyncio.TimeoutError:
            continue
        if outs[-1]["type"] == "websocket.close" or done(outs):
            break
    return outs


async def _open(view_name, *, reauth=True, interval=None):
    from channels.testing import WebsocketCommunicator

    cfg = {"reauth_on_event": reauth}
    if interval is not None:
        cfg["reauth_server_turn_interval"] = interval
    ctx = override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], LIVEVIEW_CONFIG=cfg)
    ctx.enable()
    config.reset()
    # Ticks are driven by hand (``_send_turn``): a real timer is a wall-clock race
    # (#2124). The patched loop only records the consumer and returns.
    consumers = []

    async def _record_consumer(self, interval_ms):
        consumers.append(self)

    patcher = patch.object(LiveViewConsumer, "_run_tick", _record_consumer)
    patcher.start()
    comm = WebsocketCommunicator(_mw(LiveViewConsumer.as_asgi()), "/ws/")
    comm._djust_patcher = patcher
    comm._djust_consumers = consumers
    ok, _ = await comm.connect()
    assert ok
    try:
        await comm.receive_json_from(timeout=2)
    except Exception:  # noqa: BLE001
        pass
    await comm.send_json_to({"type": "mount", "view": f"{MOD}.{view_name}"})
    first = await comm.receive_json_from(timeout=3)
    assert first.get("type") in ("mount", "html_update", "patch"), first
    if view_name in ("PushView", "OpenView"):  # the views that tick
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while not consumers and loop.time() < deadline:
            await asyncio.sleep(0.01)
        assert consumers, "the tick task was never started"
    return comm, ctx


async def _close(comm, ctx):
    try:
        await comm.disconnect()
    except Exception:  # noqa: BLE001
        pass
    getattr(comm, "_djust_patcher", None) and comm._djust_patcher.stop()
    ctx.disable()
    config.reset()


def _texts(frames):
    return [f.get("text") or "" for f in frames if f["type"] == "websocket.send"]


def _pushed(frames):
    return any(SECRET in t for t in _texts(frames))


def _closed_4403(frames):
    return [f for f in frames if f["type"] == "websocket.close" and f.get("code") == 4403]


def _navigates(frames):
    return any('"navigate"' in t for t in _texts(frames))


PUSH = {"type": "server_push", "handler": "handle_refresh", "payload": {}}
NOTIFY = {"type": "db_notify", "channel": "c", "payload": {}}


async def _send_turn(comm, kind):
    if kind == "server_push":
        await comm.send_input(PUSH)
    elif kind == "db_notify":
        await comm.send_input(NOTIFY)
    elif kind == "tick":
        await comm._djust_consumers[0]._tick_once()


KINDS = [("server_push", "QuietView"), ("db_notify", "QuietView"), ("tick", "PushView")]


@pytest.mark.parametrize("kind,view", KINDS)
async def test_revoked_user_gets_no_frame_and_is_closed(kind, view):
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open(view)
        try:
            principal.user = AnonymousUser()  # logged out after mount
            await _send_turn(comm, kind)
            frames = await _until(comm, lambda o: bool(_closed_4403(o)))
            assert not _pushed(frames)
            assert _closed_4403(frames)
            assert _navigates(frames)
        finally:
            await _close(comm, ctx)


@pytest.mark.parametrize("kind,view", KINDS)
async def test_authorized_user_still_receives_push(kind, view):
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open(view)
        try:
            await _send_turn(comm, kind)
            frames = await _until(comm, _pushed)
            assert _pushed(frames)
            assert not _closed_4403(frames)
            assert principal.calls >= 1
        finally:
            await _close(comm, ctx)


@pytest.mark.parametrize("kind,view", KINDS)
async def test_setting_off_pushes_unchanged(kind, view):
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open(view, reauth=False)
        try:
            principal.user = AnonymousUser()
            await _send_turn(comm, kind)
            frames = await _until(comm, _pushed)
            assert _pushed(frames)
            assert not _closed_4403(frames)
            assert principal.calls == 0  # no session read at all
        finally:
            await _close(comm, ctx)


@pytest.mark.parametrize("kind,view", KINDS)
async def test_view_without_auth_requirements_is_not_checked(kind, view):
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open("OpenView" if kind == "tick" else "QuietView")
        try:
            await _send_turn(comm, kind)
            await _drain(comm, 0.3)
        finally:
            await _close(comm, ctx)
    # QuietView/PushView require login so they DO read; OpenView must not.
    if kind == "tick":
        assert principal.calls == 0


@pytest.mark.parametrize("kind,view", KINDS)
async def test_raising_check_denies_and_logs_type_and_view_only(kind, view, caplog):
    principal = _Principal()
    principal.user = RuntimeError(f"db password hunter2 {SECRET}")
    caplog.set_level(logging.DEBUG, logger="djust")
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open(view)
        try:
            principal.user = RuntimeError(f"db password hunter2 {SECRET}")
            await _send_turn(comm, kind)
            frames = await _until(comm, lambda o: bool(_closed_4403(o)))
        finally:
            await _close(comm, ctx)
    assert not _pushed(frames)
    assert _closed_4403(frames)
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "re-check raised" in r.getMessage()
    ]
    assert len(warnings) >= 1
    for r in warnings:
        msg = r.getMessage()
        assert "RuntimeError" in msg and view in msg
        assert "hunter2" not in msg and SECRET not in msg
        assert r.exc_info is None
    assert "hunter2" not in caplog.text


async def test_rapid_ticks_cause_at_most_one_lookup_per_interval():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open("PushView", interval=3600)
        try:
            for _ in range(15):
                await comm._djust_consumers[0]._tick_once()
            frames = await _until(comm, _pushed)
            assert _pushed(frames)
            assert principal.calls == 1
        finally:
            await _close(comm, ctx)


async def test_first_turn_after_long_gap_rechecks():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open("QuietView", interval=0.5)
        try:
            await comm.send_input(PUSH)
            assert _pushed(await _drain(comm, 0.2))
            assert principal.calls == 1
            await comm.send_input(PUSH)
            assert not _closed_4403(await _drain(comm, 0.2))
            assert principal.calls == 1  # inside the interval: no lookup
            principal.user = AnonymousUser()
            await asyncio.sleep(0.6)  # long gap
            await comm.send_input(PUSH)
            frames = await _drain(comm, 0.4)
            assert principal.calls == 2
            assert not _pushed(frames)
            assert _closed_4403(frames)
        finally:
            await _close(comm, ctx)


async def test_real_session_logout_stops_push():
    """End-to-end with a real session row, with the default session backend."""
    from channels.db import database_sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth import get_user_model, login, logout
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    def setup():
        user = get_user_model().objects.create_user("alice", password="x")
        rf = RequestFactory().get("/")
        store = SessionStore()
        rf.session, rf.user = store, user
        login(rf, user, backend="django.contrib.auth.backends.ModelBackend")
        store.save()
        return user, store, rf

    user, store, rf = await database_sync_to_async(setup)()

    def mw(app):
        async def inner(scope, receive, send):
            scope = dict(scope)
            scope["user"], scope["session"] = user, store
            return await app(scope, receive, send)

        return inner

    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[MOD], LIVEVIEW_CONFIG={"reauth_on_event": True}
    ):
        config.reset()
        try:
            comm = WebsocketCommunicator(mw(LiveViewConsumer.as_asgi()), "/ws/")
            await comm.connect()
            try:
                await comm.receive_json_from(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            await comm.send_json_to({"type": "mount", "view": f"{MOD}.QuietView"})
            await comm.receive_json_from(timeout=3)
            await database_sync_to_async(lambda: logout(rf))()
            await comm.send_input(PUSH)
            frames = await _drain(comm, 1.0)
            assert not _pushed(frames)
            assert _closed_4403(frames)
        finally:
            config.reset()


@pytest.mark.parametrize("allowed", [True, False])
async def test_presence_event_is_gated_on_the_reauth_check(allowed):
    consumer = LiveViewConsumer()
    consumer.view_instance = PushView()
    consumer.send_json = AsyncMock()
    consumer._reauth_legacy_server_turn = AsyncMock(return_value=allowed)
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        config.reset()
        try:
            await consumer.presence_event({"event": "join", "payload": {"user": "bob"}})
        finally:
            config.reset()
    assert consumer.send_json.await_count == (1 if allowed else 0)


async def test_revoked_latch_refuses_later_turns_without_another_lookup():
    principal = _Principal()
    with patch("channels.auth.get_user", principal.get_user):
        comm, ctx = await _open("QuietView", interval=0)
        try:
            principal.user = AnonymousUser()
            await comm.send_input(PUSH)
            frames = await _drain(comm, 0.4)
            assert _closed_4403(frames)
            calls = principal.calls
            await comm.send_input(PUSH)
            assert not _pushed(await _drain(comm, 0.3))
            assert principal.calls == calls
        finally:
            await _close(comm, ctx)


class AsyncView(PushView):
    tick_interval = None

    def handle_go(self, **kw):
        self.start_async(self._work)

    def _work(self):
        return 1

    def handle_async_result(self, name, result=None, error=None):
        self.secret = SECRET


setattr(sys.modules[__name__], "AsyncView", AsyncView)


@pytest.mark.parametrize("revoked", [True, False])
async def test_async_result_turn_is_gated(revoked):
    consumer = LiveViewConsumer()
    view = AsyncView()
    view.secret = "initial"
    consumer.view_instance = view
    consumer.send_json = AsyncMock()
    consumer._send_update = AsyncMock()
    consumer._reauth_legacy_server_turn = AsyncMock(return_value=not revoked)
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        config.reset()
        try:
            await consumer._run_async_work("t", lambda: 1, (), {}, event_name="go")
        finally:
            config.reset()
    consumer._reauth_legacy_server_turn.assert_awaited()
    assert (view.secret == SECRET) is (not revoked)
    assert (consumer._send_update.await_count == 0) is revoked


@pytest.fixture(autouse=True)
def _reset_config_after_each_test():
    """Drop the config cached under this test's settings (``reauth_on_event``
    on), so a later test in the same worker does not inherit it."""
    yield
    from djust.config import config as _config

    _config.reset()
