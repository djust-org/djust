"""#2998 — ``{% csrf_token %}`` rendered over the socket must match the browser.

The socket mount path rebuilds the view's request with ``RequestFactory``. It
carried the session and user but no cookies, and ``CsrfViewMiddleware`` never
ran on it, so ``get_token()`` invented a new secret the browser never received.
Any form rendered over the socket (a ``dj-navigate`` target, a re-mount) then
posted a token that failed with "CSRF verification failed" (403).

The regression test follows djust.org#79: a CSRF-enforcing browser loads the
page over HTTP, the same view is mounted over a real ``WebsocketCommunicator``
carrying that browser's cookies, and the token from the mounted HTML is POSTed
back. It must succeed (302), not 403.
"""

from __future__ import annotations

import re
import sys

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponseRedirect
from django.middleware.csrf import _unmask_cipher_token
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView

pytest.importorskip("channels")

_TEMPLATE = (
    '<div dj-root dj-view="djust.tests.test_csrf_socket_render_2998._FormView">'
    '<form method="post" action="/csrf-2998/submit/">{% csrf_token %}'
    '<button type="submit">Sign out</button></form></div>'
)


class _FormView(LiveView):
    template = _TEMPLATE

    def mount(self, request, **kwargs):
        self.ready = True


setattr(sys.modules[__name__], "_FormView", _FormView)
_VIEW_PATH = f"{__name__}._FormView"


def _submit(request):
    # A plain Django view: CsrfViewMiddleware guards it like any POST form.
    return HttpResponseRedirect("/done/")


urlpatterns = [
    path("csrf-2998/", _FormView.as_view()),
    path("csrf-2998/submit/", _submit),
]

_TOKEN = re.compile(
    r'name="csrfmiddlewaretoken"[^>]*?value="([^"]+)"|value="([^"]+)"[^>]*?name="csrfmiddlewaretoken"'
)


def _token(html: str) -> str:
    match = _TOKEN.search(html)
    assert match, f"no csrf token in {html[:300]!r}"
    return match.group(1) or match.group(2)


def _socket_app():
    from channels.auth import AuthMiddlewareStack

    from djust.websocket import LiveViewConsumer

    return AuthMiddlewareStack(LiveViewConsumer.as_asgi())


async def _mount_html(cookie_header: str, *, app=None) -> str:
    from channels.testing import WebsocketCommunicator

    socket = WebsocketCommunicator(
        app or _socket_app(),
        "/ws/live/",
        headers=[
            (b"host", b"testserver"),
            (b"origin", b"http://testserver"),
            (b"cookie", cookie_header.encode()),
        ],
    )
    connected, _ = await socket.connect()
    assert connected
    try:
        await socket.send_json_to({"type": "mount", "view": _VIEW_PATH, "url": "/csrf-2998/"})
        for _ in range(20):
            frame = await socket.receive_json_from(timeout=10)
            assert frame.get("type") != "error", frame
            if frame.get("type") == "mount":
                return frame["html"]
    finally:
        await socket.disconnect()
    raise AssertionError("no mount frame")


def _cookie_header(browser: Client, *names: str) -> str:
    return "; ".join(f"{n}={browser.cookies[n].value}" for n in names if n in browser.cookies)


@pytest.fixture
def socket_settings(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]
    return settings


