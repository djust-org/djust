"""``djust.testing``'s live-session helpers drive explicit views for real (#4191).

``open_live_page`` loads a page through the project's middleware, and its
``LivePage`` reaches the view over each transport a browser uses: the HTTP
fallback POST, a WebSocket and an SSE stream. Each test here checks what the
VIEW saw (its user, tenant and tab state, recorded on the server), not only
that a frame came back, so a helper that dropped the session, the user, the
tab or the request headers would fail them.
"""

from __future__ import annotations

import asyncio
import gc
import json
import types

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import include, path

from djust import LiveView
from djust.decorators import event_handler
from djust.sse import sse_urlpatterns
from djust.testing import (
    LivePage,
    LiveSocket,
    LiveSSE,
    close_live_pages,
    live_session,
    open_live_page,
    with_live_session,
)

pytestmark = [pytest.mark.django_db(transaction=True)]

#: What the views saw, in order: (where, user, tenant, count).
SEEN: list[tuple[str, str, str, int]] = []
#: The keyword arguments each mount() received.
MOUNT_KWARGS: list[dict] = []
#: The scopes the recording ASGI application saw.
SCOPES: list[dict] = []


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
        MOUNT_KWARGS.append({**kwargs, "X-Tenant-ID": request.headers.get("X-Tenant-ID")})

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


def _public(view):
    from django.contrib.auth.decorators import login_not_required

    return login_not_required(view)


urlpatterns = [
    path("t/counter/", Counter.as_view()),
    path("t/members/", Members.as_view()),
    path("t/public/", _public(Counter.as_view())),
    path("djust/", include(sse_urlpatterns)),
]
#: The same pages in a project that does not route djust's SSE views.
NO_SSE_URLCONF = types.ModuleType("no_sse_urls")
NO_SSE_URLCONF.urlpatterns = urlpatterns[:3]  # type: ignore[attr-defined]


class OnlyBackend(ModelBackend):
    """A project's own backend, with Django's ModelBackend not listed."""


def _recording_application():
    """A ``djust new`` style application with a middleware of the project's own."""
    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter
    from django.urls import path as route

    from djust.routing import DjustOriginValidator
    from djust.websocket import LiveViewConsumer

    inner = DjustOriginValidator(
        AuthMiddlewareStack(URLRouter([route("ws/live/", LiveViewConsumer.as_asgi())]))
    )

    async def recording(scope, receive, send):
        SCOPES.append(dict(scope))
        return await inner(scope, receive, send)

    return recording


application = _recording_application()

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
    # The project's own ASGI application, as a ``djust new`` project sets it.
    "ASGI_APPLICATION": "%s.application" % __name__,
}
TENANT_SETTINGS = {**SETTINGS, "DJUST_CONFIG": {"TENANT_RESOLVER": "header"}}
VIEW = "%s.Counter" % __name__


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(**SETTINGS):
        yield
    SEEN.clear()
    MOUNT_KWARGS.clear()
    SCOPES.clear()


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


def test_the_page_get_carries_the_request_extras():
    open_live_page("/t/counter/", HTTP_X_TENANT_ID="acme")
    assert [kwargs["X-Tenant-ID"] for kwargs in MOUNT_KWARGS] == ["acme"]


def test_the_page_client_enforces_csrf_as_a_browser_gets():
    page = open_live_page("/t/counter/")
    assert page.client.handler.enforce_csrf_checks
    assert page.csrf_token
    refused = page.post_event("increment", HTTP_X_CSRFTOKEN="wrong" * 8)
    assert refused.status_code == 403, refused.status_code
    assert not _seen("increment")


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


