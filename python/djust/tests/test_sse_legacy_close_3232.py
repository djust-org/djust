"""#3232: closing an SSE stream disposes a legacy view too.

#3221 made a normal SSE close call ``SSESession.shutdown()``, but ``shutdown``
disposed only explicit views. A legacy view's ``start_async`` work kept
running and could render into a queue nobody reads, its waiters stayed
pending, its upload temp files stayed behind and its embedded children never
ran their unregister hooks. The SSE close now runs the WebSocket disconnect's
legacy teardown (uploads, waiters, children, Rust live handles) and detaches
the view. As on the WebSocket, the background work is not cancelled: it runs
to completion, and its late result is discarded against the detached view
(#3234 review: owner decision).

Same harness as ``test_exposure_sse_close_3221.py``: the real stream and
message views, and a stream closed the way Django closes it when the client
goes away (the generator is closed at a ``yield``).
"""

import asyncio
import json
import uuid

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

#: Background tasks that are still running, cancelled, or finished; the
#: views whose completion handler ran; and the gate that lets a task finish.
RUNNING: set = set()
CANCELLED: list = []
FINISHED: list = []
RESULTS: list = []
RELEASE: dict = {}


class LegacyChild(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>child</div>"

    def _cleanup_on_unregister(self):
        self.unregistered = True


class LegacyWorkPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self._uploads_cleaned = False
        self._root_unregistered = False
        self._child = LegacyChild()
        self._child.unregistered = False
        self._register_child("child", self._child)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def spawn(self):
        self.start_async(self._work, name="work")

    @event_handler()
    def noop(self):
        pass

    async def _work(self):
        RUNNING.add(id(self))
        try:
            await RELEASE.setdefault(id(self), asyncio.Event()).wait()
        except asyncio.CancelledError:
            CANCELLED.append(id(self))
            raise
        finally:
            RUNNING.discard(id(self))
        FINISHED.append(id(self))
        self.count = 99
        return "late"

    def handle_async_result(self, name, result=None, error=None):
        RESULTS.append((id(self), name, result))

    def _cleanup_uploads(self):
        self._uploads_cleaned = True

    def _cleanup_on_unregister(self):
        self._root_unregistered = True


urlpatterns = [path("legacy-work/", LegacyWorkPage.as_view())]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    RUNNING.clear()
    CANCELLED.clear()
    FINISHED.clear()
    RESULTS.clear()
    RELEASE.clear()
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
    request = await sync_to_async(_request)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": __name__ + ".LegacyWorkPage", "_djust_url": "/legacy-work/"},
        key,
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    ack = await stream.__anext__()
    assert "sse_connect" in ack
    return _sse_sessions[sid], stream


async def _spawn(session, key):
    """Start the view's background task and a pending waiter on it."""
    request = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "event", "event": "spawn", "params": {}},
        key,
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200
    view = session.view_instance
    waiter = asyncio.ensure_future(view.wait_for_event("never", timeout=30))
    await _until(lambda: id(view) in RUNNING, "the background task to start")
    await _until(lambda: bool(getattr(view, "_waiters", None)), "the waiter to register")
    return view, waiter


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


class _RustViewRecorder:
    """Stands in for the view's Rust state to record the live-handle release."""

    def __init__(self):
        self.calls = []

    def clear_live_handles(self):
        self.calls.append("clear_live_handles")

    def set_raw_py_values(self, values):
        self.calls.append(("set_raw_py_values", values))


async def _finish(view):
    """Let the view's background task finish, and wait until it has."""
    RELEASE[id(view)].set()
    await _until(lambda: id(view) in FINISHED, "the background task to finish")
    for _ in range(20):  # let the runtime's post-callback step run
        await asyncio.sleep(0)


async def test_a_normal_close_disposes_a_legacy_view():
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    session, stream = await _open(sid, key)
    view, waiter = await _spawn(session, key)
    child = view._child
    rust = view._rust_view = _RustViewRecorder()
    assert view.count == 0 and not view._uploads_cleaned

    await stream.aclose()  # the client went away

    await _until(lambda: session.view_instance is None, "the session to drop its view")
    await _until(waiter.done, "the waiter to be cancelled")
    assert waiter.cancelled()
    assert view._uploads_cleaned is True
    assert child.unregistered is True and view._child_views == {}
    assert rust.calls == ["clear_live_handles", ("set_raw_py_values", {})]
    # As on the WebSocket, the legacy root's own unregister hook does not run.
    assert view._root_unregistered is False
    assert session.active is False and session.runtime.view_instance is None

    # As on the WebSocket, the background work is not cancelled: it finishes,
    # and its late result is discarded against the detached view.
    assert id(view) in RUNNING and id(view) not in CANCELLED
    queued = session.queue.qsize()
    await _finish(view)
    assert id(view) not in CANCELLED
    assert RESULTS == [], "the completion handler must not run for a detached view"
    assert session.queue.qsize() == queued, "nothing may be pushed into the closed queue"


async def test_a_reconnect_within_the_linger_keeps_the_new_legacy_view():
    """#3221's reconnect case for a legacy view: the old stream's cleanup
    disposes only its own session's view, never the reconnect's."""
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    old, old_stream = await _open(sid, key)
    old_view, old_waiter = await _spawn(old, key)
    new, new_stream = await _open(sid, key)  # same owner: replaces the old session
    assert new is not old and _sse_sessions[sid] is new
    new_view, new_waiter = await _spawn(new, key)
    assert new_view is not old_view

    await old_stream.aclose()

    await _until(lambda: old.view_instance is None, "the old session to drop its view")
    await _until(old_waiter.done, "the old view's waiter to be cancelled")
    assert old_view._uploads_cleaned is True
    assert _sse_sessions[sid] is new and new.active is True
    assert new.view_instance is new_view and new.runtime.view_instance is new_view
    assert not new_waiter.done()
    assert new_view._uploads_cleaned is False and new_view._child.unregistered is False

    # The old view's late result is discarded; the new view is unaffected.
    await _finish(old_view)
    assert RESULTS == []
    assert id(new_view) in RUNNING

    # The new session still dispatches events, and its own work still completes.
    request = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{sid}/message/",
        {"type": "event", "event": "noop", "params": {}},
        key,
    )
    response = await DjustSSEMessageView().post(request, session_id=sid)
    assert response.status_code == 200
    await _finish(new_view)
    await _until(lambda: (id(new_view), "work", "late") in RESULTS, "the new view's result")

    await new_stream.aclose()
    await _until(new_waiter.done, "the new view's waiter to be cancelled")