def _browser_with_page() -> tuple[Client, str]:
    browser = Client(enforce_csrf_checks=True)
    page = browser.get("/csrf-2998/")
    assert page.status_code == 200
    return browser, page.content.decode()


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("socket_settings")
class TestSocketRenderedTokenPosts:
    def test_http_rendered_token_posts(self):
        """Baseline: the HTTP-rendered form posts successfully."""
        browser, html = _browser_with_page()
        response = browser.post("/csrf-2998/submit/", {"csrfmiddlewaretoken": _token(html)})
        assert response.status_code == 302

    async def test_socket_rendered_token_posts(self):
        """The #2998 regression: the socket-rendered token is accepted."""
        browser, _ = await sync_to_async(_browser_with_page)()
        cookies = _cookie_header(browser, settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME)
        html = await _mount_html(cookies)

        response = await sync_to_async(browser.post)(
            "/csrf-2998/submit/", {"csrfmiddlewaretoken": _token(html)}
        )
        assert response.status_code == 302

    async def test_socket_token_unmasks_to_the_browser_cookie(self):
        browser, _ = await sync_to_async(_browser_with_page)()
        cookies = _cookie_header(browser, settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME)
        html = await _mount_html(cookies)
        assert (
            _unmask_cipher_token(_token(html)) == browser.cookies[settings.CSRF_COOKIE_NAME].value
        )

    async def test_bare_consumer_reads_the_cookie_header(self):
        """Without Channels' cookie middleware (no ``scope["cookies"]``) the raw
        ``cookie`` header is still honoured."""
        from djust.websocket import LiveViewConsumer

        browser, _ = await sync_to_async(_browser_with_page)()
        cookies = _cookie_header(browser, settings.CSRF_COOKIE_NAME)
        html = await _mount_html(cookies, app=LiveViewConsumer.as_asgi())
        response = await sync_to_async(browser.post)(
            "/csrf-2998/submit/", {"csrfmiddlewaretoken": _token(html)}
        )
        assert response.status_code == 302

    async def test_socket_without_a_csrf_cookie_still_renders(self):
        browser, _ = await sync_to_async(_browser_with_page)()
        html = await _mount_html(_cookie_header(browser, settings.SESSION_COOKIE_NAME))
        assert _token(html)

    async def test_malformed_csrf_cookie_still_renders(self):
        html = await _mount_html(f"{settings.CSRF_COOKIE_NAME}=not-a-valid-token!")
        assert _token(html)


@pytest.mark.django_db(transaction=True)
class TestCsrfUseSessions:
    @pytest.fixture(autouse=True)
    def _use_sessions(self, socket_settings):
        socket_settings.CSRF_USE_SESSIONS = True

    async def test_socket_rendered_token_posts_with_session_csrf(self):
        browser, _ = await sync_to_async(_browser_with_page)()
        cookies = _cookie_header(browser, settings.SESSION_COOKIE_NAME)
        html = await _mount_html(cookies)
        response = await sync_to_async(browser.post)(
            "/csrf-2998/submit/", {"csrfmiddlewaretoken": _token(html)}
        )
        assert response.status_code == 302


# --------------------------------------------------------------------------- #
# The helper, and the other rebuild sites.
# --------------------------------------------------------------------------- #
_SECRET = "a" * 32


