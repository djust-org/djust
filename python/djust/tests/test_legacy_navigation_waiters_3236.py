"""#3236: a replaced or disconnected legacy view's ``wait_for_event`` waiters are closed.

When navigation replaces a view — ``live_redirect`` on the WebSocket,
``_replace_view`` on SSE — an explicit view is disposed through
``dispose_child_subtree`` (which cancels its waiters), but a legacy view only
had its uploads (and, on SSE, its children) cleaned up. Its waiters were never
cancelled. Nothing strong holds a waiter's future except the view, so the
view, the future and the background task blocked on it became an unreachable
cycle: the garbage collector destroyed the task while it was still pending
("Task was destroyed but it is pending!"), closing its coroutine with
``GeneratorExit``, so its ``except CancelledError`` cleanup never ran.

Navigation now cancels them, so the task's own cleanup runs. It also closes the
view to later waiters (#3242 review M2): the view's background work keeps
running, and a ``wait_for_event`` it starts after the navigation, or after a
disconnect, fails at once instead of registering a waiter nothing cancels.

Both transports are driven through their real entry points: a
``WebsocketCommunicator`` against ``LiveViewConsumer``, and the SSE stream and
message views. The waiters are awaited by the view's own ``start_async`` work,
the realistic shape (a guided tour waiting for the user's next click). Views
are told apart by a tag set at mount, not ``id()``, which a collected view's
successor can reuse.
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

#: Weak references to every mounted tour view, and ``(what, tag)`` records of
#: what each view's background work did: "waiting", "cleanup" (its
#: ``except CancelledError`` block ran), "answered".
VIEWS: list = []
EVENTS: list = []
#: Per-tag gates that hold the late-waiter task back until the test opens them.
GATES: dict = {}


class TourPage(LiveView):
    """A legacy view whose background work waits for the user's next click."""

    exposure_policy = "legacy"
    template = "<div dj-root><span>tour</span></div>"

    def mount(self, request, **kwargs):
        self.tag = uuid.uuid4().hex
        VIEWS.append(weakref.ref(self))

    @event_handler()
    def start_tour(self, **kwargs):
        self.start_async(self._wait_for_next, name="tour")

    @event_handler()
    def start_late_tour(self, **kwargs):
        GATES[self.tag] = asyncio.Event()
        self.start_async(self._wait_later, name="late")

    @event_handler()
    def next_step(self, **kwargs):
        pass

    async def _wait_for_next(self):
        EVENTS.append(("waiting", self.tag))
        try:
            await self.wait_for_event("next_step")
        except asyncio.CancelledError:
            EVENTS.append(("cleanup", self.tag))
            raise
        EVENTS.append(("answered", self.tag))

    async def _wait_later(self):
        """Some work first (the navigation happens meanwhile), then a wait."""
        EVENTS.append(("working", self.tag))
        await GATES[self.tag].wait()
        try:
            await self.wait_for_event("next_step")
        except asyncio.CancelledError:
            EVENTS.append(("late-refused", self.tag))
            raise
        EVENTS.append(("answered", self.tag))

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
    EVENTS.clear()
    GATES.clear()
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


def _assert_cleaned_up(view_ref, tag):
    """The task got ``CancelledError`` and ran its own cleanup (before the fix
    it was destroyed pending at GC time and that block never ran)."""
    assert ("cleanup", tag) in EVENTS
    assert ("answered", tag) not in EVENTS
    view = view_ref()  # the view may already be collected, which is fine
    assert view is None or view._waiters == {}
    del view


async def _assert_collectable(ref, what):
    """The replaced view must be garbage once its waiting task has ended."""
    for _ in range(50):
        gc.collect()
        if ref() is None:
            return
        await asyncio.sleep(0.01)
    holders = [type(o).__name__ for o in gc.get_referrers(ref())]
    raise AssertionError(what + " is still alive; referrers: " + ", ".join(holders))


async def _assert_late_wait_refused(view_ref, tag):
    """Open the gate: the task's ``wait_for_event`` must fail at once and
    leave no waiter registered on the discarded view."""
    view = view_ref()
    assert view is not None
    GATES[tag].set()
    await _until(lambda: ("late-refused", tag) in EVENTS, "the late wait to be refused")
    assert ("answered", tag) not in EVENTS
    assert view._waiters == {}
    del view


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