def test_the_fallback_echoes_the_render_digest_and_gets_patches():
    """The client sends back the last answer's digest, so the server answers
    with patches; ``full=True`` sends none and gets the whole page."""
    page = open_live_page("/t/counter/")
    assert page.render_digest, "the page carries its render digest"
    first = json.loads(page.post_event("increment").content)
    assert "patches" in first and "html" not in first, sorted(first)
    second = json.loads(page.post_event("increment").content)
    assert "patches" in second and "html" not in second, sorted(second)
    whole = json.loads(page.post_event("increment", full=True).content)
    assert isinstance(whole.get("html"), str), sorted(whole)
    assert [count for *_, count in _seen("increment")] == [1, 2, 3]


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


async def test_the_socket_goes_through_the_projects_asgi_application():
    """``ASGI_APPLICATION`` is used: its origin validator and auth stack read
    the socket's cookies and Origin, as a browser's."""
    user = await _auser()
    page = await sync_to_async(open_live_page)("/t/members/", user=user)
    async with await page.connect() as socket:
        assert socket.mount["type"] == "mount", socket.mount
        assert (await socket.send_event("increment"))["type"] != "error"
    assert [scope["path"] for scope in SCOPES] == ["/ws/live/"]
    assert "session" not in SCOPES[0], "the project's own middleware resolves the session"
    assert _seen("increment") == [("ada", "-", 1)]


async def test_without_an_asgi_application_the_bare_consumer_gets_the_session():
    user = await _auser()
    with override_settings(ASGI_APPLICATION=None):
        page = await sync_to_async(open_live_page)("/t/members/", user=user)
        async with await page.connect() as socket:
            assert socket.mount["type"] == "mount", socket.mount
            assert (await socket.send_event("increment"))["type"] != "error"
    assert not SCOPES
    assert _seen("increment") == [("ada", "-", 1)]


async def test_the_page_query_string_is_the_mount_params():
    page = await sync_to_async(open_live_page)("/t/counter/?doc=7&tag=a&tag=b")
    async with await page.connect():
        pass
    async with await page.connect_sse():
        pass
    page_get, socket, stream = MOUNT_KWARGS
    assert socket.get("doc") == "7" and stream.get("doc") == "7", MOUNT_KWARGS


async def test_a_receive_timeout_leaves_the_socket_usable():
    page = await sync_to_async(open_live_page)("/t/counter/")
    async with await page.connect() as socket:
        with pytest.raises(asyncio.TimeoutError):
            await socket.receive(("push_event",), timeout=0.3)
        answer = await socket.send_event("increment")
        assert answer["type"] in ("patch", "html_update"), answer
        await socket.receive_nothing(("push_event",), within=0.3)
        await socket.send_event("ping")
        with pytest.raises(AssertionError, match="expected no push_event"):
            await socket.receive_nothing(("push_event",), within=5)
    assert [count for *_, count in _seen("increment")] == [1]


async def test_a_failing_close_never_replaces_the_tests_own_error(monkeypatch):
    async def broken(self, code=1000):
        raise RuntimeError("close failed")

    page = await sync_to_async(open_live_page)("/t/counter/")
    socket = await page.connect()
    real = LiveSocket.disconnect
    monkeypatch.setattr(LiveSocket, "disconnect", broken)
    with pytest.raises(ValueError, match="the test's own failure") as caught:
        async with socket:
            raise ValueError("the test's own failure")
    assert any("close failed" in note for note in getattr(caught.value, "__notes__", []))
    monkeypatch.setattr(LiveSocket, "disconnect", real)
    await socket.disconnect()


async def test_live_session_records_the_backend_force_login_would():
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth import BACKEND_SESSION_KEY

    from djust.websocket import LiveViewConsumer

    backend = "%s.OnlyBackend" % __name__
    with override_settings(AUTHENTICATION_BACKENDS=[backend]):
        user = await _auser()
        session = await sync_to_async(live_session)(user)
        assert session[BACKEND_SESSION_KEY] == backend
        communicator = with_live_session(
            WebsocketCommunicator(
                LiveViewConsumer.as_asgi(),
                "/ws/live/",
                headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
            ),
            user,
        )
        assert (await communicator.connect())[0]
        await communicator.send_json_to(
            {"type": "mount", "view": "%s.Members" % __name__, "url": "/t/members/"}
        )
        async with LiveSocket(communicator) as socket:
            assert (await socket.receive(("mount", "error")))["type"] == "mount"
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