class TestBindCsrfCookie:
    def test_binds_from_scope_cookies(self):
        from djust.security.csrf import bind_csrf_cookie

        request = RequestFactory().get("/")
        bind_csrf_cookie(request, {"cookies": {settings.CSRF_COOKIE_NAME: _SECRET}})
        assert request.META["CSRF_COOKIE"] == _SECRET

    def test_binds_from_raw_cookie_header(self):
        from djust.security.csrf import bind_csrf_cookie

        request = RequestFactory().get("/")
        header = f"other=1; {settings.CSRF_COOKIE_NAME}={_SECRET}".encode()
        bind_csrf_cookie(request, {"headers": [(b"cookie", header)]})
        assert request.META["CSRF_COOKIE"] == _SECRET

    def test_legacy_masked_cookie_unmasks(self):
        from django.middleware.csrf import _mask_cipher_secret

        from djust.security.csrf import bind_csrf_cookie

        request = RequestFactory().get("/")
        bind_csrf_cookie(
            request, {"cookies": {settings.CSRF_COOKIE_NAME: _mask_cipher_secret(_SECRET)}}
        )
        assert request.META["CSRF_COOKIE"] == _SECRET

    def test_already_bound_request_is_untouched(self):
        from djust.security.csrf import bind_csrf_cookie

        request = RequestFactory().get("/")
        request.META["CSRF_COOKIE"] = "b" * 32
        bind_csrf_cookie(request, {"cookies": {settings.CSRF_COOKIE_NAME: _SECRET}})
        assert request.META["CSRF_COOKIE"] == "b" * 32

    def test_no_cookie_leaves_request_unbound(self):
        from djust.security.csrf import bind_csrf_cookie

        request = RequestFactory().get("/")
        bind_csrf_cookie(request, {})
        bind_csrf_cookie(request, None)
        assert "CSRF_COOKIE" not in request.META

    @override_settings(CSRF_USE_SESSIONS=True)
    def test_use_sessions_without_a_session_does_not_raise(self):
        from djust.security.csrf import bind_csrf_cookie

        request = RequestFactory().get("/")
        bind_csrf_cookie(request, {})  # ImproperlyConfigured is swallowed
        assert "CSRF_COOKIE" not in request.META

    @override_settings(CSRF_USE_SESSIONS=True)
    async def test_async_bind_reads_the_session_off_the_event_loop(self):
        """``abind_csrf_cookie`` hops to a thread for CSRF_USE_SESSIONS (the
        session read may hit the database)."""
        from djust.security.csrf import abind_csrf_cookie

        class _Session(dict):
            def __getitem__(self, key):
                from django.utils.asyncio import async_unsafe

                return async_unsafe("session")(dict.__getitem__)(self, key)

            get = lambda self, key, default=None: self[key] if key in self else default  # noqa: E731

        request = RequestFactory().get("/")
        from django.middleware.csrf import CSRF_SESSION_KEY

        request.session = _Session({CSRF_SESSION_KEY: _SECRET})
        await abind_csrf_cookie(request, {})
        assert request.META["CSRF_COOKIE"] == _SECRET


class _StickyStub:
    """Old view whose sticky staging records the request it is handed."""

    def __init__(self):
        self.seen = None

    def _preserve_sticky_children(self, new_request):
        self.seen = new_request.META.get("CSRF_COOKIE")
        return {}


@pytest.mark.django_db
class TestOtherRebuildSites:
    async def test_live_redirect_sticky_request_is_bound(self, socket_settings):
        """Sticky children are re-stamped with the live_redirect request; it
        carries the browser's secret."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.scope = {"cookies": {settings.CSRF_COOKIE_NAME: _SECRET}}
        consumer._view_group = None
        consumer._tick_task = None
        old = _StickyStub()
        consumer.view_instance = old

        async def _no_mount(*args, **kwargs):
            return None

        consumer.handle_mount = _no_mount
        await consumer.handle_live_redirect_mount(
            {"type": "live_redirect_mount", "url": "/csrf-2998/"}
        )
        assert old.seen == _SECRET

    async def test_sse_real_request_is_bound(self):
        """SSE mounts against the real stream request; it is bound too, even
        when CsrfViewMiddleware did not run on it."""
        from djust.runtime import ViewRuntime

        real = RequestFactory().get("/")
        real.COOKIES[settings.CSRF_COOKIE_NAME] = _SECRET

        class _Transport:
            def build_request(self):
                return real

        runtime = ViewRuntime.__new__(ViewRuntime)
        runtime.transport = _Transport()
        request = await runtime._build_request(page_url="/", params={})
        assert request is real
        assert request.META["CSRF_COOKIE"] == _SECRET

    async def test_ws_runtime_request_is_bound(self):
        from djust.runtime import ViewRuntime

        class _Transport:
            def build_request(self):
                return None

        runtime = ViewRuntime.__new__(ViewRuntime)
        runtime.transport = _Transport()
        runtime.scope = {"cookies": {settings.CSRF_COOKIE_NAME: _SECRET}}
        request = await runtime._build_request(page_url="/", params={})
        assert request.META["CSRF_COOKIE"] == _SECRET
