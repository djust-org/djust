"""A session that no longer exists mounts fresh, on every transport (#4102).

The browser keeps a session cookie whose session the store has lost: an
expired or flushed session, ``clearsessions``, or (with a process-local cache)
a session another process holds. The next request must not answer 500: it
mounts fresh from ``mount()``, as a page load would.
"""

from __future__ import annotations

import json

import pytest
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust._automatic_state import TAB_HEADER
from djust.decorators import event_handler, state
from djust.tests.http_fallback_matrix import PAGE

pytestmark = [pytest.mark.django_db(transaction=True)]


class LostSessionCounter(LiveView):
    exposure_policy = "explicit"
    template = PAGE % '<p id="n">{{ count }}</p><p id="m">{{ mounted }}</p>'
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.mounted = "MOUNTED"

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, mounted=self.mounted, **kwargs)

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


urlpatterns = [path("lost/", LostSessionCounter.as_view())]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__],
    "SESSION_ENGINE": "django.contrib.sessions.backends.db",
}


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(**SETTINGS):
        yield


def _tab(document: str) -> str:
    import re

    found = re.search(r'name="djust-tab" content="([^"]+)"', document) or re.search(
        r'data-djust-tab="([^"]+)"', document
    )
    return found.group(1) if found else ""


def _post(client: Client, tab: str):
    return client.post(
        "/lost/",
        data=json.dumps({}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="increment",
        **{"HTTP_" + TAB_HEADER.upper().replace("-", "_"): tab},
    )


def _count(answer) -> str:
    import re

    payload = answer.json()
    html = payload.get("html") or json.dumps(payload)
    found = re.search(r'id=\\?"n\\?">(\d+)<', html)
    return found.group(1) if found else html


def test_http_fallback_mounts_fresh_when_the_session_is_gone():
    client = Client()
    page = client.get("/lost/")
    assert page.status_code == 200
    tab = _tab(page.content.decode())
    first = _post(client, tab)
    assert first.status_code == 200, first.content
    assert _count(first) == "1"

    SessionStore(client.cookies[settings.SESSION_COOKIE_NAME].value).delete()

    second = _post(client, tab)
    assert second.status_code == 200, second.content
    # Mounted fresh: nothing of the lost session came back.
    assert _count(second) == "1"


async def _socket(cookie: str):
    from channels.auth import AuthMiddlewareStack
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(
        AuthMiddlewareStack(LiveViewConsumer.as_asgi()),
        "/ws/",
        headers=[(b"cookie", ("%s=%s" % (settings.SESSION_COOKIE_NAME, cookie)).encode())],
    )
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    return comm


@pytest.mark.asyncio
async def test_websocket_mount_mounts_fresh_when_the_session_is_gone():
    from asgiref.sync import sync_to_async

    client = Client()
    page = await sync_to_async(client.get)("/lost/")
    tab = _tab(page.content.decode())
    first = await sync_to_async(_post)(client, tab)
    assert _count(first) == "1"
    key = client.cookies[settings.SESSION_COOKIE_NAME].value
    await sync_to_async(SessionStore(key).delete)()

    comm = await _socket(key)
    try:
        await comm.send_json_to(
            {
                "type": "mount",
                "view": __name__ + ".LostSessionCounter",
                "url": "/lost/",
                "tab": tab,
            }
        )
        frame = await comm.receive_json_from()
        assert frame["type"] == "mount", frame
        assert 'id="n">0<' in frame["html"] and "MOUNTED" in frame["html"], frame["html"]
        await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
        reply = await comm.receive_json_from()
        assert reply["type"] in {"patch", "html_update"}, reply
    finally:
        await comm.disconnect()


@pytest.mark.asyncio
async def test_sse_stream_mounts_fresh_when_the_session_is_gone_and_was_already_read():
    """The SSE stream request, after a middleware already resolved its user
    (which reads the session, and so clears the vanished key)."""
    import uuid

    from asgiref.sync import sync_to_async
    from django.contrib.auth import get_user
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    from djust.sse import DjustSSEStreamView, _sse_sessions

    client = Client()
    page = await sync_to_async(client.get)("/lost/")
    tab = _tab(page.content.decode())
    assert _count(await sync_to_async(_post)(client, tab)) == "1"
    key = client.cookies[settings.SESSION_COOKIE_NAME].value
    await sync_to_async(SessionStore(key).delete)()

    sid = str(uuid.uuid4())
    request = RequestFactory().get(
        f"/djust/sse/{sid}/",
        {"view": __name__ + ".LostSessionCounter", "_djust_url": "/lost/", "_djust_tab": tab},
    )
    request.COOKIES[settings.SESSION_COOKIE_NAME] = key
    request.tenant = None

    async def stream(req):
        req.user = await sync_to_async(get_user)(req)  # reads the session now
        return await DjustSSEStreamView().get(req, session_id=sid)

    try:
        response = await SessionMiddleware(stream)(request)
        assert response.status_code == 200
        frames = []
        session = _sse_sessions[sid]
        while not session.queue.empty():
            frame = session.queue.get_nowait()
            if frame is not None:
                frames.append(frame)
        mounts = [f for f in frames if f.get("type") == "mount"]
        assert mounts, frames
        assert 'id="n">0<' in mounts[0]["html"], mounts[0]["html"]
    finally:
        _sse_sessions.pop(sid, None)


@pytest.mark.asyncio
async def test_sse_event_after_the_session_is_gone_is_refused_not_a_500():
    """Between two events on an open stream the lost session is a revocation
    (as a logout is): the event is refused with an error frame, and the next
    page load mounts fresh. It is never a 500."""
    from asgiref.sync import sync_to_async

    from djust.sse import _sse_sessions
    from djust.tests.test_exposure_sse_resilience_3200 import (
        _drain,
        _fresh_key,
        _post_through_a_sync_bridge,
        _start,
    )

    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=["djust"],
        ROOT_URLCONF="djust.tests.test_exposure_sse_resilience_3200",
    ):
        key = await sync_to_async(_fresh_key)()
        session = await _start(key)
        try:
            _drain(session)
            body = {"type": "event", "event": "increment", "params": {}}
            ok = await sync_to_async(_post_through_a_sync_bridge)(session, key, body)
            assert ok.status_code == 200
            assert not [f for f in _drain(session) if f.get("type") == "error"]
            await sync_to_async(SessionStore(key).delete)()
            refused = await sync_to_async(_post_through_a_sync_bridge)(session, key, body)
            assert refused.status_code < 500, refused.status_code
            frames = _drain(session)
            assert [f for f in frames if f.get("type") == "error"], frames
        finally:
            _sse_sessions.pop(session.session_id, None)
