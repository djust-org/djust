"""#3232: closing an SSE stream disposes a legacy view too.

#3221 made a normal SSE close call ``SSESession.shutdown()``, but ``shutdown``
disposed only explicit views. A legacy view's ``start_async`` work kept
running (and would render into a queue nobody reads), its waiters stayed
pending, its upload temp files stayed behind and its embedded children never
ran their unregister hooks. The WebSocket disconnect cleans up a legacy view's
uploads, waiters and children; the SSE close now does that, and cancels the
background work.

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

#: Background tasks that are still running, and the ones that were cancelled.
RUNNING: set = set()
CANCELLED: list = []


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

    async def _work(self):
        RUNNING.add(id(self))
        try:
            await asyncio.Event().wait()  # until cancelled
        except asyncio.CancelledError:
            CANCELLED.append(id(self))
            raise
        finally:
            RUNNING.discard(id(self))

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


async def test_a_normal_close_disposes_a_legacy_view():
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    session, stream = await _open(sid, key)
    view, waiter = await _spawn(session, key)
    child = view._child
    assert view.count == 0 and not view._uploads_cleaned

    await stream.aclose()  # the client went away

    await _until(lambda: id(view) in CANCELLED, "the background task to be cancelled")
    assert id(view) not in RUNNING
    await _until(waiter.done, "the waiter to be cancelled")
    assert waiter.cancelled()
    assert view._uploads_cleaned is True
    assert child.unregistered is True and view._child_views == {}
    # As on the WebSocket, the legacy root's own unregister hook does not run.
    assert view._root_unregistered is False
    assert session.active is False
    assert session.view_instance is None and session.runtime.view_instance is None


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

    await _until(lambda: id(old_view) in CANCELLED, "the old view's task to be cancelled")
    await _until(old_waiter.done, "the old view's waiter to be cancelled")
    assert old.view_instance is None and old_view._uploads_cleaned is True
    assert _sse_sessions[sid] is new and new.active is True
    assert new.view_instance is new_view and new.runtime.view_instance is new_view
    assert id(new_view) in RUNNING and id(new_view) not in CANCELLED
    assert not new_waiter.done()
    assert new_view._uploads_cleaned is False and new_view._child.unregistered is False

    # The new session still dispatches events.
    request = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{sid}/message/",
        {"type": "event", "event": "spawn", "params": {}},
        key,
    )
    response = await DjustSSEMessageView().post(request, session_id=sid)
    assert response.status_code == 200

    await new_stream.aclose()
    await _until(lambda: id(new_view) in CANCELLED, "the new view's task to be cancelled")
    await _until(new_waiter.done, "the new view's waiter to be cancelled")
