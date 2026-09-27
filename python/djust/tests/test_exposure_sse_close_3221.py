"""#3221: a normal SSE stream close disposes the session's view.

The stream's ``finally`` lingered and unregistered the session, but never
called ``session.shutdown()``. The view and its children were never disposed,
so ``start_async`` work kept running (and rendering into a dead queue), waiters
stayed pending, upload temp files stayed behind and unregister hooks never
ran. The WebSocket path tears all of this down on disconnect.

Driven through the real stream and message views. A stream is closed the way
Django closes it when the client goes away: the generator is closed at a
``yield``.
"""

import asyncio
import json
import threading
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.decorators import state
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

#: Background tasks that are still running, and the ones that were cancelled.
RUNNING: set = set()
CANCELLED: list = []
SLOW_ENTERED = threading.Event()
SLOW_RELEASE = threading.Event()


class WorkPage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.count = 0
        self.unregistered = False

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def spawn(self):
        self.start_async(self._work, name="work")

    @event_handler()
    def slow(self):
        SLOW_ENTERED.set()
        SLOW_RELEASE.wait(timeout=10)
        self.count += 1

    async def _work(self):
        RUNNING.add(id(self))
        try:
            await asyncio.Event().wait()  # until cancelled
        except asyncio.CancelledError:
            CANCELLED.append(id(self))
            raise
        finally:
            RUNNING.discard(id(self))

    def _cleanup_on_unregister(self):
        self.unregistered = True


urlpatterns = [path("work/", WorkPage.as_view())]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    RUNNING.clear()
    CANCELLED.clear()
    SLOW_ENTERED.clear()
    SLOW_RELEASE.clear()
    _sse_sessions.clear()
    with override_settings(ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust"], DEBUG=False):
        yield
    _sse_sessions.clear()


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


async def _open(sid, key):
    """Open a stream and read its ack, as a connected browser does."""
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": __name__ + ".WorkPage", "_djust_url": "/work/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    ack = await stream.__anext__()
    assert "sse_connect" in ack
    return _sse_sessions[sid], stream


async def _spawn(session, key):
    request = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "event", "event": "spawn", "params": {}},
        key,
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200
    view = session.view_instance
    for _ in range(500):
        if id(view) in RUNNING:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("the background task never started")


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


async def test_a_normal_close_cancels_background_work_and_disposes_the_view():
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    session, stream = await _open(sid, key)
    view = await _spawn(session, key)

    await stream.aclose()  # the client went away

    await _until(lambda: id(view) in CANCELLED, "the background task to be cancelled")
    assert id(view) not in RUNNING
    assert sid not in _sse_sessions
    assert session.active is False
    assert session.view_instance is None and session.runtime.view_instance is None
    assert view._djust_child_disposed is True
    assert view.unregistered is True


async def test_a_reconnect_within_the_linger_keeps_its_view():
    """EventSource reconnects with the same id before the old stream's linger
    ends. The reconnect's session and view must survive the old stream's
    cleanup; only the old session's own view is disposed."""
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    old, old_stream = await _open(sid, key)
    old_view = await _spawn(old, key)
    new, new_stream = await _open(sid, key)  # same owner: replaces the old session
    assert new is not old and _sse_sessions[sid] is new
    new_view = await _spawn(new, key)

    await old_stream.aclose()

    await _until(lambda: id(old_view) in CANCELLED, "the old view's task to be cancelled")
    assert _sse_sessions[sid] is new
    assert new.active is True
    assert new.view_instance is new_view and new_view is not old_view
    assert id(new_view) in RUNNING and id(new_view) not in CANCELLED
    assert not getattr(new_view, "_djust_child_disposed", False)
    assert old.view_instance is None

    await new_stream.aclose()
    await _until(lambda: id(new_view) in CANCELLED, "the new view's task to be cancelled")


async def test_an_event_posted_during_the_linger_still_dispatches(monkeypatch):
    """The linger exists so in-flight POSTs still find the session; the
    shutdown waits until it ends."""
    gate = asyncio.Event()

    async def linger():
        await gate.wait()

    monkeypatch.setattr(sse, "_linger", linger)
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    session, stream = await _open(sid, key)
    closing = asyncio.ensure_future(stream.aclose())
    await _until(lambda: session._stream_closed, "the stream to start closing")
    try:
        view = await _spawn(session, key)
        assert session.active is True
    finally:
        gate.set()
    await closing
    await _until(lambda: id(view) in CANCELLED, "the task to be cancelled after the linger")
    assert session.view_instance is None


async def test_the_shutdown_waits_for_an_event_still_being_dispatched():
    """A turn still running when the linger ends finishes before the view is
    disposed, so it is never torn down half-way."""
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    session, stream = await _open(sid, key)
    view = session.view_instance
    request = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{sid}/message/",
        {"type": "event", "event": "slow", "params": {}},
        key,
    )
    post = asyncio.ensure_future(DjustSSEMessageView().post(request, session_id=sid))
    try:
        await _until(SLOW_ENTERED.is_set, "the handler to start")
        closing = asyncio.ensure_future(stream.aclose())
        await _until(lambda: session._stream_closed, "the stream to start closing")
        for _ in range(20):  # let the closing stream run as far as it can
            await asyncio.sleep(0)
        assert session.active is True, "disposed while its turn was still running"
        assert session.view_instance is view
    finally:
        SLOW_RELEASE.set()
    response = await post
    assert response.status_code == 200
    await closing
    assert view.count == 1
    assert session.active is False and session.view_instance is None


async def test_a_turn_stuck_past_the_cap_does_not_keep_the_session_alive(monkeypatch, caplog):
    """#3229 review I3: the wait for an in-flight turn is bounded. A turn stuck
    in storage (here: the dispatch lock held and never released) must not
    keep the closed session, its runtime and view alive indefinitely."""
    monkeypatch.setattr(sse, "_CLOSE_DISPATCH_WAIT_S", 0.2)
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    session, stream = await _open(sid, key)
    view = await _spawn(session, key)
    await session._dispatch_lock.acquire()  # a turn that never finishes
    try:
        await asyncio.wait_for(stream.aclose(), 5)
        assert session.active is False and session.view_instance is None
        await _until(lambda: id(view) in CANCELLED, "the background task to be cancelled")
        assert "still dispatching" in caplog.text
    finally:
        session._dispatch_lock.release()


class _RustViewRecorder:
    """Stands in for the view's Rust state to record the live-handle release."""

    def __init__(self):
        self.calls = []

    def clear_live_handles(self):
        self.calls.append("clear_live_handles")

    def set_raw_py_values(self, values):
        self.calls.append(("set_raw_py_values", values))


async def test_a_normal_close_releases_an_explicit_views_live_handles():
    """#3239: the explicit branch of ``shutdown()`` disposed the subtree but
    never dropped the Rust live handles, which the WebSocket disconnect (and,
    since #3232, the SSE legacy branch) does for every view. The Rust state
    keeps strong references the garbage collector cannot see."""
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    session, stream = await _open(sid, key)
    view = await _spawn(session, key)
    rust = view._rust_view = _RustViewRecorder()

    await stream.aclose()  # the client went away

    await _until(lambda: session.view_instance is None, "the session to drop its view")
    assert view._djust_child_disposed is True
    assert rust.calls == ["clear_live_handles", ("set_raw_py_values", {})]
