"""Deferred activity commits through real SSE/WS dispatch before its success frame."""

import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.security import unsign_snapshot
from djust.sse import SSESession
from djust.websocket import LiveViewConsumer

from ._ws_frames import receive_settled, has_type
from .test_exposure_runtime import make_request

TOKEN = "state_snapshot_signed"
MOD = __name__


class DeferredLegacy(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = "<div dj-root>count={{ count }} label={{ label }}</div>"
    _listen_channels = frozenset({"deferred_3253"})

    def mount(self, request, **kwargs):
        self.count = 0
        self.label = "initial"
        self.set_activity_visible("panel", False)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, label=self.label, **kwargs)

    @event_handler()
    def change(self, quiet=False, **kwargs):
        self.count += 1
        self.label = "updated"
        self._skip_render = quiet

    @event_handler()
    def reveal(self, **kwargs):
        self.set_activity_visible("panel", True)
        self._skip_render = True

    def handle_info(self, message):
        self.set_activity_visible("panel", True)


class DeferredExplicit(DeferredLegacy):
    exposure_policy = "explicit"
    count = state(0, persist="server")
    label = state("initial", persist="client", client=True)


@pytest.fixture(autouse=True)
def settings_for_test():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False, DJUST_TENANTS=None):
        yield


def drain(session):
    frames = []
    while not session.queue.empty():
        frame = session.queue.get_nowait()
        if frame is not None:
            frames.append(frame)
    return frames


async def mount_sse(request, cls):
    session = SSESession(str(uuid.uuid4()))
    session._request = request
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": f"{MOD}.{cls.__name__}", "url": request.path}
    )
    frames = drain(session)
    assert any(f.get("type") == "mount" for f in frames), frames
    return session


async def sse_event(session, request, event, params):
    fresh = await sync_to_async(make_request)(request.session.session_key)
    await session.dispatch(fresh, {"type": "event", "event": event, "params": params})
    return drain(session)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", ["sse", "ws", "ws-notify"])
@pytest.mark.parametrize("cls", [DeferredLegacy, DeferredExplicit])
@pytest.mark.parametrize("quiet", [False, True])
async def test_deferred_turn_is_saved_and_refreshes_token(transport, cls, quiet):
    request = await sync_to_async(make_request)()
    session = socket = None
    try:
        if transport == "sse":
            session = await mount_sse(request, cls)
            queued = await sse_event(
                session, request, "change", {"_activity": "panel", "quiet": quiet}
            )
            assert any(f.get("type") == "noop" for f in queued)
            frames = await sse_event(session, request, "reveal", {})
        else:
            socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            socket.scope.update(session=request.session, user=request.user, tenant=None)
            assert (await socket.connect())[0]
            await socket.receive_json_from(timeout=3)
            await socket.send_json_to(
                {"type": "mount", "view": f"{MOD}.{cls.__name__}", "url": request.path}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "change",
                    "params": {"_activity": "panel", "quiet": quiet},
                }
            )
            await receive_settled(socket, has_type("noop"))
            if transport == "ws-notify":
                await get_channel_layer().group_send(
                    "djust_db_notify_deferred_3253",
                    {"type": "db_notify", "channel": "deferred_3253", "payload": {}},
                )
            else:
                await socket.send_json_to({"type": "event", "event": "reveal", "params": {}})
            frames = await receive_settled(
                socket,
                lambda fs: any(
                    f.get(TOKEN) or f.get("event_name") == "change" or f.get("type") == "noop"
                    for f in fs
                ),
            )
        successes = [f for f in frames if f.get("type") in ("noop", "patch", "html_update")]
        assert successes, frames
        # Read fresh storage, not the in-memory mounted instance/session cache.
        fresh = await sync_to_async(make_request)(request.session.session_key)
        if cls is DeferredExplicit:
            from djust._exposure_sessions import server_state_adapter

            stored = await sync_to_async(lambda: server_state_adapter(cls(), fresh).load())()
            assert stored["count"] == 1, frames
        else:
            stored = await sync_to_async(fresh.session.load)()
            assert stored[f"liveview_{request.path}"]["count"] == 1, frames
        assert successes[-1].get(TOKEN), frames
        if cls is DeferredLegacy:
            payload = unsign_snapshot(
                successes[-1][TOKEN], f"{MOD}.{cls.__name__}", request.session.session_key
            )
            assert json.loads(payload)["count"] == 1
    finally:
        if socket is not None:
            await socket.disconnect()
        if session is not None:
            await session.close()


@pytest.mark.django_db
@pytest.mark.parametrize("quiet", [False, True])
def test_http_post_returns_current_legacy_token(quiet):
    request = make_request()
    response = DeferredLegacy.as_view()(request)
    assert response.status_code == 200
    post = RequestFactory().post(
        request.path,
        json.dumps({"event": "change", "params": {"quiet": quiet}}),
        content_type="application/json",
    )
    post.session, post.user = request.session, request.user
    response = DeferredLegacy.as_view()(post)
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    assert body.get(TOKEN), body
    state_json = unsign_snapshot(body[TOKEN], f"{MOD}.DeferredLegacy", post.session.session_key)
    assert json.loads(state_json)["count"] == 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("outcome", ["failure", "delay", "cancel"])
