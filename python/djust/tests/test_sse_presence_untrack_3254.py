"""#3254: closing an SSE stream untracks the view's presence.

#3250 untracked presence when SSE navigated away from a view but not when the
stream closed: ``SSESession.shutdown()`` is synchronous and ``untrack_presence``
broadcasts through the synchronous channel-layer API, so the record lingered
for ``PRESENCE_TIMEOUT``. ``shutdown`` now schedules the untrack on a worker
thread, from whichever loop closes the session, unless a reconnect under the
same session id has replaced (or is mounting to replace) the closing session.

Same harness as ``test_sse_legacy_close_3232.py``: the real stream and message
views, the in-memory presence backend, and a stream closed the way Django
closes it when the client goes away (the generator is closed at a ``yield``).
"""

import asyncio
import threading
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, sse
from djust.presence import PresenceManager, PresenceMixin
from djust.sse import DjustSSEStreamView, _sse_sessions

pytestmark = [pytest.mark.django_db(transaction=True)]

ROOM = "sse3254"
LEFT: list = []
#: Held by a reconnect's mount (after it joined presence) so a test can close
#: the old stream while the new GET is still mounting.
MOUNT_GATE = {"gate": None, "reached": threading.Event()}


class PresentPage(PresenceMixin, LiveView):
    exposure_policy = "legacy"
    presence_key = ROOM
    template = "<div dj-root><span>{{ online_count }}</span></div>"

    def mount(self, request, **kwargs):
        self.track_presence(meta={})
        gate = MOUNT_GATE["gate"]
        if gate is not None:
            MOUNT_GATE["reached"].set()
            assert gate.wait(10), "the test never released the mount"

    def get_presence_user_id(self):
        return "user-3254"

    def handle_presence_leave(self, presence):
        LEFT.append(presence["id"])


class PlainPage(LiveView):
    """No presence mixin: shutdown must not schedule anything for it."""

    exposure_policy = "legacy"
    template = "<div dj-root>plain</div>"


