"""``djust.testing``'s live-session helpers drive explicit views for real (#4191).

``open_live_page`` loads a page through the project's middleware, and its
``LivePage`` reaches the view over each transport a browser uses: the HTTP
fallback POST, a WebSocket and an SSE stream. Each test here checks what the
VIEW saw (its user, tenant and tab state, recorded on the server), not only
that a frame came back, so a helper that dropped the session, the user, the
tab or the request headers would fail them.
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler
from djust.testing import (
    LivePage,
    LiveSocket,
    LiveSSE,
    live_session,
    open_live_page,
    with_live_session,
)

pytestmark = [pytest.mark.django_db(transaction=True)]

#: What the views saw, in order: (where, user, tenant, count).
SEEN: list[tuple[str, str, str, int]] = []


def _who(request) -> tuple[str, str]:
    user = getattr(request, "user", None)
    name = user.get_username() if getattr(user, "is_authenticated", False) else "anonymous"
    tenant = getattr(getattr(request, "tenant", None), "id", None) or "-"
    return name, str(tenant)


class Counter(LiveView):
    """``count`` is the view's own state, saved per tab (#4106/#4107)."""

    exposure_policy = "explicit"
    template = "<div dj-root><p>count={{ count }}</p></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        SEEN.append(("mount", *_who(request), self.count))

    def get_context_data(self, **kwargs):
        return {"count": self.count}

    @event_handler
    def increment(self, **kwargs):
        self.count += 1
        SEEN.append(("increment", *_who(self.request), self.count))

    @event_handler
    def ping(self, **kwargs):
        """A push event, sent before the event's own answer."""
        self.push_event("pinged", {"count": self.count})
        self.count += 1


class Members(Counter):
    login_required = True


urlpatterns = [
    path("t/counter/", Counter.as_view()),
    path("t/members/", Members.as_view()),
]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
}
TENANT_SETTINGS = {**SETTINGS, "DJUST_CONFIG": {"TENANT_RESOLVER": "header"}}
VIEW = "%s.Counter" % __name__


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(**SETTINGS):
        yield
    SEEN.clear()


def _user(name: str = "ada"):
    return get_user_model().objects.create_user(username=name, password="x")


async def _auser(name: str = "ada"):
    return await sync_to_async(_user)(name)


def _seen(where: str) -> list[tuple[str, str, int]]:
    return [entry[1:] for entry in SEEN if entry[0] == where]


# --------------------------------------------------------------------------- #
# The page GET
# --------------------------------------------------------------------------- #


def test_the_page_names_its_tab_view_and_saved_session():
    page = open_live_page("/t/counter/")
    assert isinstance(page, LivePage)
    assert page.tab and page.view_path == VIEW
    assert page.session.session_key
    assert _seen("mount") == [("anonymous", "-", 0)]


def test_a_refused_page_get_raises():
    with pytest.raises(AssertionError, match="answered 302"):
        open_live_page("/t/members/")


# --------------------------------------------------------------------------- #
# HTTP fallback
# --------------------------------------------------------------------------- #


def test_fallback_post_runs_the_handler_on_the_pages_tab_state():
    page = open_live_page("/t/counter/")
    for _ in range(2):
        response = page.post_event("increment")
        assert response.status_code == 200, response.content[:300]
        assert "html" in json.loads(response.content) or "patches" in json.loads(response.content)
    assert _seen("increment") == [("anonymous", "-", 1), ("anonymous", "-", 2)]


def test_fallback_post_as_a_logged_in_user():
    user = _user()
    page = open_live_page("/t/members/", user=user)
    assert page.post_event("increment").status_code == 200
    assert _seen("increment") == [("ada", "-", 1)]


def test_two_tabs_keep_their_own_state_over_the_fallback():
    first = open_live_page("/t/counter/")
    second = open_live_page("/t/counter/", client=first.client)
    assert first.tab != second.tab
    assert first.session.session_key == second.session.session_key
    first.post_event("increment")
    first.post_event("increment")
    second.post_event("increment")
    assert [count for *_, count in _seen("increment")] == [1, 2, 1]


def test_fallback_tenant_from_the_pages_headers():
    with override_settings(**TENANT_SETTINGS):
        page = open_live_page("/t/counter/", HTTP_X_TENANT_ID="acme")
        assert page.post_event("increment").status_code == 200
    assert _seen("increment") == [("anonymous", "acme", 1)]


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