async def test_a_receive_timeout_leaves_the_stream_usable():
    page = await sync_to_async(open_live_page)("/t/counter/")
    async with await page.connect_sse() as stream:
        with pytest.raises(asyncio.TimeoutError):
            await stream.receive(("push_event",), timeout=0.3)
        assert stream.session is not None
        assert (await stream.send_event("increment"))["type"] in ("patch", "html_update")
    assert [count for *_, count in _seen("increment")] == [1]


async def test_closing_streams_together_leaves_the_sse_module_alone():
    """The close skips the grace period for its own session only."""
    from djust import sse

    linger = sse._SESSION_LINGER_S
    first = await sync_to_async(open_live_page)("/t/counter/")
    second = await sync_to_async(open_live_page)("/t/counter/", client=first.client)
    one, two = await first.connect_sse(), await second.connect_sse()
    assert sse._SESSION_LINGER_S == linger > 1
    await asyncio.wait_for(asyncio.gather(one.close(), two.close()), linger - 1)
    assert sse._SESSION_LINGER_S == linger
    assert one.session is None and two.session is None


async def test_an_unrouted_sse_endpoint_fails_with_how_to_route_it():
    with override_settings(ROOT_URLCONF=NO_SSE_URLCONF):
        page = await sync_to_async(open_live_page)("/t/counter/")
        with pytest.raises(AssertionError, match="include\\(sse_urlpatterns\\)"):
            await page.connect_sse()


async def test_the_sse_requests_go_through_the_projects_middleware():
    """A public page under LoginRequiredMiddleware: the page GET is allowed, the
    SSE stream URL is not public, and the browser's stream GET is redirected."""
    from django.conf import settings

    middleware = [*settings.MIDDLEWARE, "django.contrib.auth.middleware.LoginRequiredMiddleware"]
    with override_settings(MIDDLEWARE=middleware):
        page = await sync_to_async(open_live_page)("/t/public/")
        with pytest.raises(AssertionError, match="SSE stream GET answered 302"):
            await page.connect_sse()


async def test_close_live_pages_releases_what_a_test_left_open():
    page = await sync_to_async(open_live_page)("/t/counter/")
    stream = await page.connect_sse()
    assert stream.session is not None
    close_live_pages()
    assert stream.session is None and stream.closed


async def test_a_dropped_stream_is_released_by_its_finaliser():
    from djust import sse

    page = await sync_to_async(open_live_page)("/t/counter/")
    stream = await page.connect_sse()
    session_id = stream.session_id
    assert sse._get_session(session_id) is not None
    del stream, page
    gc.collect()
    assert sse._get_session(session_id) is None


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
# The frame reader, against a raw application
# --------------------------------------------------------------------------- #


def _raw_app(on_connect=(), on_frame=lambda frame: ()):
    """An ASGI WebSocket application that sends ``on_connect`` after accepting,
    and ``on_frame(frame)`` for each JSON frame the client sends."""

    async def app(scope, receive, send):
        await receive()  # websocket.connect
        await send({"type": "websocket.accept"})
        for message in on_connect:
            await send(message)
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return
            for reply in on_frame(json.loads(message["text"])):
                await send(reply)

    return app


async def _raw_socket(app) -> LiveSocket:
    from channels.testing import WebsocketCommunicator

    communicator = WebsocketCommunicator(app, "/ws/live/")
    assert (await communicator.connect())[0]
    return LiveSocket(communicator, timeout=5)


def _text(frame: dict) -> dict:
    return {"type": "websocket.send", "text": json.dumps(frame)}


