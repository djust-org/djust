"""Django's ``LoginRequiredMiddleware`` (Django 5.1+) on live transports.

The middleware sends every anonymous HTTP request to the login page unless the
routed view is marked with ``login_not_required``. It runs for HTTP requests
only, so ``check_view_auth`` asks the same question for the rest:

* a view rendered inside a page (``{% live_render %}``, lazy children) takes
  its page's verdict, as Django never gates what a page includes;
* a view whose own HTTP request Django dispatched (page GET, POST fallback) is
  not checked again: the installed middleware, subclasses included, ran;
* a top-level live mount (WebSocket, SSE, API, ``mount_batch``) is public when
  the view's ``dispatch`` carries ``login_not_required``, when the request's
  route serves this class and the installed middleware lets it through, or
  when the URLconf routes this class through a ``login_not_required`` entry;
  otherwise an anonymous user is sent where the middleware would send it;
* a view refused beside the page view (``dj-lazy`` hydration, ``mount_batch``)
  is refused alone and the page keeps running;
* an authenticated user passes; without the middleware nothing changes.

djust's own ``login_required = False`` is not an exemption: the middleware does
not read class attributes either. The views below set it so the S005 check
stays quiet, which also shows that.
"""

from __future__ import annotations

import json
import sys
import uuid
from copy import copy
from unittest.mock import MagicMock

import pytest

pytest.importorskip("channels")

from django.conf import settings  # noqa: E402
from django.test import Client, RequestFactory, override_settings  # noqa: E402
from django.urls import clear_url_caches, path  # noqa: E402
from django.utils.decorators import method_decorator  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.auth._login_required_middleware import (  # noqa: E402
    login_required_by_middleware,
    mark_embedded,
    note_http_dispatch,
)
from djust.auth.core import check_view_auth  # noqa: E402
from djust.decorators import event_handler  # noqa: E402

try:
    from django.contrib.auth.decorators import login_not_required, login_required
    from django.contrib.auth.middleware import LoginRequiredMiddleware
except ImportError:  # Django < 5.1: the code path is inert, see the last test
    login_not_required = None
    login_required = None
    LoginRequiredMiddleware = object

pytestmark = pytest.mark.skipif(
    login_not_required is None, reason="LoginRequiredMiddleware needs Django 5.1+"
)

LRM = "django.contrib.auth.middleware.LoginRequiredMiddleware"
LOGIN = "/lrm-login/"
MOD = __name__
RAN: list = []
_public = login_not_required or (lambda f: f)


class _Anon:
    is_authenticated = False
    is_active = False
    is_anonymous = True
    pk = None


class _Authed:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    pk = 1

    def has_perms(self, perms):  # pragma: no cover - no permission rules here
        return True


def _template(name: str) -> str:
    return f'<div dj-view="{MOD}.{name}" dj-id="0">{name}:{{{{ n }}}}</div>'


class _PassThroughAuth:
    """API auth that admits the request's own user, anonymous included, so the
    view-level check is what decides (SessionAuth refuses anonymous first)."""

    csrf_exempt = True

    def authenticate(self, request):
        return request.user


class _LrmBase(LiveView):
    login_required = False  # not an exemption under the middleware

    def mount(self, request, **kwargs):
        self.n = 1

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class LrmPrivateView(_LrmBase):
    template = _template("LrmPrivateView")
    api_name = "lrm.members"
    api_auth_classes = [_PassThroughAuth]

    @event_handler(expose_api=True)
    def touch(self, **kwargs):
        RAN.append(type(self).__name__)
        return "touched"


class LrmPublicRouteView(LrmPrivateView):
    """Public on the route wrapped with ``login_not_required`` only."""

    template = _template("LrmPublicRouteView")
    api_name = "lrm.public"


@method_decorator(_public, name="dispatch")
class LrmMarkedView(LrmPrivateView):
    """``login_not_required`` on dispatch: public on every route."""

    template = _template("LrmMarkedView")
    api_name = "lrm.marked"


class LrmEmbeddedView(LrmPrivateView):
    """Never routed: only rendered inside a page."""

    template = _template("LrmEmbeddedView")
    api_name = "lrm.embedded"


class LrmLoginRequiredView(LrmPrivateView):
    """djust's own ``login_required = True``."""

    login_required = True
    template = _template("LrmLoginRequiredView")
    api_name = "lrm.login-required"


class LrmHomeView(LrmPrivateView):
    """A public page that embeds an unrouted, a public and a private child."""

    template = (
        "{% load live_tags %}"
        f'<div dj-view="{MOD}.LrmHomeView" dj-id="0">LrmHomeView:{{{{ n }}}} '
        f'{{% live_render "{MOD}.LrmEmbeddedView" %}} '
        f'{{% live_render "{MOD}.LrmPublicRouteView" %}} '
        f'{{% live_render "{MOD}.LrmPrivateView" %}}</div>'
    )
    api_name = "lrm.home"