async def test_deferred_explicit_waits_for_storage(monkeypatch, quiet, outcome):
    import asyncio
    import threading

    from djust._exposure_sessions import server_state_adapter

    request = await sync_to_async(make_request)()
    session = await mount_sse(request, DeferredExplicit)
    started, release = threading.Event(), threading.Event()
    original = session.runtime._save_explicit_root
    task = None

    def save(view, current_request):
        if view.count == 1:
            started.set()
            if outcome == "failure":
                raise RuntimeError("storage unavailable")
            assert release.wait(3), "test storage gate timed out"
        return original(view, current_request)

    monkeypatch.setattr(session.runtime, "_save_explicit_root", save)
    monkeypatch.setattr(session.runtime, "_explicit_save_deadline", lambda: 2)
    try:
        await sse_event(session, request, "change", {"_activity": "panel", "quiet": quiet})
        task = asyncio.create_task(sse_event(session, request, "reveal", {}))
        async with asyncio.timeout(2):
            while not started.is_set() and not task.done():
                await asyncio.sleep(0.001)
        assert started.is_set(), "deferred dispatch skipped persistence"
        if outcome == "failure":
            frames = await task
            assert any(f.get("code") == "state_error" for f in frames), frames
        else:
            # The outer reveal may acknowledge count=0. No deferred success is
            # allowed while its count=1 write is blocked.
            frames = drain(session)
            assert not any(f.get("type") in ("patch", "html_update") for f in frames), frames
            assert not any(f.get("event_name") == "change" for f in frames), frames
            assert not task.done()
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            release.set()
            if outcome == "delay":
                frames += await task
                assert any(f.get(TOKEN) for f in frames), frames
        release.set()
        # Barrier on the same thread-sensitive executor waits for an uncancellable
        # storage worker after task cancellation before closing this session.
        fresh = await sync_to_async(make_request)(request.session.session_key)
        stored = await sync_to_async(
            lambda: server_state_adapter(DeferredExplicit(), fresh).load()
        )()
        assert stored["count"] == (0 if outcome == "failure" else 1)
        if outcome == "cancel":
            assert not drain(session)
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.close()


@pytest.mark.django_db
@pytest.mark.parametrize("control", ["disabled", "not_opted_in", "capture_failure"])
def test_http_snapshot_respects_opt_in_and_revokes_stale_token(monkeypatch, control):
    request = make_request()
    if control == "not_opted_in":
        monkeypatch.setattr(DeferredLegacy, "enable_state_snapshot", False)
    assert DeferredLegacy.as_view()(request).status_code == 200
    if control == "capture_failure":

        def fail_capture(self, **kwargs):
            raise ValueError("uncapturable state")

        monkeypatch.setattr(DeferredLegacy, "_capture_snapshot_state", fail_capture)
    post = RequestFactory().post(
        request.path,
        json.dumps({"event": "change", "params": {}}),
        content_type="application/json",
    )
    post.session, post.user = request.session, request.user
    with override_settings(DJUST_STATE_SNAPSHOT_ENABLED=control != "disabled"):
        response = DeferredLegacy.as_view()(post)
    assert response.status_code == 200
    body = json.loads(response.content)
    if control == "capture_failure":
        assert TOKEN in body and body[TOKEN] is None
    else:
        assert TOKEN not in body


@pytest.mark.django_db
@pytest.mark.parametrize("cookie", [None, "expired-session-key"])
@pytest.mark.parametrize("backend", ["db", "signed_cookies"])
@pytest.mark.parametrize("mode", ["enabled", "disabled", "not_opted_in"])
def test_http_token_binds_cookie_issued_by_session_middleware(cookie, backend, mode, monkeypatch):
    from django.conf import settings
    from django.test import Client
    from django.urls import path

    if mode == "not_opted_in":
        monkeypatch.setattr(DeferredLegacy, "enable_state_snapshot", False)
    with override_settings(
        DJUST_STATE_SNAPSHOT_ENABLED=mode != "disabled",
        SESSION_ENGINE=f"django.contrib.sessions.backends.{backend}",
        ROOT_URLCONF=type(
            "URLs", (), {"urlpatterns": [path("fallback/", DeferredLegacy.as_view())]}
        ),
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ],
    ):
        client = Client()
        if cookie:
            client.cookies[settings.SESSION_COOKIE_NAME] = cookie
        response = client.post(
            "/fallback/",
            json.dumps({"event": "change", "params": {}}),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        body = response.json()
        if mode != "enabled":
            assert TOKEN not in body
            return
        key = client.cookies[settings.SESSION_COOKIE_NAME].value
        if backend == "signed_cookies":
            assert TOKEN in body and body[TOKEN] is None
            return
        state_json = unsign_snapshot(body[TOKEN], f"{MOD}.DeferredLegacy", key)
        assert state_json is not None, "HTTP token must match the response session cookie"
        assert json.loads(state_json)["count"] == 1
        assert unsign_snapshot(body[TOKEN], f"{MOD}.DeferredLegacy", "other-session") is None


@pytest.mark.django_db
def test_http_missing_module_identity_withholds_token(monkeypatch):
    class MissingModuleMeta(type(LiveView)):
        def __getattribute__(cls, name):
            if name == "__module__" and type.__getattribute__(cls, "_hide_module"):
                raise AttributeError("__module__")
            return super().__getattribute__(name)

    class MissingModuleView(DeferredLegacy, metaclass=MissingModuleMeta):
        _hide_module = False

    endpoint = MissingModuleView.as_view()
    request = make_request()
    assert endpoint(request).status_code == 200
    post = RequestFactory().post(
        request.path,
        json.dumps({"event": "change", "params": {}}),
        content_type="application/json",
    )
    post.session, post.user = request.session, request.user
    monkeypatch.setattr(MissingModuleView, "_hide_module", True)
    response = endpoint(post)
    assert response.status_code == 200, response.content
    assert TOKEN not in json.loads(response.content)