async def test_socket_mounts_on_the_pages_session_and_runs_events():
    page = await sync_to_async(open_live_page)("/t/counter/")
    async with await page.connect() as socket:
        assert isinstance(socket, LiveSocket)
        assert socket.mount["type"] == "mount", socket.mount
        answer = await socket.send_event("increment")
        assert answer["type"] in ("patch", "html_update"), answer
        assert answer["ref"] == 1
    assert _seen("increment") == [("anonymous", "-", 1)]


async def test_send_event_answers_with_its_own_frame_and_keeps_the_others():
    """``ping``'s push event follows its patch, so it arrives while the next
    event waits for its answer: ``send_event`` returns the frame that echoes
    its own ``ref`` and leaves the push for ``receive``."""
    page = await sync_to_async(open_live_page)("/t/counter/")
    async with await page.connect() as socket:
        first = await socket.send_event("ping")
        second = await socket.send_event("increment")
        assert first["ref"] == 1 and second["ref"] == 2, (first, second)
        assert second["type"] in ("patch", "html_update"), second
        pushed = await socket.receive(("push_event",), timeout=5)
        assert pushed["event"] == "pinged", socket.frames
        assert socket.frames.index(pushed) < socket.frames.index(second)


async def test_socket_as_a_logged_in_user():
    user = await _auser()
    page = await sync_to_async(open_live_page)("/t/members/", user=user)
    async with await page.connect() as socket:
        assert socket.mount["type"] == "mount", socket.mount
        assert (await socket.send_event("increment"))["type"] != "error"
    assert _seen("increment") == [("ada", "-", 1)]


async def test_socket_tenant_from_the_pages_headers():
    with override_settings(**TENANT_SETTINGS):
        page = await sync_to_async(open_live_page)("/t/counter/", HTTP_X_TENANT_ID="acme")
        async with await page.connect() as socket:
            assert socket.mount["type"] == "mount", socket.mount
            assert (await socket.send_event("increment"))["type"] != "error"
    assert _seen("increment")[-1] == ("anonymous", "acme", 1)


async def test_two_tabs_over_two_sockets():
    first = await sync_to_async(open_live_page)("/t/counter/")
    second = await sync_to_async(open_live_page)("/t/counter/", client=first.client)
    async with await first.connect() as one, await second.connect() as two:
        await one.send_event("increment")
        await one.send_event("increment")
        await two.send_event("increment")
    assert [count for *_, count in _seen("increment")] == [1, 2, 1]


async def test_a_second_tab_of_a_logged_in_client_is_logged_in():
    """``client=`` without ``user=``: the transports use the session's user."""
    user = await _auser()
    first = await sync_to_async(open_live_page)("/t/members/", user=user)
    second = await sync_to_async(open_live_page)("/t/members/", client=first.client)
    assert second.user is None and second.tab != first.tab
    async with await second.connect() as socket:
        assert socket.mount["type"] == "mount", socket.mount
        await socket.send_event("increment")
    async with await second.connect_sse() as stream:
        assert stream.mount["type"] == "mount", stream.mount
        await stream.send_event("increment")
    assert [entry[:2] for entry in _seen("increment")] == [("ada", "-")] * 2


async def test_socket_through_the_projects_own_asgi_stack():
    """A ``djust new`` project's application: origin validator, auth stack and
    URL router read the socket's cookie and Origin as a browser's."""
    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter
    from django.urls import path as route

    from djust.routing import DjustOriginValidator
    from djust.websocket import LiveViewConsumer

    application = DjustOriginValidator(
        AuthMiddlewareStack(URLRouter([route("ws/live/", LiveViewConsumer.as_asgi())]))
    )
    user = await _auser()
    page = await sync_to_async(open_live_page)("/t/members/", user=user)
    async with await page.connect(application=application) as socket:
        assert socket.mount["type"] == "mount", socket.mount
        assert (await socket.send_event("increment"))["type"] != "error"
    assert _seen("increment") == [("ada", "-", 1)]


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