async def _ws_drain(communicator):
    while not await communicator.receive_nothing(timeout=0.2):
        await communicator.receive_json_from(timeout=2)


async def _ws_mounted_tour(event):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    await communicator.send_json_to({"type": "mount", "view": TOUR, "url": "/tour/"})
    await _ws_drain(communicator)
    assert len(VIEWS) == 1
    view_ref = VIEWS[0]
    tag = view_ref().tag
    await communicator.send_json_to({"type": "event", "event": event, "params": {}})
    return communicator, view_ref, tag


async def _ws_redirect_away(communicator):
    await communicator.send_json_to(
        {"type": "live_redirect_mount", "view": OTHER, "url": "/other/", "params": {}}
    )
    await _ws_drain(communicator)


async def test_websocket_live_redirect_cancels_the_legacy_views_waiter():
    communicator, view_ref, tag = await _ws_mounted_tour("start_tour")
    try:
        await _until(lambda: ("waiting", tag) in EVENTS, "the tour to start waiting")
        await _ws_redirect_away(communicator)
        await _until(lambda: ("cleanup", tag) in EVENTS, "the replaced view's waiter cleanup")
        _assert_cleaned_up(view_ref, tag)
        await _assert_collectable(view_ref, "the replaced legacy view")
    finally:
        await communicator.disconnect()


async def test_websocket_a_waiter_started_after_live_redirect_is_refused():
    communicator, view_ref, tag = await _ws_mounted_tour("start_late_tour")
    try:
        await _until(lambda: ("working", tag) in EVENTS, "the late task to start")
        await _ws_redirect_away(communicator)
        await _assert_late_wait_refused(view_ref, tag)
    finally:
        await communicator.disconnect()


async def test_websocket_a_waiter_started_after_disconnect_is_refused():
    communicator, view_ref, tag = await _ws_mounted_tour("start_late_tour")
    await _until(lambda: ("working", tag) in EVENTS, "the late task to start")
    await communicator.disconnect()
    await _assert_late_wait_refused(view_ref, tag)


# --------------------------------------------------------------------------- #
# SSE
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


async def _sse_mounted_tour(event):
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
    view_ref = weakref.ref(session.view_instance)
    tag = view_ref().tag
    await _post(session, key, {"type": "event", "event": event, "params": {}})
    return session, key, stream, view_ref, tag


async def test_sse_navigation_cancels_the_legacy_views_waiter(caplog):
    session, key, stream, view_ref, tag = await _sse_mounted_tour("start_tour")
    try:
        await _until(lambda: ("waiting", tag) in EVENTS, "the tour to start waiting")
        await _post(session, key, {"type": "live_redirect_mount", "url": "/other/", "params": {}})
        assert type(session.view_instance) is OtherPage
        # A view without UploadMixin has no ``_cleanup_uploads``; the cleanup
        # no longer raises (and logs) on it.
        assert "SSE old view cleanup failed" not in caplog.text

        await _until(lambda: ("cleanup", tag) in EVENTS, "the replaced view's waiter cleanup")
        _assert_cleaned_up(view_ref, tag)
        await _assert_collectable(view_ref, "the replaced legacy view")
        assert session.active is True
    finally:
        await stream.aclose()


async def test_sse_a_waiter_started_after_navigation_is_refused():
    session, key, stream, view_ref, tag = await _sse_mounted_tour("start_late_tour")
    try:
        await _until(lambda: ("working", tag) in EVENTS, "the late task to start")
        await _post(session, key, {"type": "live_redirect_mount", "url": "/other/", "params": {}})
        assert type(session.view_instance) is OtherPage
        await _assert_late_wait_refused(view_ref, tag)
    finally:
        await stream.aclose()


async def test_sse_a_waiter_started_after_close_is_refused():
    session, key, stream, view_ref, tag = await _sse_mounted_tour("start_late_tour")
    await _until(lambda: ("working", tag) in EVENTS, "the late task to start")
    await stream.aclose()  # the client went away
    await _until(lambda: session.view_instance is None, "the session to drop its view")
    await _assert_late_wait_refused(view_ref, tag)
