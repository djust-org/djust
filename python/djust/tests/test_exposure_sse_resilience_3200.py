"""#3200/#3201 review round 2: the SSE transport, through the real SSE views.

Two SSE-only behaviours the WebSocket tests cannot reach:

- A background turn (``start_async``) outlives the event POST that queued it.
  In production that POST runs behind an ``async_to_sync`` bridge, so the task
  inherited the POST's asgiref executors, which die when the POST returns;
  every later ``sync_to_async`` raised "CurrentThreadExecutor already quit".
  The E5 browser matrix recorded that as a known failure (#3097). The bridge is
  reproduced here by running the POST view inside ``async_to_sync`` from a
  worker thread, as a sync middleware stack does.
- The SSE mount runs on the real stream request, so a replacement session for
  a vanished cookie is issued as a cookie by ``SessionMiddleware`` and must
  keep Django's default lifetime (review I-b).
"""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user, get_user_model, login
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler
from djust.decorators import state
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = pytest.mark.django_db(transaction=True)


class BackgroundPage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span id='n'>{{ count }}</span></div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def increment(self):
        self.count += 1

    @event_handler()
    def spawn(self):
        self.start_async(self._work)

    def _work(self):
        return 10

    def handle_async_result(self, name, result=None, error=None):
        self.count += result


urlpatterns = [path("bg/", BackgroundPage.as_view())]


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust"], DEBUG=False):
        yield


def _request(method, url, body, key):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _fresh_key():
    session = SessionStore()
    session.create()
    return session.session_key


async def _start(key):
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": __name__ + ".BackgroundPage", "_djust_url": "/bg/"},
        key,
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    return _sse_sessions[sid]


def _drain(session):
    frames = []
    while not session.queue.empty():
        frame = session.queue.get_nowait()
        if frame is not None:
            frames.append(frame)
    return frames


def _post_through_a_sync_bridge(session, key, body):
    """Run the POST view the way a sync middleware stack does: in a worker
    thread, through ``async_to_sync``. Tasks it spawns inherit that bridge."""
    request = _request("POST", f"/djust/sse/{session.session_id}/message/", body, key)
    return async_to_sync(DjustSSEMessageView().post)(request, session_id=session.session_id)


async def test_sse_background_result_renders_after_the_post_returns():
    key = await sync_to_async(_fresh_key)()
    session = await _start(key)
    try:
        _drain(session)
        response = await sync_to_async(_post_through_a_sync_bridge)(
            session, key, {"type": "event", "event": "spawn", "params": {}}
        )
        assert response.status_code == 200
        frames = []
        for _ in range(500):
            frames.extend(_drain(session))
            if any(
                f.get("source") == "async" and f.get("type") in {"patch", "html_update"}
                for f in frames
            ) or any(f.get("type") == "error" for f in frames):
                break
            await asyncio.sleep(0.01)
        errors = [f for f in frames if f.get("type") == "error"]
        assert not errors, errors
        assert session.view_instance.count == 10
        assert any(
            f.get("source") == "async" and f.get("type") in {"patch", "html_update"} for f in frames
        ), frames
    finally:
        _sse_sessions.pop(session.session_id, None)


async def test_sse_shaped_mount_replaces_a_vanished_session_with_the_default_lifetime(settings):
    """Review I-b at the runtime: a mount on the REAL request (no scope, as SSE).

    The replacement is created in place on the request's own session, so
    ``SessionMiddleware`` would issue it as a cookie, and it keeps Django's
    default lifetime rather than the short one a WebSocket replacement gets.
    """
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    def dead_request():
        dead = SessionStore()
        dead.create()
        key = dead.session_key
        dead.delete()
        return _request("GET", "/bg/", {}, key), key

    request, key = await sync_to_async(dead_request)()
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    assert runtime.scope is None
    await runtime.dispatch_mount(
        {"type": "mount", "view": __name__ + ".BackgroundPage", "url": "/bg/"}
    )
    assert any(f.get("type") == "mount" for f in transport.sent), transport.sent
    replacement = request.session.session_key
    assert replacement and replacement != key
    assert request.session.modified, "SessionMiddleware must issue it as a cookie"
    assert request.session.get_expiry_age() == settings.SESSION_COOKIE_AGE


async def test_sse_stream_with_a_vanished_cookie_issues_a_default_lifetime_session(settings):
    """Review I-b end to end, through SessionMiddleware and the real stream view.

    The stream GET binds its owner before mounting, and that already replaces
    a vanished session (``sse.py`` owner binding saves a fresh one), so the
    browser gets a default-lifetime cookie, events work with it, and a later
    login keeps the default lifetime.
    """
    from djust._exposure_sessions import server_state_adapter

    def dead_key():
        dead = SessionStore()
        dead.create()
        key = dead.session_key
        dead.delete()
        return key

    key = await sync_to_async(dead_key)()
    sid = str(uuid.uuid4())
    request = RequestFactory().get(
        f"/djust/sse/{sid}/", {"view": __name__ + ".BackgroundPage", "_djust_url": "/bg/"}
    )
    request.COOKIES[settings.SESSION_COOKIE_NAME] = key
    request.tenant = None

    async def stream(req):
        req.user = SimpleLazyObject(lambda: get_user(req))
        return await DjustSSEStreamView().get(req, session_id=sid)

    try:
        response = await SessionMiddleware(stream)(request)
        assert response.status_code == 200
        cookie = response.cookies.get(settings.SESSION_COOKIE_NAME)
        assert cookie is not None, "the SSE stream must issue the replacement session"
        replacement = cookie.value
        assert replacement and replacement != key
        assert int(cookie["max-age"]) == settings.SESSION_COOKIE_AGE

        # The replacement works for SSE events, which arrive with that cookie.
        session = _sse_sessions[sid]
        _drain(session)
        post = await sync_to_async(_request)(
            "POST",
            f"/djust/sse/{sid}/message/",
            {"type": "event", "event": "increment", "params": {}},
            replacement,
        )
        assert (await DjustSSEMessageView().post(post, session_id=sid)).status_code == 200
        frames = _drain(session)
        assert not [f for f in frames if f.get("type") == "error"], frames
        fresh = await sync_to_async(_request)("GET", "/bg/", {}, replacement)
        adapter = await sync_to_async(server_state_adapter)(BackgroundPage(), fresh)
        assert (await adapter.aload())["count"] == 1
    finally:
        _sse_sessions.pop(sid, None)

    # A later login with that cookie keeps Django's default lifetime.
    def log_in():
        user = get_user_model().objects.create_user(username="later", password="pw")
        req = RequestFactory().post("/login/")
        req.COOKIES[settings.SESSION_COOKIE_NAME] = replacement

        def view(r):
            login(r, user, backend="django.contrib.auth.backends.ModelBackend")
            return HttpResponse("ok")

        resp = SessionMiddleware(view)(req)
        return req.session.get_expiry_age(), int(
            resp.cookies[settings.SESSION_COOKIE_NAME]["max-age"]
        )

    age, max_age = await sync_to_async(log_in)()
    assert age == settings.SESSION_COOKIE_AGE
    assert max_age == settings.SESSION_COOKIE_AGE
