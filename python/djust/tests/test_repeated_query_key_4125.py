"""A repeated query key reaches ``handle_params()`` whole, on every transport (#4125).

For ``?a=1&a=2&b=x`` the page GET and the fallback POST hand ``handle_params()``
``{"a": ["1", "2"], "b": "x"}`` (``mixins/request.py::_http_url_params``). The
socket and SSE mounts must hand it the same: the client sends every value of a
repeated key (``djustQueryParams`` in ``00-namespace.js``, pinned by
``tests/js/query_params_repeated_key_4125.test.js``), the SSE stream view keeps
them, and the request the runtime builds encodes them as repeated keys. URL
comparison for saved state counts every value too.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state

pytestmark = [pytest.mark.django_db(transaction=True)]

SEEN: list[tuple[str, Any, str]] = []
CHANGED: list[bool] = []

QUERY = "a=1&a=2&b=x"
EXPECTED = {"a": ["1", "2"], "b": "x"}


class Tagged(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ count }}</p></div>"
    count = state(0, persist="server")

    def handle_params(self, params, uri, **kwargs):
        SEEN.append(("params", dict(params), uri))
        request = getattr(self, "request", None)
        if request is not None:
            SEEN.append(("request", dict(request.GET.lists()), ""))

    @event_handler
    def probe(self, **kwargs):
        from djust import _automatic_state

        CHANGED.append(_automatic_state.url_changed(self))
        self.count += 1


class IntTagged(LiveView):
    """``mount()`` reads a query key as one value, as 1.3 handed it over."""

    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ n }}</p></div>"
    n = state(0)

    def mount(self, request, tag=None, **kwargs):
        self.n = int(tag) if tag else 0
        SEEN.append(("mount", tag, ""))

    def handle_params(self, params, uri, **kwargs):
        SEEN.append(("params", dict(params), uri))


URL = "/q/tagged/"
VIEW = "%s.Tagged" % __name__
INT_URL = "/q/int-tagged/"
INT_VIEW = "%s.IntTagged" % __name__
urlpatterns = [path("q/tagged/", Tagged.as_view()), path("q/int-tagged/", IntTagged.as_view())]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
}


@pytest.fixture(autouse=True)
def _routes():
    SEEN.clear()
    CHANGED.clear()
    with override_settings(**SETTINGS):
        yield


def _params_seen() -> list[Any]:
    return [value for kind, value, _ in SEEN if kind == "params"]


def _page(query: str = QUERY) -> tuple[Client, str]:
    client = Client()
    response = client.get(URL + "?" + query)
    assert response.status_code == 200
    tab = re.search(r'<meta name="djust-tab" content="([^"]+)">', response.content.decode())
    assert tab
    return client, tab.group(1)


def test_the_page_get_and_the_fallback_post_hand_over_every_value():
    client, tab = _page()
    response = client.post(
        URL + "?" + QUERY,
        data="{}",
        content_type="application/json",
        HTTP_X_DJUST_EVENT="probe",
        HTTP_X_DJUST_TAB=tab,
    )
    assert response.status_code == 200, response.content[:300]
    assert _params_seen() == [EXPECTED, EXPECTED]


@pytest.mark.asyncio
async def test_the_socket_mount_hands_over_every_value():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    client, tab = await sync_to_async(_page)()
    SEEN.clear()
    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    comm.scope["session"] = client.session
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    # The params the client builds for ``?a=1&a=2&b=x`` (``djustQueryParams``).
    await comm.send_json_to(
        {"type": "mount", "view": VIEW, "url": URL, "params": EXPECTED, "tab": tab}
    )
    for _ in range(20):
        frame = json.loads(await comm.receive_from(timeout=30))
        if frame.get("type") in ("mount", "error"):
            break
    assert frame["type"] == "mount", frame
    await comm.disconnect()
    assert _params_seen() == [EXPECTED]
    # The request the runtime built for the view carries both values too.
    params = [entry for entry in SEEN if entry[0] == "params"][0]
    assert params[2] == URL + "?" + QUERY


@pytest.mark.asyncio
async def test_the_sse_mount_hands_over_every_value():
    from djust.sse import DjustSSEStreamView, _sse_sessions

    client, tab = await sync_to_async(_page)()
    SEEN.clear()
    sid = "41254125-4125-4125-4125-412541254125"
    _sse_sessions.pop(sid, None)
    request = RequestFactory().get(
        "/djust/sse/%s/" % sid,
        # The stream URL the client builds: every value of a repeated key.
        {"view": VIEW, "_djust_url": URL, "_djust_tab": tab, **EXPECTED},
        HTTP_ORIGIN="http://testserver",
        HTTP_HOST="testserver",
    )
    request.session = client.session
    request.user = AnonymousUser()
    await DjustSSEStreamView().get(request, session_id=sid)
    session = _sse_sessions.pop(sid, None)
    assert session is not None and session.runtime.view_instance is not None, "SSE mount failed"
    session.shutdown()
    assert _params_seen() == [EXPECTED]


def test_a_change_to_an_earlier_value_of_a_repeated_key_is_a_url_change():
    client, tab = _page()

    def probe(query: str) -> bool:
        response = client.post(
            URL + "?" + query,
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="probe",
            HTTP_X_DJUST_TAB=tab,
        )
        assert response.status_code == 200, response.content[:300]
        return CHANGED[-1]

    assert probe(QUERY) is False  # the URL the state was saved under
    assert probe("a=3&a=2&b=x") is True  # only the first value differs


TAG_PARAMS = {"tag": ["1", "2"]}


async def _socket_mount(url: str, view: str, params: dict) -> dict:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    def page() -> Any:
        client = Client()
        assert client.get(url + "?tag=1&tag=2").status_code == 200
        return client.session

    session = await sync_to_async(page)()
    SEEN.clear()
    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    comm.scope["session"] = session
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.send_json_to({"type": "mount", "view": view, "url": url, "params": params})
    for _ in range(20):
        frame = json.loads(await comm.receive_from(timeout=30))
        if frame.get("type") in ("mount", "error"):
            break
    await comm.disconnect()
    return frame


@pytest.mark.asyncio
async def test_a_socket_mount_hands_mount_the_last_value_and_handle_params_the_list():
    frame = await _socket_mount(INT_URL, INT_VIEW, TAG_PARAMS)
    assert frame["type"] == "mount", frame
    assert ("mount", "2", "") in SEEN
    assert _params_seen() == [TAG_PARAMS]
    assert re.search(r"<p[^>]*>2</p>", frame["html"]), frame["html"]


@pytest.mark.asyncio
async def test_an_sse_mount_hands_mount_the_last_value_and_handle_params_the_list():
    from djust.sse import DjustSSEStreamView, _sse_sessions

    def page() -> Any:
        client = Client()
        assert client.get(INT_URL + "?tag=1&tag=2").status_code == 200
        return client.session

    session_store = await sync_to_async(page)()
    SEEN.clear()
    sid = "41254125-4125-4125-4125-4125412541ff"
    _sse_sessions.pop(sid, None)
    request = RequestFactory().get(
        "/djust/sse/%s/" % sid,
        {"view": INT_VIEW, "_djust_url": INT_URL, **TAG_PARAMS},
        HTTP_ORIGIN="http://testserver",
        HTTP_HOST="testserver",
    )
    request.session = session_store
    request.user = AnonymousUser()
    await DjustSSEStreamView().get(request, session_id=sid)
    session = _sse_sessions.pop(sid, None)
    assert session is not None and session.runtime.view_instance is not None, "SSE mount failed"
    assert session.runtime.view_instance.n == 2
    session.shutdown()
    assert ("mount", "2", "") in SEEN
    assert _params_seen() == [TAG_PARAMS]


def test_the_page_get_hands_handle_params_the_list():
    assert Client().get(INT_URL + "?tag=1&tag=2").status_code == 200
    assert _params_seen() == [TAG_PARAMS]
