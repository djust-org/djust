"""An explicit event sees the page's query string on every transport (#4231).

An explicit view's event is authorized against a fresh request bound to the
mount (``_exposure_auth._event_request``): the route comes from the mount, the
principal and session from the current request. Over the WebSocket that fresh
request is a copy of the mount request (``fresh_socket_request``), so it keeps
the page's query; the HTTP fallback POSTs to the page URL itself. Over SSE the
current request is the ``/djust/sse/<id>/message/`` POST, which has no page
query, and only the path and resolver match were copied from the mount. A view
that resolves state from the query (here ``resolve_tenant`` reading
``?tenant=``) resolved a different tenant on the event than on the mount, the
binding no longer matched, and the first SSE event was refused with "Event
authorization failed".

Each transport mounts ``/o/qtenant/?tenant=other`` and sends one event; the
handler records the tenant and query it ran under. The three must agree.
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state
from djust.tenants.mixin import TenantMixin
from djust.tenants.resolvers import TenantInfo

pytestmark = [pytest.mark.django_db(transaction=True)]

SEEN: list[tuple[str, str]] = []


class QueryTenantView(TenantMixin, LiveView):
    exposure_policy = "explicit"
    tenant_required = False
    template = "<div dj-root><p>{{ count }}</p></div>"
    count = state(0, persist="server")

    def resolve_tenant(self, request):
        tenant = request.GET.get("tenant")
        return TenantInfo(tenant) if tenant else None

    @event_handler
    def bump(self, **kwargs):
        tenant = self.tenant
        SEEN.append((getattr(tenant, "id", "none"), self.request.GET.get("tenant", "none")))
        self.count += 1


PATH = "/o/qtenant/"
URL = PATH + "?tenant=other"
VIEW = "%s.QueryTenantView" % __name__
ORIGIN = "http://testserver"
urlpatterns = [path("o/qtenant/", QueryTenantView.as_view())]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
}

EXPECTED = [("other", "other")]


@pytest.fixture(autouse=True)
def _routes():
    SEEN.clear()
    with override_settings(**SETTINGS):
        yield
    SEEN.clear()


def _page() -> Client:
    """The page GET: its client (session). (1.3 has no per-tab id, #4107 is 2.0-only.)"""
    client = Client()
    response = client.get(URL)
    assert response.status_code == 200, response.content[:300]
    return client


def _fallback() -> list[tuple[str, str]]:
    client = _page()
    response = client.post(
        URL,
        data="{}",
        content_type="application/json",
        HTTP_X_DJUST_EVENT="bump",
    )
    assert response.status_code == 200, response.content[:300]
    return list(SEEN)


async def _socket() -> list[tuple[str, str]]:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    client = await sync_to_async(_page)()
    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", ORIGIN.encode())],
    )
    comm.scope["session"] = client.session
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    try:
        await comm.send_json_to(
            {
                "type": "mount",
                "view": VIEW,
                # The client sends the pathname and the page query as params.
                "url": PATH,
                "params": {"tenant": "other"},
                "has_prerendered": True,
            }
        )
        for _ in range(20):
            frame = json.loads(await comm.receive_from(timeout=30))
            if frame.get("type") in ("mount", "error"):
                break
        assert frame["type"] == "mount", frame
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}})
        for _ in range(20):
            frame = json.loads(await comm.receive_from(timeout=30))
            if frame.get("type") in ("patch", "html_update", "error"):
                break
        assert frame["type"] != "error", frame
    finally:
        await comm.disconnect()
    return list(SEEN)


async def _sse(message_query: str = "", expect_error: bool = False) -> list[tuple[str, str]]:
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    client = await sync_to_async(_page)()
    session_store = client.session
    sid = "42314231-4231-4231-4231-423142314231"
    _sse_sessions.pop(sid, None)
    stream = RequestFactory().get(
        "/djust/sse/%s/" % sid,
        # The client's stream URL: the pathname plus the page query (03b-sse.js).
        {"view": VIEW, "_djust_url": PATH, "tenant": "other"},
        HTTP_ORIGIN=ORIGIN,
        HTTP_HOST="testserver",
    )
    stream.session = session_store
    stream.user = AnonymousUser()
    await DjustSSEStreamView().get(stream, session_id=sid)
    session = _sse_sessions.get(sid)
    try:
        assert session is not None and session.runtime.view_instance is not None, "SSE mount failed"
        while not session.queue.empty():
            session.queue.get_nowait()
        post = RequestFactory().post(
            "/djust/sse/%s/message/%s" % (sid, message_query),
            data=json.dumps({"type": "event", "event": "bump", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_HOST="testserver",
        )
        post.session = session_store
        post.user = AnonymousUser()
        response = await DjustSSEMessageView().post(post, session_id=sid)
        assert response.status_code == 200, response.content[:300]
        frames = []
        while not session.queue.empty():
            frame = session.queue.get_nowait()
            if frame is not None:
                frames.append(frame)
        errors = [f for f in frames if isinstance(f, dict) and f.get("type") == "error"]
        if expect_error:
            assert [e.get("code") for e in errors] == ["permission_denied"], frames
        else:
            assert not errors, errors
        # The live POST and the stored mount request keep their own query.
        assert post.META.get("QUERY_STRING", "") == message_query.lstrip("?")
        assert session._request.GET.get("tenant") == "other"
    finally:
        session = _sse_sessions.pop(sid, None)
        if session is not None:
            session.shutdown()
    return list(SEEN)


def test_the_fallback_event_sees_the_page_query():
    assert _fallback() == EXPECTED


@pytest.mark.asyncio
async def test_the_socket_event_sees_the_page_query():
    assert await _socket() == EXPECTED


@pytest.mark.asyncio
async def test_the_sse_event_sees_the_mount_query():
    assert await _sse() == EXPECTED


@pytest.mark.asyncio
async def test_every_transport_gives_the_event_the_same_query():
    results = {}
    for name, run in (
        ("fallback", sync_to_async(_fallback)),
        ("socket", _socket),
        ("sse", _sse),
    ):
        SEEN.clear()
        try:
            results[name] = await run()
        except AssertionError as exc:
            results[name] = "refused: %s" % str(exc)[:120]
    assert len({repr(r) for r in results.values()}) == 1, results


@pytest.mark.asyncio
async def test_the_sse_message_post_query_cannot_steer_authorization():
    """Only the mount's query counts; one appended to the message POST is ignored."""
    assert await _sse(message_query="?tenant=attacker") == EXPECTED


@pytest.mark.asyncio
async def test_the_sse_binding_still_refuses_a_changed_tenant(monkeypatch):
    """Copying the query removes a false mismatch; a real one is still refused."""
    calls = []
    original = QueryTenantView.resolve_tenant

    def resolve(self, request):
        calls.append(request.path)
        if len(calls) > 1 and getattr(request, "method", "") == "POST":
            return TenantInfo("someone-else")
        return original(self, request)

    monkeypatch.setattr(QueryTenantView, "resolve_tenant", resolve)
    assert await _sse(expect_error=True) == []