class LrmPrefixView(LrmPrivateView):
    """Routed without a marker under a prefix a middleware subclass exempts."""

    template = _template("LrmPrefixView")
    api_name = "lrm.prefix"


class LrmPerViewLoginView(LrmPrivateView):
    """Routed through ``login_required(login_url=..., redirect_field_name=...)``."""

    template = _template("LrmPerViewLoginView")
    api_name = "lrm.per-view"


for _name in (
    "LrmPrivateView",
    "LrmPublicRouteView",
    "LrmMarkedView",
    "LrmEmbeddedView",
    "LrmLoginRequiredView",
    "LrmHomeView",
    "LrmPrefixView",
    "LrmPerViewLoginView",
):
    setattr(sys.modules[MOD], _name, globals()[_name])


class PrefixExemptLRM(LoginRequiredMiddleware):
    """A common subclass: exempt a URL prefix in ``process_view``."""

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path_info.startswith("/lrm/open/"):
            return None
        return super().process_view(request, view_func, view_args, view_kwargs)


class GotoLRM(LoginRequiredMiddleware):
    redirect_field_name = "goto"


sys.modules[MOD].PrefixExemptLRM = PrefixExemptLRM
sys.modules[MOD].GotoLRM = GotoLRM


def _per_view_login(view):
    if login_required is None:  # pragma: no cover - Django < 5.1
        return view
    return login_required(login_url="/lrm-per-view-login/", redirect_field_name="back")(view)


urlpatterns = [
    path("lrm/private/", LrmPrivateView.as_view()),
    path("lrm/public/", _public(LrmPublicRouteView.as_view())),
    path("lrm/public-gated/", LrmPublicRouteView.as_view()),
    path("lrm/marked/", LrmMarkedView.as_view()),
    path("lrm/home/", _public(LrmHomeView.as_view())),
    path("lrm/open/page/", LrmPrefixView.as_view()),
    path("lrm/per-view/", _per_view_login(LrmPerViewLoginView.as_view())),
    path(LOGIN.strip("/") + "/", _public(lambda r: None)),
]


def _settings(with_middleware: bool = True, middleware_path: str = LRM, **extra):
    middleware = [m for m in settings.MIDDLEWARE if "LoginRequiredMiddleware" not in m]
    if with_middleware:
        middleware.append(middleware_path)
    return override_settings(
        **{
            "MIDDLEWARE": middleware,
            "ROOT_URLCONF": MOD,
            "LOGIN_URL": LOGIN,
            "LIVEVIEW_ALLOWED_MODULES": [MOD],
            **extra,
        }
    )


@pytest.fixture
def lrm():
    with _settings(True):
        yield


@pytest.fixture
def no_lrm():
    with _settings(False):
        yield


@pytest.fixture(autouse=True)
def _reset():
    RAN.clear()
    yield
    RAN.clear()


def _get(url: str, user):
    request = RequestFactory().get(url)
    request.user = user
    return request


def _redirect(url: str) -> str:
    """The middleware's own redirect for an anonymous GET of ``url``."""
    return f"{LOGIN}?next={url}"


# --------------------------------------------------------------------------- #
# Top-level live mounts: the verdict
# --------------------------------------------------------------------------- #

# view, request url, the redirect for an anonymous user (None: public)
VERDICTS = [
    (LrmPrivateView, "/lrm/private/", _redirect("/lrm/private/")),
    (LrmPublicRouteView, "/lrm/public/", None),
    # Its own unmarked route: refused there, as Django refuses that URL.
    (LrmPublicRouteView, "/lrm/public-gated/", _redirect("/lrm/public-gated/")),
    # A path that is not its page (SSE/API endpoint, another URL): the
    # URLconf routes this class through a login_not_required entry.
    (LrmPublicRouteView, "/not-routed/", None),
    (LrmMarkedView, "/lrm/marked/", None),
    (LrmMarkedView, "/lrm/private/", None),
    # The client names the URL: a public route of another view exempts nothing.
    (LrmPrivateView, "/lrm/public/", LOGIN),
    (LrmPrivateView, "/lrm/marked/", LOGIN),
    (LrmPrivateView, "/lrm/home/", LOGIN),
    (LrmPrivateView, "/not-routed/", LOGIN),
    # A class the URLconf does not route needs the dispatch marker, whatever
    # page URL the client names.
    (LrmEmbeddedView, "/lrm/public/", LOGIN),
    (LrmEmbeddedView, "/lrm/home/", LOGIN),
    (LrmEmbeddedView, "/lrm/private/", LOGIN),
    (LrmEmbeddedView, "/not-routed/", LOGIN),
]


@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("view_cls,url,redirect", VERDICTS)
def test_verdict_anonymous(view_cls, url, redirect):
    assert login_required_by_middleware(view_cls(), _get(url, _Anon())) is (redirect is not None)
    assert check_view_auth(view_cls(), _get(url, _Anon())) == redirect