async def test_a_frame_that_is_not_json_is_the_reason_the_socket_ended():
    socket = await _raw_socket(_raw_app([{"type": "websocket.send", "text": "<not json>"}]))
    with pytest.raises(AssertionError, match="not JSON: '<not json>'"):
        await socket.receive()
    await socket.disconnect()


async def test_a_binary_frame_is_a_frame():
    app = _raw_app([{"type": "websocket.send", "bytes": b"\x00\x01"}, _text({"type": "after"})])
    socket = await _raw_socket(app)
    assert await socket.receive() == {"type": "binary", "bytes": b"\x00\x01"}
    assert (await socket.receive())["type"] == "after"
    await socket.disconnect()


async def test_unread_frames_are_capped_with_a_clear_failure(monkeypatch):
    from djust import _testing_live

    monkeypatch.setattr(_testing_live, "_MAX_UNREAD_FRAMES", 5)
    socket = await _raw_socket(_raw_app([_text({"type": "push", "n": n}) for n in range(20)]))
    # Nothing is read while the 20 frames arrive: the reader stops at the cap.
    socket._start()
    await asyncio.wait({socket._pump}, timeout=5)
    assert socket._pump.done()
    got = []
    with pytest.raises(AssertionError, match="more than 5 frames arrived without being read"):
        while True:
            got.append((await socket.receive())["n"])
    assert got == [0, 1, 2, 3, 4]
    await socket.disconnect()


async def test_send_event_takes_only_its_own_or_a_refless_error():
    """An error naming another event's ref is not this event's answer; an
    error with no ref is."""

    def answer(frame):
        if frame["event"] == "other":
            yield _text({"type": "error", "ref": frame["ref"] + 99, "error": "not yours"})
            yield _text({"type": "noop", "ref": frame["ref"]})
        else:
            yield _text({"type": "error", "error": "refused"})

    socket = await _raw_socket(_raw_app(on_frame=answer))
    answered = await socket.send_event("other")
    assert answered == {"type": "noop", "ref": 1}, answered
    assert (await socket.receive(("error",)))["error"] == "not yours"
    assert (await socket.send_event("refused"))["error"] == "refused"
    await socket.disconnect()


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
    def setUp(self):
        self.addCleanup(close_live_pages)
        self.addCleanup(SEEN.clear)

    def test_fallback_and_login(self):
        user = _user()
        page = open_live_page("/t/members/", user=user)
        self.assertEqual(page.post_event("increment").status_code, 200)
        self.assertEqual(_seen("increment"), [("ada", "-", 1)])

    async def test_socket_events_work_in_the_test_transaction(self):
        user = await _auser()
        page = await sync_to_async(open_live_page)("/t/members/", user=user)
        async with await page.connect() as socket:
            self.assertEqual(socket.mount["type"], "mount")
            self.assertNotEqual((await socket.send_event("increment"))["type"], "error")
        self.assertEqual(_seen("increment"), [("ada", "-", 1)])


@override_settings(**SETTINGS, SESSION_ENGINE="django.contrib.sessions.backends.cache")
class CacheSessionsUnderTestCase(TestCase):
    """With the cache session store, socket and SSE both work in TestCase."""

    def setUp(self):
        self.addCleanup(close_live_pages)
        self.addCleanup(SEEN.clear)

    async def test_socket_and_sse(self):
        user = await _auser()
        page = await sync_to_async(open_live_page)("/t/members/", user=user)
        async with await page.connect() as socket:
            self.assertNotEqual((await socket.send_event("increment"))["type"], "error")
        async with await page.connect_sse() as stream:
            self.assertNotEqual((await stream.send_event("increment"))["type"], "error")
        self.assertEqual([entry[:2] for entry in _seen("increment")], [("ada", "-")] * 2)


@override_settings(**SETTINGS)
class SocketUnderTransactionTestCase(TransactionTestCase):
    def setUp(self):
        self.addCleanup(close_live_pages)
        self.addCleanup(SEEN.clear)

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
