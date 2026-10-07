"""#3252 / #3104 over SSE: an embedded child's event reaches the child, and a
frame addressed to a view that is not mounted is refused.

A ``{% live_render %}`` child's event runs on the child over SSE (the shared
runtime routes by ``view_id``); this pins it as the counterpart of the HTTP
routing (#3104). A frame that names a ``target_id`` with no view mounted there
is refused with ``code: "view_unavailable"``, because answering it with the
page view would run the event on the wrong view. The views that ARE mounted
beside the page view are pinned in ``test_sse_view_slots_3252.py``.

Driven through the real stream and message views, as the browser does.
"""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MOD = __name__
RECEIVED: list = []


class Kid(LiveView):
    exposure_policy = "legacy"
    template = '<div><p>kid <span id="r">{{ got }}</span></p></div>'

    def mount(self, request, **kwargs):
        self.got = ""

    @event_handler()
    def click(self, **kwargs):
        self.got = "clicked"
        RECEIVED.append("kid")


class Page(LiveView):
    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Page"><p>page</p>'
        '{% live_render "' + MOD + '.Kid" view_id="kid" %}</div>'
    )

    def mount(self, request, **kwargs):
        self.got = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context

    @event_handler()
    def click(self, **kwargs):
        RECEIVED.append("page")


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    RECEIVED.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", __name__], DEBUG=False
    ):
        yield
    _sse_sessions.clear()


urlpatterns: list = []


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


async def _frame(stream, *types, timeout=10.0):
    """The next frame of one of ``types`` on the stream (keepalives skipped)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "no frame of type %r" % (types,)
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
        if not chunk.startswith("data:"):
            continue
        frame = json.loads(chunk[len("data:") :])
        if frame.get("type") in types:
            return frame


async def _open():
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + ".Page", "_djust_url": "/page/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert (await _frame(stream, "sse_connect"))["type"] == "sse_connect"
    await _frame(stream, "mount")
    return _sse_sessions[sid], key, stream


async def _post(session, key, body):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200
    return response


async def test_a_childs_event_runs_on_the_child_over_sse():
    session, key, stream = await _open()
    try:
        await _post(
            session,
            key,
            {"type": "event", "event": "click", "params": {"view_id": "kid"}, "ref": 4},
        )
        frame = await _frame(stream, "embedded_update", "error", "patch", "html_update")
        assert frame["type"] == "embedded_update"
        assert frame["view_id"] == "kid"
        assert "clicked" in frame["html"]
        assert RECEIVED == ["kid"]
    finally:
        await stream.aclose()


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "event", "event": "click", "params": {}, "ref": 7, "target_id": "lazy-1"},
        {"type": "url_change", "params": {}, "uri": "/page/", "target_id": "lazy-1"},
        {"type": "request_html", "target_id": "lazy-1"},
    ],
)
async def test_a_frame_for_an_address_with_no_view_is_refused_not_run_on_the_page(frame):
    """Nothing is mounted at ``lazy-1``: the frame is refused (``view_unavailable``),
    never answered by the page view, which would run the event on the wrong view."""
    session, key, stream = await _open()
    try:
        page = session.view_instance
        await _post(session, key, frame)
        reply = await _frame(stream, "error", "patch", "html_update", "noop")
        assert reply["type"] == "error"
        assert reply["code"] == "view_unavailable"
        if "ref" in frame:
            assert reply["ref"] == frame["ref"]
        assert RECEIVED == []
        # The session still hosts the page view, untouched.
        assert session.view_instance is page
        await _post(session, key, {"type": "event", "event": "click", "params": {}})
        await _frame(stream, "patch", "html_update", "noop")
        assert RECEIVED == ["page"]
    finally:
        await stream.aclose()