@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("view_cls,url,redirect", VERDICTS)
def test_verdict_authenticated(view_cls, url, redirect):
    assert login_required_by_middleware(view_cls(), _get(url, _Authed())) is False
    assert check_view_auth(view_cls(), _get(url, _Authed())) is None


@pytest.mark.usefixtures("no_lrm")
@pytest.mark.parametrize("view_cls,url,redirect", VERDICTS)
def test_verdict_without_middleware(view_cls, url, redirect):
    assert login_required_by_middleware(view_cls(), _get(url, _Anon())) is False
    assert check_view_auth(view_cls(), _get(url, _Anon())) is None


@pytest.mark.usefixtures("lrm")
def test_djust_login_url_applies_to_djust_login_required():
    view = LrmLoginRequiredView()
    view.login_url = "/elsewhere/"
    assert check_view_auth(view, _get("/lrm/private/", _Anon())) == "/elsewhere/"


@pytest.mark.usefixtures("lrm")
def test_resolver_match_of_the_request_is_used():
    from django.urls import resolve

    request = _get("/somewhere-else/", _Anon())
    request.resolver_match = resolve("/lrm/public/")
    assert check_view_auth(LrmPublicRouteView(), request) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "middleware_path,view_cls,url",
    [
        (LRM, LrmPerViewLoginView, "/lrm/per-view/"),
        (f"{MOD}.GotoLRM", LrmPrivateView, "/lrm/private/"),
        (f"{MOD}.GotoLRM", LrmPerViewLoginView, "/lrm/per-view/"),
    ],
)
def test_redirect_is_the_middlewares(middleware_path, view_cls, url):
    """Per-route ``login_url`` / ``redirect_field_name`` and a subclass's
    ``redirect_field_name``: the live refusal goes where HTTP goes."""
    with _settings(True, middleware_path):
        http = Client().get(url)
        assert http.status_code == 302
        assert check_view_auth(view_cls(), _get(url, _Anon())) == http["Location"]


def test_subclass_of_the_middleware_counts():
    class Custom(LoginRequiredMiddleware):
        pass

    sys.modules[MOD].Custom = Custom
    try:
        with _settings(True, f"{MOD}.Custom"):
            assert check_view_auth(LrmPrivateView(), _get("/lrm/private/", _Anon())) == (
                _redirect("/lrm/private/")
            )
    finally:
        del sys.modules[MOD].Custom


@pytest.mark.django_db
def test_subclass_exemption_holds_on_http_and_live():
    with _settings(True, f"{MOD}.PrefixExemptLRM"):
        # HTTP: exactly as without djust's check (the subclass exempts it).
        assert Client().get("/lrm/open/page/").status_code == 200
        assert Client().get("/lrm/private/").status_code == 302
        # Live, naming the page: the subclass's process_view decides.
        assert check_view_auth(LrmPrefixView(), _get("/lrm/open/page/", _Anon())) is None
        # Naming the exempt URL does not exempt another class.
        assert check_view_auth(LrmPrivateView(), _get("/lrm/open/page/", _Anon())) == LOGIN
        assert check_view_auth(LrmEmbeddedView(), _get("/lrm/open/page/", _Anon())) == LOGIN


@pytest.mark.django_db
def test_no_redirect_loop_on_a_login_page_exempted_by_a_subclass():
    """LOGIN_URL is a LiveView exempted only by the subclass's prefix rule
    (and it carries djust's own login_required, which is separate)."""

    class LoginPage(_LrmBase):
        template = _template("LoginPage")

    sys.modules[MOD].LoginPage = LoginPage
    urlpatterns.append(path("lrm/open/signin/", LoginPage.as_view()))
    clear_url_caches()
    try:
        with _settings(True, f"{MOD}.PrefixExemptLRM", LOGIN_URL="/lrm/open/signin/"):
            response = Client().get("/lrm/open/signin/")
            assert response.status_code == 200, response.get("Location")
            assert check_view_auth(LoginPage(), _get("/lrm/open/signin/", _Anon())) is None
    finally:
        urlpatterns.pop()
        clear_url_caches()
        del sys.modules[MOD].LoginPage


@pytest.mark.usefixtures("lrm")
def test_routes_follow_urlconf_reloads():
    """A class routed after first use is seen once the URLconf caches clear."""

    class LateView(_LrmBase):
        template = _template("LateView")

    sys.modules[MOD].LateView = LateView
    try:
        assert check_view_auth(LateView(), _get("/elsewhere/", _Anon())) == LOGIN
        urlpatterns.append(path("lrm/late/", _public(LateView.as_view())))
        clear_url_caches()
        assert check_view_auth(LateView(), _get("/elsewhere/", _Anon())) is None
    finally:
        urlpatterns.pop()
        clear_url_caches()
        del sys.modules[MOD].LateView


# --------------------------------------------------------------------------- #
# Requests Django dispatched, and embedded renders
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("lrm")
def test_http_dispatched_request_is_not_checked_again():
    view, request = LrmPrivateView(), _get("/lrm/private/", _Anon())
    note_http_dispatch(view, request)
    assert check_view_auth(view, request) is None
    # Only that view, only that request object.
    assert check_view_auth(view, copy(request)) == _redirect("/lrm/private/")
    assert check_view_auth(LrmPrivateView(), request) == _redirect("/lrm/private/")


