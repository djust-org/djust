"""#4174: an explicit attribute-form ``dj-lazy`` view on the page-POST fallback.

``<div dj-view="..." dj-lazy>`` on an explicit page was never registered over
HTTP. Now it is: the explicit child is mounted with the eager
``{% live_render %}`` explicit checks under its lazy address, and keeps exactly
its ``persist="server"`` fields between POSTs (its other state starts again
from ``mount()``).
"""

import json
import re

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler, state

MOD = __name__
TAG = '<div dj-view="' + MOD + '.Child" dj-lazy></div>'


class Child(LiveView):
    exposure_policy = "explicit"
    count = state(0, persist="server")
    clicks = state(0)
    template = (
        '<div><span class="count">{{ count }}</span><span class="clicks">{{ clicks }}</span>'
        '<button dj-click="inc">+</button></div>'
    )

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1
        self.clicks += 1


class Page(LiveView):
    exposure_policy = "explicit"
    template = (
        '<div dj-root><section id="one">'
        + TAG
        + '</section><section id="two">'
        + TAG
        + "</section></div>"
    )


class DeniedChild(Child):
    permission_required = "auth.change_user"


class ObjectDeniedChild(Child):
    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False


def request(method, session, body=None):
    factory = RequestFactory()
    req = (
        factory.get("/lazy/")
        if method == "get"
        else factory.post("/lazy/", json.dumps(body), content_type="application/json")
    )
    req.session = session
    req.user = AnonymousUser()
    req.tenant = None
    return req


def get_ids(session, cls=Page):
    response = cls.as_view()(request("get", session))
    assert response.status_code == 200, response.content
    session.save()
    return re.findall(r'data-djust-lazy-id="([^"]+)"', response.content.decode())


def post(session, view_id, event="inc", cls=Page):
    # A fresh read of the stored session per request, as a browser's would be.
    session = SessionStore(session.session_key)
    response = cls.as_view()(
        request("post", session, {"event": event, "params": {"view_id": view_id}})
    )
    session.save()
    return response


def values(response):
    assert response.status_code == 200, response.content
    data = json.loads(response.content)
    assert data["type"] == "embedded_update"
    html = data["html"]
    count = int(re.search(r'class="count"[^>]*>(\d+)<', html).group(1))
    clicks = int(re.search(r'class="clicks"[^>]*>(\d+)<', html).group(1))
    return data["view_id"], count, clicks


@pytest.fixture(autouse=True)
def _allowed():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust", MOD], DEBUG=False):
        yield


@pytest.fixture
def session():
    store = SessionStore()
    store.create()
    return store


@pytest.mark.django_db
def test_explicit_lazy_child_fills_over_page_post_then_takes_events(session):
    ids = get_ids(session)
    assert len(ids) == 2 and len(set(ids)) == 2
    assert get_ids(SessionStore(session.session_key)) == ids
    a, b = ids
    assert values(post(session, a, "djust_lazy_mount")) == (a, 0, 0)
    assert values(post(session, a)) == (a, 1, 1)
    # count is persist="server"; clicks is not, and starts again from mount().
    assert values(post(session, a)) == (a, 2, 1)
    # The second container is its own view.
    assert values(post(session, b)) == (b, 1, 1)
    assert values(post(session, a)) == (a, 3, 1)


@pytest.mark.django_db
def test_other_session_and_forged_lazy_ids_are_refused(session):
    other = SessionStore()
    other.create()
    ids = get_ids(session)
    assert not set(ids) & set(get_ids(other))
    for value in (ids[0], "lazy_000000000000", MOD + ".Child"):
        response = post(other, value)
        assert response.status_code == 400
        assert json.loads(response.content) == {"error": "Embedded view not found"}


@pytest.mark.django_db
@pytest.mark.parametrize("view", [DeniedChild, ObjectDeniedChild])
def test_denied_explicit_lazy_child_is_not_registered(session, view):
    class DeniedPage(Page):
        template = (
            '<div dj-root><div dj-view="' + MOD + "." + view.__name__ + '" dj-lazy></div></div>'
        )

    ids = get_ids(session, DeniedPage)
    if view is DeniedChild:
        assert ids == []
    else:
        assert len(ids) == 1
        assert post(session, ids[0], "djust_lazy_mount", DeniedPage).status_code == 403


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_explicit_lazy_child_over_websocket():
    from djust.websocket import LiveViewConsumer

    from ._ws_frames import has_frame, receive_until
    from .test_exposure_runtime import make_request

    req = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=req.session, user=req.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    try:
        await socket.send_json_to({"type": "mount", "view": MOD + ".Page", "url": "/lazy/"})
        assert (await socket.receive_json_from())["type"] == "mount"
        await socket.send_json_to(
            {"type": "mount", "view": MOD + ".Child", "url": "/lazy/", "target_id": "c1"}
        )
        frames = await receive_until(socket, lambda fs: any(f.get("type") == "mount" for f in fs))
        mount = [f for f in frames if f.get("type") == "mount"][0]
        assert mount.get("target_id") == "c1"
        await socket.send_json_to(
            {"type": "event", "event": "inc", "target_id": "c1", "params": {}, "ref": 7}
        )
        frames = await receive_until(socket, has_frame(ref=7))
        reply = [f for f in frames if f.get("ref") == 7][-1]
        assert reply.get("target_id") == "c1"
        assert reply.get("type") != "error", reply
    finally:
        await socket.disconnect()
