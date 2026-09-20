"""Real SSE endpoints: fresh route replacement and explicit snapshot restore."""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler
from djust.decorators import state
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = pytest.mark.django_db(transaction=True)


class FirstPage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ navigation }}</div>"
    navigation = state("initial", persist="client", client=True)

    def mount(self, request, pk=0, **kwargs):
        self.seen_path = request.path
        self.seen_pk = pk
        self._cleaned = False

    def get_context_data(self, **kwargs):
        return super().get_context_data(navigation=self.navigation, **kwargs)

    @event_handler()
    def change(self):
        self.navigation = "latest"

    def _cleanup_uploads(self):
        self._cleaned = True


class SecondPage(FirstPage):
    navigation = state("second", persist="client", client=True)

    def check_permissions(self, request):
        return request.META.get("HTTP_X_ACCESS") == "allow"


urlpatterns = [
    path("first/<int:pk>/", FirstPage.as_view()),
    path("second/<int:pk>/", SecondPage.as_view()),
]


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust"], DEBUG=False):
        yield


def request_for(method, url, body, key=None, access="allow"):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    request.session = SessionStore(request.session.session_key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    request.META["HTTP_X_ACCESS"] = access
    return request


async def start():
    sid = str(uuid.uuid4())
    request = await sync_to_async(request_for)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": __name__ + ".FirstPage", "_djust_url": "/first/1/"},
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]
    return session, request.session.session_key


def drain(session):
    frames = []
    while not session.queue.empty():
        frame = session.queue.get_nowait()
        if frame is not None:
            frames.append(frame)
    return frames


async def post(session, key, body, access="allow"):
    request = await sync_to_async(request_for)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key, access
    )
    return await DjustSSEMessageView().post(request, session_id=session.session_id)


async def test_initial_sse_mount_uses_page_route_not_endpoint():
    session, _ = await start()
    try:
        view = session.view_instance
        assert view.seen_path == "/first/1/"
        assert view.seen_pk == 1
        assert "_djust_url" not in view.request.GET
        assert "view" not in view.request.GET
    finally:
        _sse_sessions.pop(session.session_id, None)


async def test_sse_replace_and_back_restore_through_real_endpoints():
    session, key = await start()
    try:
        drain(session)
        assert (
            await post(session, key, {"type": "event", "event": "change", "params": {}})
        ).status_code == 200
        token = next(
            frame["state_snapshot_signed"]
            for frame in drain(session)
            if frame.get("state_snapshot_signed")
        )
        old_runtime, old_view = session.runtime, session.view_instance
        assert (
            await post(
                session,
                key,
                {
                    "type": "live_redirect_mount",
                    "view": "untrusted.Wrong",
                    "url": "/second/2/",
                    "params": {},
                },
            )
        ).status_code == 200
        assert type(session.view_instance) is SecondPage
        assert session.view_instance.seen_path == "/second/2/"
        assert session.view_instance.seen_pk == 2
        assert old_runtime.view_instance is None
        assert old_view._cleaned is True
        assert any(frame.get("type") == "mount" for frame in drain(session))

        await post(
            session,
            key,
            {
                "type": "live_redirect_mount",
                "view": __name__ + ".FirstPage",
                "url": "/first/1/",
                "params": {},
                "state_snapshot": {"view_slug": __name__ + ".FirstPage", "state_json": token},
            },
        )
        assert type(session.view_instance) is FirstPage
        assert session.view_instance.navigation == "latest"
        assert session.view_instance.seen_pk == 1
        assert session._event_request is None
        restored_frame = next(frame for frame in drain(session) if frame.get("type") == "mount")
        assert "latest" in restored_frame["html"]
    finally:
        _sse_sessions.pop(session.session_id, None)