@pytest.mark.usefixtures("lrm")
def test_embedded_view_takes_its_pages_verdict():
    child = LrmEmbeddedView()
    mark_embedded(child)
    assert check_view_auth(child, _get("/lrm/private/", _Anon())) is None
    assert check_view_auth(LrmEmbeddedView(), _get("/lrm/private/", _Anon())) == LOGIN
    # djust's own rules still apply to a child.
    child = LrmLoginRequiredView()
    mark_embedded(child)
    assert check_view_auth(child, _get("/lrm/public/", _Anon())) == LOGIN


@pytest.mark.django_db
@pytest.mark.usefixtures("lrm")
def test_http_get_of_a_public_page_with_children():
    """Unrouted, public-routed and private-routed children of a public page
    render for anonymous users, as Django never gates includes."""
    response = Client().get("/lrm/home/")
    assert response.status_code == 200
    body = response.content.decode()
    for name in ("LrmHomeView", "LrmEmbeddedView", "LrmPublicRouteView", "LrmPrivateView"):
        assert f"{name}:1" in body, name


@pytest.mark.usefixtures("lrm")
def test_live_render_child_follows_its_page():
    from django.template import TemplateSyntaxError

    from djust.templatetags.live_tags import live_render

    def render(child_cls, user, page_url="/lrm/public/"):
        parent = LrmPublicRouteView()
        request = _get(page_url, user)
        parent.request = request
        context = {"request": request, "view": parent}
        return live_render(context, f"{MOD}.{child_cls.__name__}")

    for child_cls in (LrmEmbeddedView, LrmPublicRouteView, LrmPrivateView):
        assert f"{child_cls.__name__}:1" in str(render(child_cls, _Anon()))
        assert f"{child_cls.__name__}:1" in str(render(child_cls, _Authed(), "/lrm/private/"))
    with pytest.raises(TemplateSyntaxError):
        render(LrmLoginRequiredView, _Anon())


# --------------------------------------------------------------------------- #
# WebSocket (the real consumer)
# --------------------------------------------------------------------------- #


def _scoped(app, user):
    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = user
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


