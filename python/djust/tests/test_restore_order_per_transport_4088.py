"""``handle_restore()`` runs at the same point of a mount on every transport (#4088).

A page GET saves the view's state for its tab; the next request on that tab
puts the state back over a new view object. Over the socket and SSE that is the
live mount (``ViewRuntime.dispatch_mount``), over the stateless HTTP fallback a
POST (``mixins/request.py``). Each records the order of the view's hooks here,
and the three must agree:

    [mount()] -> handle_params() -> object permission -> handle_restore()

``mount()`` runs on the live mount; the fallback rebuilds a view whose state was
all saved without it (the 1.3 rule, #4107), so it is left out of the comparison
and only required to come first when it runs.
``handle_restore()`` comes after ``handle_params()`` and the saved state's
re-apply, so it reads from the state the view will render, and after the
object-permission check, so it never reads for a denied object.
"""

from __future__ import annotations

import json
import re

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state

pytestmark = [pytest.mark.django_db(transaction=True)]

ORDER: list[str] = []
EXPECTED = ["handle_params", "object", "handle_restore"]


class Recorded(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ count }}</p></div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        ORDER.append("mount")

    def handle_params(self, params, uri, **kwargs):
        ORDER.append("handle_params")

    def get_object(self):
        ORDER.append("object")
        return object()

    def has_object_permission(self, request, obj):
        return True

    def handle_restore(self, request):
        ORDER.append("handle_restore")

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


URL = "/o/recorded/"
VIEW = "%s.Recorded" % __name__
urlpatterns = [path("o/recorded/", Recorded.as_view())]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
}


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(**SETTINGS):
        yield
    ORDER.clear()


def _page() -> tuple[Client, str]:
    """The page GET: its client (session) and the tab id the page was given."""
    client = Client()
    response = client.get(URL)
    assert response.status_code == 200
    tab = re.search(r'<meta name="djust-tab" content="([^"]+)">', response.content.decode())
    assert tab, "the page names its tab"
    ORDER.clear()
    return client, tab.group(1)


def _fallback() -> list[str]:
    client, tab = _page()
    response = client.post(
        URL,
        data="{}",
        content_type="application/json",
        HTTP_X_DJUST_EVENT="increment",
        HTTP_X_DJUST_TAB=tab,
    )
    assert response.status_code == 200, response.content[:300]
    return list(ORDER)


async def _socket() -> list[str]:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    client, tab = await sync_to_async(_page)()
    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    comm.scope["session"] = client.session
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.send_json_to(
        {
            "type": "mount",
            "view": VIEW,
            "url": URL,
            "params": {},
            "tab": tab,
            "has_prerendered": True,
        }
    )
    for _ in range(20):
        frame = json.loads(await comm.receive_from(timeout=30))
        if frame.get("type") in ("mount", "error"):
            break
    assert frame["type"] == "mount", frame
    await comm.disconnect()
    return list(ORDER)


async def _sse() -> list[str]:
    from djust.sse import DjustSSEStreamView, _sse_sessions

    client, tab = await sync_to_async(_page)()
    sid = "40884088-4088-4088-4088-408840884088"
    _sse_sessions.pop(sid, None)
    request = RequestFactory().get(
        "/djust/sse/%s/" % sid,
        {"view": VIEW, "_djust_url": URL, "_djust_tab": tab},
        HTTP_ORIGIN="http://testserver",
        HTTP_HOST="testserver",
    )
    request.session = client.session
    request.user = AnonymousUser()
    await DjustSSEStreamView().get(request, session_id=sid)
    session = _sse_sessions.pop(sid, None)
    assert session is not None and session.runtime.view_instance is not None, "SSE mount failed"
    session.shutdown()
    return list(ORDER)


def _after_setup(order: list[str]) -> list[str]:
    """The order without ``mount()``, which must come first when it ran."""
    if "mount" in order:
        assert order.index("mount") == 0 and order.count("mount") == 1, order
    return [step for step in order if step != "mount"]


def test_the_fallback_runs_handle_restore_after_handle_params_and_the_object_check():
    assert _after_setup(_fallback()) == EXPECTED


@pytest.mark.asyncio
async def test_the_socket_mount_runs_the_hooks_in_the_fallbacks_order():
    assert await _socket() == ["mount", *EXPECTED]


@pytest.mark.asyncio
async def test_the_sse_mount_runs_the_hooks_in_the_fallbacks_order():
    assert await _sse() == ["mount", *EXPECTED]


@pytest.mark.asyncio
async def test_every_transport_records_the_same_order():
    orders = {
        "fallback": await sync_to_async(_fallback)(),
        "socket": await _socket(),
        "sse": await _sse(),
    }
    assert len({tuple(_after_setup(order)) for order in orders.values()}) == 1, orders