urlpatterns = [
    path("present/", PresentPage.as_view()),
    path("plain/", PlainPage.as_view()),
]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    LEFT.clear()
    MOUNT_GATE["gate"] = None
    MOUNT_GATE["reached"].clear()
    _sse_sessions.clear()
    sse._sse_mounting.clear()
    with override_settings(ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust"], DEBUG=False):
        yield
    _sse_sessions.clear()
    sse._sse_mounting.clear()
    PresenceManager.leave_presence(ROOM, "user-3254")


def _request(url, body, key):
    request = RequestFactory().get(url, data=body)
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _fresh_key():
    session = SessionStore()
    session.create()
    return session.session_key


async def _open(page, url, sid=None, key=None):
    sid = sid or str(uuid.uuid4())
    key = key or await sync_to_async(_fresh_key)()
    request = await sync_to_async(_request)(
        f"/djust/sse/{sid}/",
        {"view": __name__ + "." + page.__name__, "_djust_url": url},
        key,
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert "sse_connect" in await stream.__anext__()
    return _sse_sessions[sid], stream, sid, key


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


async def _members():
    return await sync_to_async(PresenceManager.list_presences)(ROOM)


async def test_closing_an_sse_stream_untracks_the_views_presence():
    session, stream, _, _ = await _open(PresentPage, "/present/")
    view = session.view_instance
    assert view._presence_tracked is True
    assert [p["id"] for p in await _members()] == ["user-3254"]

    await stream.aclose()  # the client went away

    await _until(lambda: session.view_instance is None, "the session to drop its view")
    await _until(lambda: LEFT, "the presence leave handler to run")
    assert await _members() == []
    assert view._presence_tracked is False
    assert LEFT == ["user-3254"]


async def test_a_forced_close_untracks_the_views_presence():
    """``SSESession.close`` (the rate limiter's hook) shares ``shutdown``."""
    session, stream, _, _ = await _open(PresentPage, "/present/")
    view = session.view_instance
    assert len(await _members()) == 1

    await session.close()

    await _until(lambda: view._presence_tracked is False, "the presence to be untracked")
    assert await _members() == []
    await stream.aclose()


async def test_a_reconnect_keeps_the_user_present_when_the_old_stream_closes():
    """EventSource auto-reconnect reuses the session id and owner: the new GET
    mounts (and joins the same (room, user) record) and replaces the old
    session; the old stream's cleanup must not delete the new view's record."""
    old, old_stream, sid, key = await _open(PresentPage, "/present/")
    new, new_stream, _, _ = await _open(PresentPage, "/present/", sid, key)
    assert new is not old and _sse_sessions[sid] is new
    assert [p["id"] for p in await _members()] == ["user-3254"]

    await old_stream.aclose()  # the old stream's linger, then its shutdown

    await _until(lambda: old.view_instance is None, "the old session to drop its view")
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.2)  # a wrongly scheduled untrack would have run by now
    assert [p["id"] for p in await _members()] == ["user-3254"]
    assert new.view_instance._presence_tracked is True
    assert LEFT == []

    await new_stream.aclose()  # the live session's own close still untracks
    await _until(lambda: LEFT, "the live session's untrack")
    assert await _members() == []


async def test_a_reconnect_still_mounting_when_the_old_stream_closes():
    """The reconnect joins presence before it registers: the old session's
    close, landing between the two, must not delete the fresh record."""
    old, old_stream, sid, key = await _open(PresentPage, "/present/")
    MOUNT_GATE["gate"] = gate = threading.Event()
    reconnect = asyncio.ensure_future(_open(PresentPage, "/present/", sid, key))
    await _until(MOUNT_GATE["reached"].is_set, "the reconnect to join presence")
    assert _sse_sessions[sid] is old  # not registered yet

    await old_stream.aclose()
    await _until(lambda: old.view_instance is None, "the old session to drop its view")
    await asyncio.sleep(0.2)
    gate.set()
    new, new_stream, _, _ = await reconnect

    assert [p["id"] for p in await _members()] == ["user-3254"]
    assert LEFT == []
    assert new.view_instance._presence_tracked is True
    await new_stream.aclose()


async def test_shutdown_outside_a_running_loop_untracks_synchronously():
    """A caller with no running loop has nothing to schedule onto: the untrack
    runs inline."""
    session, stream, _, _ = await _open(PresentPage, "/present/")
    view = session.view_instance
    assert len(await _members()) == 1

    def close_from_a_plain_thread():
        session.shutdown()  # no running loop on this thread

    await asyncio.get_running_loop().run_in_executor(None, close_from_a_plain_thread)

    assert view._presence_tracked is False
    assert await _members() == []
    await stream.aclose()


@pytest.mark.parametrize("tracked", [False, True])
async def test_only_a_presence_tracking_view_schedules_an_untrack(monkeypatch, tracked):
    calls = []
    import djust._child_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "untrack_view_presence", calls.append)
    page = PresentPage if tracked else PlainPage
    session, stream, _, _ = await _open(page, "/present/" if tracked else "/plain/")
    view = session.view_instance
    assert getattr(view, "_presence_tracked", False) is tracked

    session.shutdown()
    scheduled = list(session._presence_untrack_tasks)
    for task in scheduled:
        await task

    assert calls == ([view] if tracked else [])
    assert bool(scheduled) is tracked
    await stream.aclose()


def test_a_loopless_sync_caller_untracks_inline():
    """No loop anywhere (a management command, a plain sync test)."""
    view = PresentPage()
    view._websocket_session_id = "ws-3254"
    view.track_presence(meta={})
    assert [p["id"] for p in PresenceManager.list_presences(ROOM)] == ["user-3254"]
    session = sse.SSESession("loopless-3254")
    session.view_instance = view
    session.runtime.view_instance = view
    assert session._loop is None

    session.shutdown()

    assert view._presence_tracked is False
    assert PresenceManager.list_presences(ROOM) == []
    assert LEFT == ["user-3254"]
    assert not session._presence_untrack_tasks