async def _ws_mount(user, view_cls, url):
    """Return ("mount", html) or ("navigate", frame) + whether 4403 closed."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    socket = WebsocketCommunicator(_scoped(LiveViewConsumer.as_asgi(), user), "/ws/")
    connected, _ = await socket.connect()
    assert connected
    try:
        await socket.receive_json_from(timeout=2)
    except Exception:  # noqa: BLE001 - the connect ack is optional here
        pass
    await socket.send_json_to({"type": "mount", "view": f"{MOD}.{view_cls.__name__}", "url": url})
    frame = await socket.receive_json_from(timeout=3)
    closed = None
    if frame.get("type") == "navigate":
        out = await socket.receive_output(timeout=2)
        closed = out.get("code") if out.get("type") == "websocket.close" else None
    await socket.disconnect()
    return frame, closed


WS_CASES = [
    (LrmPrivateView, "/lrm/private/", True),
    (LrmPrivateView, "/lrm/public/", True),
    (LrmPublicRouteView, "/lrm/public/", False),
    (LrmPublicRouteView, "/lrm/public-gated/", True),
    (LrmMarkedView, "/lrm/marked/", False),
    (LrmHomeView, "/lrm/home/", False),
    # An unrouted class named with a public page's URL is refused.
    (LrmEmbeddedView, "/lrm/public/", True),
    (LrmEmbeddedView, "/lrm/home/", True),
    (LrmEmbeddedView, "/lrm/private/", True),
]


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("view_cls,url,refused", WS_CASES)
async def test_ws_anonymous(view_cls, url, refused):
    frame, closed = await _ws_mount(_Anon(), view_cls, url)
    if refused:
        assert frame.get("type") == "navigate", frame
        assert LOGIN in json.dumps(frame)
        assert closed == 4403
    else:
        assert frame.get("type") == "mount", frame
        assert f"{view_cls.__name__}:1" in (frame.get("html") or "")


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_public_page_renders_its_children():
    frame, _ = await _ws_mount(_Anon(), LrmHomeView, "/lrm/home/")
    assert frame.get("type") == "mount", frame
    for name in ("LrmEmbeddedView", "LrmPublicRouteView", "LrmPrivateView"):
        assert f"{name}:1" in (frame.get("html") or ""), name


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("view_cls,url,refused", WS_CASES)
async def test_ws_authenticated(view_cls, url, refused):
    frame, _ = await _ws_mount(_Authed(), view_cls, url)
    assert frame.get("type") == "mount", frame


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_lrm")
async def test_ws_without_middleware_unchanged():
    frame, _ = await _ws_mount(_Anon(), LrmPrivateView, "/lrm/private/")
    assert frame.get("type") == "mount", frame


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_ws_subclass_exemption():
    with _settings(True, f"{MOD}.PrefixExemptLRM"):
        frame, _ = await _ws_mount(_Anon(), LrmPrefixView, "/lrm/open/page/")
        assert frame.get("type") == "mount", frame
        frame, closed = await _ws_mount(_Anon(), LrmEmbeddedView, "/lrm/open/page/")
        assert frame.get("type") == "navigate" and closed == 4403, frame


# --------------------------------------------------------------------------- #
# SSE (the shared runtime, against the stream's real request)
# --------------------------------------------------------------------------- #


async def _sse_mount(user, view_cls, request_path="/djust/sse/stream/"):
    from djust import sse

    request = RequestFactory().get(request_path)
    request.user = user
    request.session = MagicMock()
    session = sse.SSESession(str(uuid.uuid4()))
    session._owner_user_pk = getattr(user, "pk", None)
    legacy_mount = getattr(sse, "_sse_mount_view", None)  # releases before the runtime
    if legacy_mount is not None:
        mounted = await legacy_mount(session, request, f"{MOD}.{view_cls.__name__}")
    else:
        session._request = request
        await session.runtime.dispatch_mount(
            {"type": "mount", "view": f"{MOD}.{view_cls.__name__}", "url": "/lrm/x/", "params": {}}
        )
        mounted = session.runtime.view_instance is not None
    msgs = []
    while not session.queue.empty():
        msgs.append(session.queue.get_nowait())
    return mounted, [m for m in msgs if m]


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("view_cls", [LrmPrivateView, LrmEmbeddedView])
async def test_sse_anonymous_refused(view_cls):
    mounted, msgs = await _sse_mount(_Anon(), view_cls)
    assert mounted is False
    assert any(m.get("type") == "navigate" and LOGIN in json.dumps(m) for m in msgs), msgs


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_sse_authenticated_allowed():
    mounted, msgs = await _sse_mount(_Authed(), LrmPrivateView)
    assert mounted is True
    assert any(m.get("type") == "mount" for m in msgs), msgs


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("view_cls", [LrmMarkedView, LrmPublicRouteView])
async def test_sse_public_views_allowed_anonymously(view_cls):
    """The dispatch marker, or a login_not_required route of the class."""
    mounted, _ = await _sse_mount(_Anon(), view_cls)
    assert mounted is True


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_lrm")
async def test_sse_without_middleware_unchanged():
    mounted, _ = await _sse_mount(_Anon(), LrmPrivateView)
    assert mounted is True


# --------------------------------------------------------------------------- #
# HTTP POST fallback: an HTTP request the middleware itself processes
# --------------------------------------------------------------------------- #

POST_CASES = [
    (LrmPrivateView, "/lrm/private/", True),
    (LrmPublicRouteView, "/lrm/public/", False),
    (LrmPublicRouteView, "/lrm/public-gated/", True),
    (LrmMarkedView, "/lrm/marked/", False),
]


def _client_post(url):
    return Client().post(
        url, data="{}", content_type="application/json", HTTP_X_DJUST_EVENT="touch"
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("view_cls,url,refused", POST_CASES)
def test_post_fallback_anonymous(view_cls, url, refused):
    response = _client_post(url)
    if refused:
        assert response.status_code == 302
        assert response["Location"] == _redirect(url)
        assert RAN == []
    else:
        assert response.status_code == 200, response.content
        assert RAN == [view_cls.__name__]


@pytest.mark.django_db
def test_post_fallback_subclass_exemption():
    with _settings(True, f"{MOD}.PrefixExemptLRM"):
        response = _client_post("/lrm/open/page/")
        assert response.status_code == 200, response.content
        assert RAN == ["LrmPrefixView"]


@pytest.mark.django_db
@pytest.mark.usefixtures("lrm")
def test_post_fallback_authenticated():
    from django.contrib.sessions.middleware import SessionMiddleware

    request = RequestFactory().post(
        "/lrm/private/", data="{}", content_type="application/json", HTTP_X_DJUST_EVENT="touch"
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user, request.tenant = _Authed(), None
    response = LrmPrivateView().post(request)
    assert response.status_code == 200, response.content
    assert RAN == ["LrmPrivateView"]


@pytest.mark.django_db
@pytest.mark.usefixtures("no_lrm")
def test_post_fallback_without_middleware_unchanged():
    response = _client_post("/lrm/private/")
    assert response.status_code == 200, response.content


# --------------------------------------------------------------------------- #
# API dispatch (the request is the API endpoint's)
# --------------------------------------------------------------------------- #


@pytest.fixture
def _api():
    from djust.api.dispatch import reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry

    reset_registry()
    reset_rate_buckets()
    for cls in (LrmPrivateView, LrmMarkedView, LrmPublicRouteView, LrmEmbeddedView):
        register_api_view(cls.api_name, cls)
    yield
    reset_registry()
    reset_rate_buckets()


def _api_call(slug, user):
    from django.contrib.sessions.middleware import SessionMiddleware

    from djust.api.dispatch import dispatch_api

    request = RequestFactory().post(
        f"/djust/api/{slug}/touch/", data=b"{}", content_type="application/json"
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.user = user
    request._dont_enforce_csrf_checks = True
    return dispatch_api(request, slug, "touch")


@pytest.mark.django_db
@pytest.mark.usefixtures("lrm", "_api")
def test_api_dispatch():
    assert _api_call("lrm.members", _Anon()).status_code == 401
    assert _api_call("lrm.embedded", _Anon()).status_code == 401
    assert RAN == []
    assert _api_call("lrm.members", _Authed()).status_code == 200
    assert _api_call("lrm.marked", _Anon()).status_code == 200
    assert _api_call("lrm.public", _Anon()).status_code == 200
    assert RAN == ["LrmPrivateView", "LrmMarkedView", "LrmPublicRouteView"]


@pytest.mark.django_db
@pytest.mark.usefixtures("no_lrm", "_api")
def test_api_dispatch_without_middleware_unchanged():
    assert _api_call("lrm.members", _Anon()).status_code == 200


# --------------------------------------------------------------------------- #
# Inert before Django 5.1; parity with the middleware on HTTP
# --------------------------------------------------------------------------- #


def test_inert_without_django_5_1(monkeypatch):
    import builtins

    from djust.auth import _login_required_middleware as core

    real_import = builtins.__import__

    def no_lrm(name, *args, **kwargs):
        if name == "django.contrib.auth.middleware" and "LoginRequiredMiddleware" in (
            args[2] if len(args) > 2 and args[2] else ()
        ):
            raise ImportError("no LoginRequiredMiddleware before Django 5.1")
        return real_import(name, *args, **kwargs)

    core._installed_middleware.cache_clear()
    monkeypatch.setattr(builtins, "__import__", no_lrm)
    try:
        with _settings(True):
            assert (
                login_required_by_middleware(LrmPrivateView(), _get("/lrm/private/", _Anon()))
                is False
            )
            assert check_view_auth(LrmPrivateView(), _get("/lrm/private/", _Anon())) is None
    finally:
        monkeypatch.undo()
        core._installed_middleware.cache_clear()


@pytest.mark.django_db
@pytest.mark.parametrize("middleware_path", [LRM, f"{MOD}.PrefixExemptLRM"])
@pytest.mark.parametrize(
    "view_cls,url",
    [
        (LrmPrivateView, "/lrm/private/"),
        (LrmPublicRouteView, "/lrm/public/"),
        (LrmPublicRouteView, "/lrm/public-gated/"),
        (LrmMarkedView, "/lrm/marked/"),
        (LrmHomeView, "/lrm/home/"),
        (LrmPrefixView, "/lrm/open/page/"),
        (LrmPerViewLoginView, "/lrm/per-view/"),
    ],
)
def test_same_verdict_as_the_middleware_on_http(middleware_path, view_cls, url):
    with _settings(True, middleware_path):
        response = Client().get(url)
        expected = response["Location"] if response.status_code == 302 else None
        assert check_view_auth(view_cls(), _get(url, _Anon())) == expected


# --------------------------------------------------------------------------- #
# Views beside the page view (dj-lazy hydration, mount_batch): the middleware's
# refusal refuses that view only and the page keeps running, as on HTTP, where
# the middleware never gates what a page includes.
# --------------------------------------------------------------------------- #


async def _ws_open(user):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    socket = WebsocketCommunicator(_scoped(LiveViewConsumer.as_asgi(), user), "/ws/")
    connected, _ = await socket.connect()
    assert connected
    try:
        await socket.receive_json_from(timeout=2)
    except Exception:  # noqa: BLE001 - the connect ack is optional here
        pass
    return socket


async def _ws_frame(socket, *types):
    while True:
        frame = await socket.receive_json_from(timeout=3)
        if frame.get("type") in types:
            return frame


async def _ws_public_page(user):
    socket = await _ws_open(user)
    await socket.send_json_to(
        {"type": "mount", "view": f"{MOD}.LrmPublicRouteView", "url": "/lrm/public/"}
    )
    frame = await _ws_frame(socket, "mount", "navigate", "error")
    assert frame.get("type") == "mount", frame
    return socket


async def _ws_page_event_runs(socket):
    RAN.clear()
    await socket.send_json_to({"type": "event", "event": "touch", "params": {}, "ref": 7})
    frames = [await socket.receive_json_from(timeout=3)]
    # (a timed-out receive cancels the consumer, so wait for quiet instead)
    while not await socket.receive_nothing(timeout=0.3):
        frames.append(await socket.receive_json_from(timeout=3))
    assert RAN == ["LrmPublicRouteView"], frames
    assert not any(f.get("type") in ("error", "navigate") for f in frames), frames


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
@pytest.mark.parametrize("target_id", [None, "lazy-slot"])
@pytest.mark.parametrize("view_cls", [LrmEmbeddedView, LrmPrivateView])
async def test_ws_lazy_view_beside_a_public_page_is_refused_alone(view_cls, target_id):
    socket = await _ws_public_page(_Anon())
    try:
        mount = {"type": "mount", "view": f"{MOD}.{view_cls.__name__}", "url": "/lrm/public/"}
        if target_id:
            mount["target_id"] = target_id
        await socket.send_json_to(mount)
        frame = await _ws_frame(socket, "mount", "navigate", "error")
        assert frame.get("type") == "error" and frame.get("code") == "login_required", frame
        # No navigate frame and no close: the page stays where it is.
        assert await socket.receive_nothing(timeout=0.3)
        await _ws_page_event_runs(socket)
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_mount_batch_beside_a_public_page_refuses_entries_alone():
    socket = await _ws_public_page(_Anon())
    try:
        await socket.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": f"{MOD}.LrmEmbeddedView", "url": "/lrm/public/", "target_id": "u"},
                    {"view": f"{MOD}.LrmPrivateView", "url": "/lrm/public/", "target_id": "p"},
                ],
            }
        )
        reply = await _ws_frame(socket, "mount_batch")
        assert reply.get("views") == [], reply
        assert {f["target_id"]: f["error"] for f in reply["failed"]} == {
            "u": "Login required",
            "p": "Login required",
        }, reply
        assert "navigate" not in reply, reply
        assert await socket.receive_nothing(timeout=0.3)
        await _ws_page_event_runs(socket)
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_mount_batch_mixed_entries():
    """Public entries mount, middleware refusals fail alone, and a view's own
    ``login_required`` keeps its redirect."""
    socket = await _ws_public_page(_Anon())
    try:
        await socket.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": f"{MOD}.LrmEmbeddedView", "url": "/lrm/public/", "target_id": "u"},
                    {"view": f"{MOD}.LrmMarkedView", "url": "/lrm/public/", "target_id": "m"},
                    {
                        "view": f"{MOD}.LrmLoginRequiredView",
                        "url": "/lrm/public/",
                        "target_id": "own",
                    },
                ],
            }
        )
        reply = await _ws_frame(socket, "mount_batch")
        assert [v["target_id"] for v in reply["views"]] == ["m"], reply
        assert [f["target_id"] for f in reply["failed"]] == ["u"], reply
        assert [n["target_id"] for n in reply.get("navigate", [])] == ["own"], reply
        assert await socket.receive_nothing(timeout=0.3)
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_views_beside_the_page_mount_for_a_signed_in_user():
    socket = await _ws_public_page(_Authed())
    try:
        await socket.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": f"{MOD}.LrmEmbeddedView", "url": "/lrm/public/", "target_id": "u"},
                    {"view": f"{MOD}.LrmPrivateView", "url": "/lrm/public/", "target_id": "p"},
                ],
            }
        )
        reply = await _ws_frame(socket, "mount_batch")
        assert [v["target_id"] for v in reply["views"]] == ["u", "p"], reply
        assert reply["failed"] == [], reply
        await socket.send_json_to(
            {"type": "mount", "view": f"{MOD}.LrmEmbeddedView", "url": "/lrm/public/"}
        )
        frame = await _ws_frame(socket, "mount", "navigate", "error")
        assert frame.get("type") == "mount", frame
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_first_mount_is_the_page_and_is_redirected():
    """The first view on a socket is the page view: refused, it moves the page."""
    socket = await _ws_open(_Anon())
    try:
        await socket.send_json_to(
            {"type": "mount", "view": f"{MOD}.LrmEmbeddedView", "url": "/lrm/public/"}
        )
        frame = await _ws_frame(socket, "mount", "navigate", "error")
        assert frame.get("type") == "navigate" and LOGIN in json.dumps(frame), frame
        out = await socket.receive_output(timeout=2)
        assert out.get("type") == "websocket.close" and out.get("code") == 4403, out
    finally:
        await socket.disconnect()


class LrmTickingView(LrmEmbeddedView):
    """Unrouted, with a tick: its mount sets up a tick task before auth runs."""

    template = _template("LrmTickingView")
    api_name = "lrm.ticking"
    tick_interval = 60_000

    def handle_tick(self):
        pass


sys.modules[MOD].LrmTickingView = LrmTickingView
_CONSUMERS: list = []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_refusal_beside_the_page_leaves_the_consumer_as_it_was():
    import asyncio

    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Capturing(LiveViewConsumer):
        async def connect(self):
            _CONSUMERS.append(self)
            await super().connect()

    _CONSUMERS.clear()
    socket = WebsocketCommunicator(_scoped(_Capturing.as_asgi(), _Anon()), "/ws/")
    connected, _ = await socket.connect()
    assert connected
    try:
        await socket.send_json_to(
            {"type": "mount", "view": f"{MOD}.LrmPublicRouteView", "url": "/lrm/public/"}
        )
        assert (await _ws_frame(socket, "mount", "navigate", "error"))["type"] == "mount"
        consumer = _CONSUMERS[-1]
        page_view = consumer.view_instance
        view_group = getattr(consumer, "_view_group", None)
        tick_task = getattr(consumer, "_tick_task", None)
        assert type(page_view) is LrmPublicRouteView and view_group
        await socket.send_json_to(
            {"type": "mount", "view": f"{MOD}.LrmTickingView", "url": "/lrm/public/"}
        )
        frame = await _ws_frame(socket, "mount", "navigate", "error")
        assert frame.get("code") == "login_required", frame
        assert consumer.view_instance is page_view
        assert getattr(consumer, "_view_group", None) == view_group
        assert getattr(consumer, "_tick_task", None) is tick_task
        await _ws_page_event_runs(socket)
        ticking = [
            t for t in asyncio.all_tasks() if "_run_tick" in repr(t.get_coro()) and not t.done()
        ]
        assert ticking == [], ticking
        from djust.push import view_group_name

        members = getattr(consumer.channel_layer, "groups", {})
        assert consumer.channel_name in members.get(view_group, {})
        assert consumer.channel_name not in members.get(
            view_group_name(f"{MOD}.LrmTickingView"), {}
        )
    finally:
        await socket.disconnect()


# --------------------------------------------------------------------------- #
# A refusal beside the page view also leaves the observability registry on the
# page view, and a view relying on permission checks alone is refused alone.
# --------------------------------------------------------------------------- #


class LrmPermissionOnlyView(LrmEmbeddedView):
    """Unrouted, with ``permission_required`` and no ``login_required``."""

    template = _template("LrmPermissionOnlyView")
    api_name = "lrm.permission-only"
    permission_required = "auth.view_user"


sys.modules[MOD].LrmPermissionOnlyView = LrmPermissionOnlyView


async def _ws_capturing(user):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Capturing(LiveViewConsumer):
        async def connect(self):
            _CONSUMERS.append(self)
            await super().connect()

    _CONSUMERS.clear()
    socket = WebsocketCommunicator(_scoped(_Capturing.as_asgi(), user), "/ws/")
    connected, _ = await socket.connect()
    assert connected
    return socket, _CONSUMERS[-1]


async def _ws_capturing_public_page(user):
    socket, consumer = await _ws_capturing(user)
    await socket.send_json_to(
        {"type": "mount", "view": f"{MOD}.LrmPublicRouteView", "url": "/lrm/public/"}
    )
    assert (await _ws_frame(socket, "mount", "navigate", "error"))["type"] == "mount"
    return socket, consumer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_refusal_beside_the_page_keeps_the_page_view_observed():
    import gc

    from djust.observability.registry import get_view_for_session

    socket, consumer = await _ws_capturing_public_page(_Anon())
    try:
        page_view = consumer.view_instance
        assert get_view_for_session(consumer.session_id) is page_view
        for extra in ({}, {"target_id": "lz"}):
            await socket.send_json_to(
                {"type": "mount", "view": f"{MOD}.LrmEmbeddedView", "url": "/lrm/public/", **extra}
            )
            frame = await _ws_frame(socket, "mount", "navigate", "error")
            assert frame.get("code") == "login_required", frame
            gc.collect()
            assert get_view_for_session(consumer.session_id) is page_view, extra
        await socket.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": f"{MOD}.LrmEmbeddedView", "url": "/lrm/public/", "target_id": "u"}
                ],
            }
        )
        reply = await _ws_frame(socket, "mount_batch")
        assert [f["target_id"] for f in reply["failed"]] == ["u"], reply
        gc.collect()
        assert get_view_for_session(consumer.session_id) is page_view
        await _ws_page_event_runs(socket)
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_refused_first_mount_with_a_target_leaves_no_view_observed():
    from djust.observability.registry import get_view_for_session

    socket, consumer = await _ws_capturing(_Anon())
    try:
        await socket.send_json_to(
            {
                "type": "mount",
                "view": f"{MOD}.LrmEmbeddedView",
                "url": "/lrm/public/",
                "target_id": "lz",
            }
        )
        frame = await _ws_frame(socket, "mount", "navigate", "error")
        assert frame.get("code") == "login_required", frame
        assert consumer.view_instance is None
        assert get_view_for_session(consumer.session_id) is None
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("lrm")
async def test_ws_permission_only_view_beside_the_page_is_refused_alone():
    """Without its own ``login_required``, the middleware's answer comes before
    the view's permission checks: refused for an anonymous user beside the
    page view, it fails alone like any other view there."""
    socket = await _ws_public_page(_Anon())
    try:
        await socket.send_json_to(
            {"type": "mount", "view": f"{MOD}.LrmPermissionOnlyView", "url": "/lrm/public/"}
        )
        frame = await _ws_frame(socket, "mount", "navigate", "error")
        assert frame.get("type") == "error" and frame.get("code") == "login_required", frame
        assert await socket.receive_nothing(timeout=0.3)
        await _ws_page_event_runs(socket)
    finally:
        await socket.disconnect()