async def test_replacement_rechecks_current_post_permissions():
    session, key = await start()
    try:
        old_runtime = session.runtime
        drain(session)
        await post(
            session,
            key,
            {
                "type": "live_redirect_mount",
                "view": __name__ + ".SecondPage",
                "url": "/second/2/",
                "params": {},
            },
            access="denied",
        )
        frames = drain(session)
        assert any(frame.get("type") in {"error", "navigate"} for frame in frames), frames
        assert not any(frame.get("type") == "mount" for frame in frames)
        assert old_runtime.view_instance is None
        assert session.runtime.view_instance is None
        assert session.view_instance is None
        assert session.active is False
    finally:
        _sse_sessions.pop(session.session_id, None)


async def test_wrong_owner_cannot_replace_view():
    session, _ = await start()
    try:
        old_view = session.view_instance
        response = await post(
            session,
            None,
            {
                "type": "live_redirect_mount",
                "url": "/second/2/",
                "params": {},
            },
        )
        assert response.status_code == 403
        assert session.view_instance is old_view
    finally:
        _sse_sessions.pop(session.session_id, None)


async def test_old_background_result_cannot_reach_replacement():
    session, key = await start()
    try:
        old_runtime = session.runtime
        started, release = asyncio.Event(), asyncio.Event()

        async def callback():
            started.set()
            await release.wait()
            return "old-result"

        task = asyncio.create_task(old_runtime._execute_async_task("old", callback, (), {}, None))
        await started.wait()
        await post(
            session,
            key,
            {
                "type": "live_redirect_mount",
                "url": "/second/2/",
                "params": {},
            },
        )
        drain(session)
        release.set()
        await task
        assert type(session.view_instance) is SecondPage
        assert session.view_instance.navigation == "second"
        assert drain(session) == []
    finally:
        _sse_sessions.pop(session.session_id, None)


@pytest.mark.parametrize("url", ["https://example.org/second/2/", "/%2e%2e/second/2/", "/missing/"])
async def test_invalid_destination_does_not_replace_current_view(url):
    session, key = await start()
    try:
        old_view = session.view_instance
        drain(session)
        await post(session, key, {"type": "live_redirect_mount", "url": url, "params": {}})
        assert session.view_instance is old_view
        assert old_view._cleaned is False
        assert any(frame.get("type") == "error" for frame in drain(session))
    finally:
        _sse_sessions.pop(session.session_id, None)


async def test_replacement_shares_rate_limit_with_events():
    from djust.rate_limit import ConnectionRateLimiter

    session, key = await start()
    try:
        old_view = session.view_instance
        session._rate_limiter = ConnectionRateLimiter(rate=0, burst=0)
        drain(session)
        await post(session, key, {"type": "live_redirect_mount", "url": "/second/2/"})
        assert session.view_instance is old_view
        assert any(frame.get("code") == "rate_limited" for frame in drain(session))
    finally:
        _sse_sessions.pop(session.session_id, None)


async def test_navigation_waits_for_inflight_event_without_replacing_its_request(monkeypatch):
    session, key = await start()
    started, release = asyncio.Event(), asyncio.Event()
    old_view = session.view_instance

    @event_handler()
    async def slow_change(self):
        original_request = self.request
        started.set()
        await release.wait()
        assert self.request is original_request
        self.navigation = "finished"

    monkeypatch.setattr(FirstPage, "change", slow_change)
    event_request = await sync_to_async(request_for)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "event", "event": "change", "params": {}},
        key,
    )
    nav_request = await sync_to_async(request_for)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "live_redirect_mount", "url": "/second/2/"},
        key,
        "denied",
    )
    event_task = asyncio.create_task(DjustSSEMessageView().post(event_request, session.session_id))
    try:
        await asyncio.wait_for(started.wait(), 2)
        nav_task = asyncio.create_task(DjustSSEMessageView().post(nav_request, session.session_id))
        await asyncio.sleep(0)
        assert not nav_task.done()
        assert session.view_instance is old_view
        release.set()
        await asyncio.wait_for(asyncio.gather(event_task, nav_task), 2)
        assert old_view.navigation == "finished"
        assert session.view_instance is None  # fresh navigation request denied
        assert session._event_request is None
    finally:
        release.set()
        _sse_sessions.pop(session.session_id, None)