async def test_sse_mounts_on_the_pages_session_and_runs_events():
    page = await sync_to_async(open_live_page)("/t/counter/")
    async with await page.connect_sse() as stream:
        assert isinstance(stream, LiveSSE)
        assert stream.mount["type"] == "mount", stream.mount
        assert stream.session is not None
        answer = await stream.send_event("increment")
        assert answer["type"] in ("patch", "html_update"), answer
    assert stream.session is None, "closing the stream disposes the session"
    assert _seen("increment") == [("anonymous", "-", 1)]


async def test_sse_as_a_logged_in_user():
    user = await _auser()
    page = await sync_to_async(open_live_page)("/t/members/", user=user)
    async with await page.connect_sse() as stream:
        assert stream.mount["type"] == "mount", stream.mount
        assert (await stream.send_event("increment"))["type"] != "error"
    assert _seen("increment") == [("ada", "-", 1)]


async def test_sse_tenant_from_the_pages_headers():
    with override_settings(**TENANT_SETTINGS):
        page = await sync_to_async(open_live_page)("/t/counter/", HTTP_X_TENANT_ID="acme")
        async with await page.connect_sse() as stream:
            assert stream.mount["type"] == "mount", stream.mount
            assert (await stream.send_event("increment"))["type"] != "error"
    assert _seen("increment")[-1] == ("anonymous", "acme", 1)


async def test_sse_resumes_the_tabs_saved_state_after_a_socket():
    """The tab carries state from one transport to the next: the socket's
    disconnect saves it, the SSE mount on the same tab restores it."""
    page = await sync_to_async(open_live_page)("/t/counter/")
    async with await page.connect() as socket:
        await socket.send_event("increment")
    async with await page.connect_sse() as stream:
        await stream.send_event("increment")
    assert [count for *_, count in _seen("increment")] == [1, 2]


# --------------------------------------------------------------------------- #
# with_live_session / live_session on hand-built targets
# --------------------------------------------------------------------------- #


def test_live_session_is_saved_and_logged_in():
    from django.contrib.auth import SESSION_KEY

    user = _user()
    session = live_session(user)
    assert session.session_key and session[SESSION_KEY] == str(user.pk)
    assert SESSION_KEY not in live_session()


def test_with_live_session_on_a_request_keeps_its_session_and_sets_the_user():
    user = _user()
    request = with_live_session(RequestFactory().get("/"))
    assert request.session.session_key and isinstance(request.user, AnonymousUser)
    key = request.session.session_key
    assert with_live_session(request, user) is request
    assert request.session.session_key == key and request.user == user


def test_with_live_session_on_a_scope():
    scope: dict = {"type": "websocket"}
    assert with_live_session(scope) is scope
    assert scope["session"].session_key and isinstance(scope["user"], AnonymousUser)


async def test_with_live_session_mounts_a_hand_built_communicator():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    user = await _auser()
    communicator = with_live_session(
        WebsocketCommunicator(
            LiveViewConsumer.as_asgi(),
            "/ws/live/",
            headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
        ),
        user,
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.send_json_to({"type": "mount", "view": VIEW, "url": "/t/counter/"})
    socket = LiveSocket(communicator)
    assert (await socket.receive(("mount", "error")))["type"] == "mount"
    assert (await socket.send_event("increment"))["type"] != "error"
    await socket.disconnect()
    assert _seen("increment") == [("ada", "-", 1)]


# --------------------------------------------------------------------------- #
# Django's own test runner
# --------------------------------------------------------------------------- #


@override_settings(**SETTINGS)
class FallbackUnderTestCase(TestCase):
    def tearDown(self):
        SEEN.clear()

    def test_fallback_and_login(self):
        user = _user()
        page = open_live_page("/t/members/", user=user)
        self.assertEqual(page.post_event("increment").status_code, 200)
        self.assertEqual(_seen("increment"), [("ada", "-", 1)])


@override_settings(**SETTINGS)
class SocketUnderTransactionTestCase(TransactionTestCase):
    def tearDown(self):
        SEEN.clear()

    async def test_socket_and_sse(self):
        user = await _auser()
        page = await sync_to_async(open_live_page)("/t/members/", user=user)
        async with await page.connect() as socket:
            self.assertEqual(socket.mount["type"], "mount")
            await socket.send_event("increment")
        async with await page.connect_sse() as stream:
            self.assertEqual(stream.mount["type"], "mount")
            await stream.send_event("increment")
        self.assertEqual([entry[:2] for entry in _seen("increment")], [("ada", "-")] * 2)
