"""#3236: navigation cancels a replaced legacy view's ``wait_for_event`` waiters.

When navigation replaces a view — ``live_redirect`` on the WebSocket,
``_replace_view`` on SSE — an explicit view is disposed through
``dispose_child_subtree`` (which cancels its waiters), but a legacy view only
had its uploads (and, on SSE, its children) cleaned up. Its waiters were never
cancelled, and they have no default timeout, so the background task blocked on
one, and through it the whole view, stayed alive for the life of the
connection. A disconnect cancels them; navigation now does too.

Both transports are driven through their real entry points: a
``WebsocketCommunicator`` against ``LiveViewConsumer``, and the SSE stream and
message views. The waiter is awaited by the view's own ``start_async`` work,
the realistic shape (a guided tour waiting for the user's next click).
"""

import asyncio
import gc
import json
import uuid
import weakref

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

#: Weak references to every mounted tour view, and the ids of views whose
#: waiting task started, was cancelled, or got an answer.
VIEWS: list = []
WAITING: set = set()
CANCELLED: list = []
ANSWERED: list = []


class TourPage(LiveView):
    """A legacy view whose background work waits for the user's next click."""

    exposure_policy = "legacy"
    template = "<div dj-root><span>tour</span></div>"

    def mount(self, request, **kwargs):
        VIEWS.append(weakref.ref(self))

    @event_handler()
    def start_tour(self, **kwargs):
        self.start_async(self._wait_for_next, name="tour")

    @event_handler()
    def next_step(self, **kwargs):
        pass

    async def _wait_for_next(self):
        WAITING.add(id(self))
        try:
            await self.wait_for_event("next_step")
        except asyncio.CancelledError:
            CANCELLED.append(id(self))
            raise
        finally:
            WAITING.discard(id(self))
        ANSWERED.append(id(self))

    def handle_async_result(self, name, result=None, error=None):
        pass


class OtherPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>other</span></div>"


urlpatterns = [path("tour/", TourPage.as_view()), path("other/", OtherPage.as_view())]

TOUR = __name__ + ".TourPage"
OTHER = __name__ + ".OtherPage"


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    VIEWS.clear()
    WAITING.clear()
    CANCELLED.clear()
    ANSWERED.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", __name__], DEBUG=False
    ):
        yield
    _sse_sessions.clear()


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


async def _assert_collectable(ref, what):
    """The replaced view must be garbage once its waiting task has ended."""
    for _ in range(50):
        gc.collect()
        if ref() is None:
            return
        await asyncio.sleep(0.01)
    holders = [type(o).__name__ for o in gc.get_referrers(ref())]
    raise AssertionError(what + " is still alive; referrers: " + ", ".join(holders))


# --------------------------------------------------------------------------- #
# WebSocket: live_redirect_mount
# --------------------------------------------------------------------------- #


async def _ws_drain(communicator):
    while not await communicator.receive_nothing(timeout=0.2):
        await communicator.receive_json_from(timeout=2)


async def test_websocket_live_redirect_cancels_the_legacy_views_waiter():
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)  # connect ack
        await communicator.send_json_to({"type": "mount", "view": TOUR, "url": "/tour/"})
        await _ws_drain(communicator)
        assert len(VIEWS) == 1
        view_ref = VIEWS[0]
        tour_id = id(view_ref())
        await communicator.send_json_to({"type": "event", "event": "start_tour", "params": {}})
        await _until(lambda: tour_id in WAITING, "the tour to start waiting")
        assert view_ref()._waiters

        await communicator.send_json_to(
            {"type": "live_redirect_mount", "view": OTHER, "url": "/other/", "params": {}}
        )
        await _ws_drain(communicator)

        await _until(lambda: tour_id in CANCELLED, "the replaced view's waiter to be cancelled")
        assert tour_id not in WAITING and ANSWERED == []
        assert view_ref()._waiters == {}
        await _assert_collectable(view_ref, "the replaced legacy view")
    finally:
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# SSE: _replace_view
# --------------------------------------------------------------------------- #


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


async def _post(session, key, body):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200
    return response


async def test_sse_navigation_cancels_the_legacy_views_waiter(caplog):
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": TOUR, "_djust_url": "/tour/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert "sse_connect" in await stream.__anext__()
    session = _sse_sessions[sid]
    try:
        view_ref = weakref.ref(session.view_instance)
        tour_id = id(view_ref())
        await _post(session, key, {"type": "event", "event": "start_tour", "params": {}})
        await _until(lambda: tour_id in WAITING, "the tour to start waiting")

        await _post(session, key, {"type": "live_redirect_mount", "url": "/other/", "params": {}})
        assert type(session.view_instance) is OtherPage
        # A view without UploadMixin has no ``_cleanup_uploads``; the cleanup
        # no longer raises (and logs) on it.
        assert "SSE old view cleanup failed" not in caplog.text

        await _until(lambda: tour_id in CANCELLED, "the replaced view's waiter to be cancelled")
        assert tour_id not in WAITING and ANSWERED == []
        assert view_ref()._waiters == {}
        await _assert_collectable(view_ref, "the replaced legacy view")
        assert session.active is True
    finally:
        await stream.aclose()
