"""#3254: closing an SSE stream untracks the view's presence.

#3250 untracked presence when SSE navigated away from a view but not when the
stream closed: ``SSESession.shutdown()`` is synchronous and ``untrack_presence``
broadcasts through the synchronous channel-layer API, so the record lingered
for ``PRESENCE_TIMEOUT``. ``shutdown`` now schedules the untrack on a worker
thread, from whichever loop closes the session.

Same harness as ``test_sse_legacy_close_3232.py``: the real stream and message
views, the in-memory presence backend, and a stream closed the way Django
closes it when the client goes away (the generator is closed at a ``yield``).
"""

import asyncio
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

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

ROOM = "sse3254"
LEFT: list = []


class PresentPage(PresenceMixin, LiveView):
    exposure_policy = "legacy"
    presence_key = ROOM
    template = "<div dj-root><span>{{ online_count }}</span></div>"

    def mount(self, request, **kwargs):
        self.track_presence(meta={})

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
    _sse_sessions.clear()
    with override_settings(ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust"], DEBUG=False):
        yield
    _sse_sessions.clear()
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


async def _open(page, url):
    sid = str(uuid.uuid4())
    key = await sync_to_async(_fresh_key)()
    request = await sync_to_async(_request)(
        f"/djust/sse/{sid}/",
        {"view": __name__ + "." + page.__name__, "_djust_url": url},
        key,
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert "sse_connect" in await stream.__anext__()
    return _sse_sessions[sid], stream


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


async def _members():
    return await sync_to_async(PresenceManager.list_presences)(ROOM)


async def test_closing_an_sse_stream_untracks_the_views_presence():
    session, stream = await _open(PresentPage, "/present/")
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
    session, stream = await _open(PresentPage, "/present/")
    view = session.view_instance
    assert len(await _members()) == 1

    await session.close()

    await _until(lambda: view._presence_tracked is False, "the presence to be untracked")
    assert await _members() == []
    await stream.aclose()


async def test_shutdown_outside_a_running_loop_untracks_synchronously():
    """A caller with no running loop (a management command, a sync test) has
    nothing to schedule onto: the untrack runs inline."""
    session, stream = await _open(PresentPage, "/present/")
    view = session.view_instance
    assert len(await _members()) == 1

    def close_from_a_plain_thread():
        session.shutdown()  # no running loop on this thread

    await asyncio.get_running_loop().run_in_executor(None, close_from_a_plain_thread)

    assert view._presence_tracked is False
    assert await _members() == []
    await stream.aclose()


async def test_a_view_without_presence_schedules_nothing():
    session, stream = await _open(PlainPage, "/plain/")
    assert not session._presence_untrack_tasks
    session.shutdown()
    assert not session._presence_untrack_tasks
    await stream.aclose()
