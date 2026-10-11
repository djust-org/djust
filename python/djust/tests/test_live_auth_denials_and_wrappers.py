"""HTTP-only access control on live transports: denials, URLconf wrappers,
decorators built on ``user_passes_test``, ``post()``-only rules, async tests.

Companion to ``test_live_auth_outside_declarations.py`` (same rule, more
shapes). Every runtime item runs over a real transport: the WebSocket
consumer, the SSE runtime mount, or the view's own ``post()``.

* A user check combined with any denial (``redirect()``,
  ``HttpResponseRedirect``, ``raise Http404`` or any other raise) is a gate:
  refused on live paths until a declared rule covers it. A declared
  ``login_required`` covers a login-only gate only.
* A LiveView routed through a URLconf wrapper (``login_required(V.as_view())``,
  ``user_passes_test(...)(V.as_view())``, an ``as_view()`` override) gets the
  same replay on live paths. The wrappers are read from the server's URLconf,
  not from the URL a client sends; a project wrapper whose code checks the
  user is refused; an unknown wrapper follows the line's unknown-decorator
  policy (``EXPECT_UNKNOWN_REFUSED``).
* A third-party decorator built on ``user_passes_test`` is replayed.
* A decorator applied only to ``post`` applies to events, not to the mount.
* An async ``user_passes_test`` test is awaited, not counted as a failure.
* A gate in ``setup()`` is refused; djust-tenants' mixin gets no V013; a
  refused view defined by an installed package is a V013 warning, not S012.

Gate-off: see the "Revision round 3" notes; each item has tests that fail
when its code path is disabled.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("channels")

from django.contrib.auth.decorators import login_required, user_passes_test  # noqa: E402
from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402
from django.core.exceptions import PermissionDenied  # noqa: E402
from django.http import Http404, HttpResponseRedirect  # noqa: E402
from django.shortcuts import redirect  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402
from django.urls import clear_url_caches, path  # noqa: E402
from django.utils.decorators import method_decorator  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.auth import core  # noqa: E402
from djust.auth.core import check_view_auth, live_auth_refusals  # noqa: E402
from djust.decorators import event_handler  # noqa: E402

#: This line's policy for a decorator or URLconf wrapper djust cannot classify.
EXPECT_UNKNOWN_REFUSED = False

RAN: list = []
_MOD = __name__


class _AnonUser:
    is_authenticated = False
    is_active = False
    is_anonymous = True
    is_staff = False
    is_superuser = False
    pk = None

    def has_perms(self, perms):
        return False

    def is_verified(self):
        return False


class _MemberUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    is_superuser = False
    pk = 1

    def has_perms(self, perms):
        return False

    def is_verified(self):
        return False


class _StaffUser(_MemberUser):
    is_staff = True
    pk = 2


class _VerifiedUser(_MemberUser):
    pk = 4

    def is_verified(self):
        return True


class _SuperUser(_StaffUser):
    is_superuser = True
    pk = 5


def _template(name, body):
    return f'<div dj-root dj-view="{_MOD}.{name}" dj-id="0">{body}</div>'


class _Base(LiveView):
    abstract = True

    def mount(self, request, **kwargs):
        self.x = 1

    @event_handler
    def touch(self, **kwargs):
        RAN.append(type(self).__name__)


def _view(name, body=None, decorators=(), **attrs):
    """Build ``name`` in this module (so the WS mount can import it)."""
    ns = {"template": _template(name, f"{name.upper()}-MARKER"), "__module__": _MOD}
    ns.update(attrs)
    if body:
        ns.update(body)
    cls = type(name, (_Base,), ns)
    cls.__qualname__ = name
    for deco in reversed(decorators):
        cls = deco(cls)
    setattr(sys.modules[__name__], name, cls)
    return cls


def _module(name, filename, source):
    """A module that looks installed from ``filename``."""
    module = types.ModuleType(name)
    module.__file__ = filename
    exec(compile(source, filename, "exec"), module.__dict__)  # noqa: S102
    sys.modules[name] = module
    return module


# --------------------------------------------------------------------------- #
# R1: a user check with any denial is a gate
# --------------------------------------------------------------------------- #
def _dispatch_redirect_non_staff(self, request, *args, **kwargs):
    if not request.user.is_staff:
        return HttpResponseRedirect("/")
    return LiveView.dispatch(self, request, *args, **kwargs)


def _get_redirect_anonymous(self, request, *args, **kwargs):
    if not request.user.is_authenticated:
        return redirect("/login/")
    return LiveView.get(self, request, *args, **kwargs)


def _dispatch_404_non_superuser(self, request, *args, **kwargs):
    if not request.user.is_superuser:
        raise Http404
    return LiveView.dispatch(self, request, *args, **kwargs)


def _dispatch_redirect_non_staff_shortcut(self, request, *args, **kwargs):
    if not request.user.is_staff:
        return redirect("/")
    return LiveView.dispatch(self, request, *args, **kwargs)


def _dispatch_raise_value_error(self, request, *args, **kwargs):
    if request.user.is_anonymous:
        raise ValueError("no")
    return LiveView.dispatch(self, request, *args, **kwargs)


def _setup_gate(self, request, *args, **kwargs):
    if not request.user.is_authenticated:
        raise PermissionDenied
    LiveView.setup(self, request, *args, **kwargs)


RedirectStaffDispatchView = _view(
    "RedirectStaffDispatchView", {"dispatch": _dispatch_redirect_non_staff}
)
RedirectAnonGetView = _view("RedirectAnonGetView", {"get": _get_redirect_anonymous})
NotFoundSuperDispatchView = _view(
    "NotFoundSuperDispatchView", {"dispatch": _dispatch_404_non_superuser}
)
LoginDeclaredRedirectStaffView = _view(
    "LoginDeclaredRedirectStaffView",
    {"dispatch": _dispatch_redirect_non_staff_shortcut},
    login_required=True,
)
LoginDeclaredRedirectAnonView = _view(
    "LoginDeclaredRedirectAnonView", {"get": _get_redirect_anonymous}, login_required=True
)
StaffCheckedRedirectView = _view(
    "StaffCheckedRedirectView",
    {
        "dispatch": _dispatch_redirect_non_staff,
        "check_permissions": lambda self, request: request.user.is_staff,
    },
)
RaiseAfterCheckView = _view("RaiseAfterCheckView", {"dispatch": _dispatch_raise_value_error})
SetupGateView = _view("SetupGateView", {"setup": _setup_gate})


class BodyGateView(LiveView):
    """Visible to both the source scan and the inspect-based S012."""

    template = _template("BodyGateView", "BODYGATEVIEW-MARKER")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


# --------------------------------------------------------------------------- #
# R2: URLconf wrappers
# --------------------------------------------------------------------------- #
UrlLoginView = _view("UrlLoginView")
UrlStaffView = _view("UrlStaffView")
UrlTwiceView = _view("UrlTwiceView")
UrlProjectGateView = _view("UrlProjectGateView")
UrlUnknownView = _view("UrlUnknownView")


class UrlAsViewOverrideView(_Base):
    template = _template("UrlAsViewOverrideView", "URLASVIEWOVERRIDEVIEW-MARKER")

    @classmethod
    def as_view(cls, **initkwargs):
        return login_required(super().as_view(**initkwargs))


def _project_staff_wrapper(view_func):
    """A project URLconf wrapper whose code checks the user."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def _timing_wrapper(view_func):
    """A project URLconf wrapper with no access control."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        return view_func(request, *args, **kwargs)

    return wrapper


_urls = types.ModuleType("_live_auth_wrapper_urls")
_urls.urlpatterns = [
    path("login/", login_required(UrlLoginView.as_view())),
    path("staff/", user_passes_test(lambda u: u.is_staff)(UrlStaffView.as_view())),
    path("public-twice/", UrlTwiceView.as_view()),
    path("staff-twice/", user_passes_test(lambda u: u.is_staff)(UrlTwiceView.as_view())),
    path("project/", _project_staff_wrapper(UrlProjectGateView.as_view())),
    path("unknown/", _timing_wrapper(UrlUnknownView.as_view())),
    path("override/", UrlAsViewOverrideView.as_view()),
    path("open/", RedirectAnonGetView.as_view()),
]
sys.modules[_urls.__name__] = _urls

# --------------------------------------------------------------------------- #
# R3: a third-party decorator built on user_passes_test
# --------------------------------------------------------------------------- #
_otp = _module(
    "fake_otp.decorators",
    "/opt/site-packages/fake_otp/decorators.py",
    "from django.contrib.auth.decorators import user_passes_test\n"
    "def otp_required(view=None, login_url=None):\n"
    "    test = user_passes_test(lambda u: u.is_verified(), login_url=login_url)\n"
    "    return test(view) if view else test\n",
)
OtpView = _view("OtpView", decorators=[method_decorator(_otp.otp_required, name="dispatch")])

# --------------------------------------------------------------------------- #
# Item 6: a decorator on post() only; item 7: an async test
# --------------------------------------------------------------------------- #
PostOnlyView = _view(
    "PostOnlyView",
    decorators=[method_decorator(user_passes_test(lambda u: u.is_superuser), name="post")],
)


async def _async_is_staff(user):
    await asyncio.sleep(0)
    return bool(user.is_staff)


AsyncTestView = _view(
    "AsyncTestView", decorators=[method_decorator(user_passes_test(_async_is_staff), name="get")]
)

# --------------------------------------------------------------------------- #
# Item 9: djust-tenants' mixin, a gated view from an installed package
# --------------------------------------------------------------------------- #
_tenants = _module(
    "djust_tenants.mixin",
    "/opt/site-packages/djust_tenants/mixin.py",
    "class TenantMixin:\n"
    "    def dispatch(self, request, *args, **kwargs):\n"
    "        self._ensure_tenant(request)\n"
    "        return super().dispatch(request, *args, **kwargs)\n"
    "    def get(self, request, *args, **kwargs):\n"
    "        self._ensure_tenant(request)\n"
    "        return super().get(request, *args, **kwargs)\n"
    "    def post(self, request, *args, **kwargs):\n"
    "        self._ensure_tenant(request)\n"
    "        return super().post(request, *args, **kwargs)\n"
    "    def _ensure_tenant(self, request):\n"
    "        self._tenant = getattr(request, 'tenant', None)\n",
)
TenantView = type("TenantView", (_tenants.TenantMixin, _Base), {"__module__": _MOD})
TenantView.__qualname__ = "TenantView"
setattr(sys.modules[__name__], "TenantView", TenantView)

_pkg = _module(
    "fake_cms.views",
    "/opt/site-packages/fake_cms/views.py",
    "from django.contrib.auth.mixins import AccessMixin\n"
    "from djust import LiveView\n"
    "class EditorMixin(AccessMixin):\n"
    "    def dispatch(self, request, *args, **kwargs):\n"
    "        if not request.user.is_staff:\n"
    "            return self.handle_no_permission()\n"
    "        return super().dispatch(request, *args, **kwargs)\n"
    "class EditorView(EditorMixin, LiveView):\n"
    "    template = '<div>editor</div>'\n",
)


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #
@pytest.fixture
def _live():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD], ROOT_URLCONF=_urls.__name__):
        clear_url_caches()
        yield
    clear_url_caches()


@pytest.fixture(autouse=True)
def _clear_ran():
    RAN.clear()
    yield
    RAN.clear()


def _with_user(app, user_cls):
    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = user_cls()
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


async def _ws(user_cls, view_name, event=None, url="/x/"):
    """Mount ``view_name`` over the real consumer, optionally send one event."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(_with_user(LiveViewConsumer.as_asgi(), user_cls), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)  # connect ack
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    await communicator.send_json_to(
        {"type": "mount", "view": f"{_MOD}.{view_name}", "params": {}, "url": url}
    )
    outputs = []
    try:
        while True:
            out = await communicator.receive_output(timeout=2)
            outputs.append(out)
            if out.get("type") == "websocket.close":
                break
            if '"type": "mount"' in (out.get("text") or ""):
                if event is None:
                    break
                await communicator.send_json_to(
                    {"type": "event", "event": event, "params": {}, "ref": 1}
                )
                event = None
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    try:
        await communicator.disconnect()
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    return outputs


def _frames(outputs):
    return [json.loads(o["text"]) for o in outputs if o.get("text")]


def _close_code(outputs):
    closes = [o for o in outputs if o.get("type") == "websocket.close"]
    return closes[-1].get("code") if closes else None


def _mounted(outputs):
    return any(f.get("type") == "mount" for f in _frames(outputs))


async def _assert_refused(view_name, user_cls, url="/x/"):
    outputs = await _ws(user_cls, view_name, url=url)
    assert not _mounted(outputs), f"{view_name} mounted for {user_cls.__name__}: {outputs!r}"
    assert "MARKER" not in repr(outputs)
    assert _close_code(outputs) == 4403, outputs
    if user_cls is _AnonUser:
        assert any(f.get("type") == "navigate" for f in _frames(outputs)), outputs
    else:
        assert any(f.get("type") == "error" for f in _frames(outputs)), outputs


async def _assert_mounts(view_name, user_cls, url="/x/"):
    outputs = await _ws(user_cls, view_name, url=url)
    assert _mounted(outputs), f"{view_name} did not mount for {user_cls.__name__}: {outputs!r}"
    assert f"{view_name.upper()}-MARKER" in repr(outputs)


async def _sse_mount(user, view_name, event=None):
    from djust.sse import SSESession

    request = RequestFactory().get("/x/")
    request.user = user
    request.session = MagicMock()
    session = SSESession(str(uuid.uuid4()))
    session._owner_user_pk = user.pk
    session._request = request
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": f"{_MOD}.{view_name}", "url": "/x/", "params": {}}
    )
    if event is not None and session.runtime.view_instance is not None:
        await session.runtime.dispatch_event({"event": event, "params": {}})
    messages = []
    while not session.queue.empty():
        messages.append(session.queue.get_nowait())
    return session.runtime.view_instance is not None, messages


def _post(view_cls, user, event="touch"):
    """The HTTP POST event fallback itself (``RequestMixin.post``), past
    whatever the view's own ``post()`` decorators did."""
    request = RequestFactory().post(
        "/x/", data=json.dumps({"event": event, "params": {}}), content_type="application/json"
    )
    request.user = user
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return LiveView.post(view_cls(), request)


ws = pytest.mark.usefixtures("_live")

R1_SHAPES = [
    "RedirectStaffDispatchView",
    "RedirectAnonGetView",
    "NotFoundSuperDispatchView",
    "LoginDeclaredRedirectStaffView",
]


# --------------------------------------------------------------------------- #
# R1
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestDenialsAreGates:
    @pytest.mark.parametrize("view_name", R1_SHAPES + ["RaiseAfterCheckView", "SetupGateView"])
    async def test_anonymous_ws_mount_is_refused(self, view_name):
        await _assert_refused(view_name, _AnonUser)

    @pytest.mark.parametrize("view_name", R1_SHAPES)
    async def test_anonymous_sse_mount_is_refused(self, view_name):
        mounted, messages = await _sse_mount(_AnonUser(), view_name)
        assert mounted is False
        assert "MARKER" not in repr(messages)

    async def test_login_rule_does_not_cover_a_staff_redirect(self):
        await _assert_refused("LoginDeclaredRedirectStaffView", _MemberUser)
        await _assert_refused("LoginDeclaredRedirectStaffView", _StaffUser)

    async def test_login_rule_covers_a_login_redirect(self):
        await _assert_refused("LoginDeclaredRedirectAnonView", _AnonUser)
        await _assert_mounts("LoginDeclaredRedirectAnonView", _MemberUser)

    async def test_declared_rule_admits_the_permitted_user(self):
        await _assert_mounts("StaffCheckedRedirectView", _StaffUser)
        outputs = await _ws(_MemberUser, "StaffCheckedRedirectView")
        assert not _mounted(outputs) and "MARKER" not in repr(outputs)
        assert _close_code(outputs) == 4403, outputs


@pytest.mark.django_db
class TestDenialsOnThePostFallback:
    @pytest.mark.parametrize("view_name", R1_SHAPES)
    def test_anonymous_post_is_refused(self, view_name):
        response = _post(getattr(sys.modules[__name__], view_name), _AnonUser())
        assert response.status_code == 403
        assert RAN == []

    def test_member_post_to_login_declared_staff_redirect_is_refused(self):
        response = _post(LoginDeclaredRedirectStaffView, _MemberUser())
        assert response.status_code == 403
        assert RAN == []


class TestDenialPredicate:
    @pytest.mark.parametrize(
        "view_cls",
        [
            RedirectStaffDispatchView,
            RedirectAnonGetView,
            NotFoundSuperDispatchView,
            LoginDeclaredRedirectStaffView,
            RaiseAfterCheckView,
            SetupGateView,
        ],
    )
    def test_refused_until_declared(self, view_cls):
        assert live_auth_refusals(view_cls)

    def test_covered_shapes(self):
        assert live_auth_refusals(LoginDeclaredRedirectAnonView) == ()
        assert live_auth_refusals(StaffCheckedRedirectView) == ()


# --------------------------------------------------------------------------- #
# R2
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestUrlconfWrappers:
    async def test_login_required_wrapper_is_replayed(self):
        await _assert_refused("UrlLoginView", _AnonUser)
        await _assert_mounts("UrlLoginView", _MemberUser)

    async def test_user_passes_test_wrapper_is_replayed(self):
        await _assert_refused("UrlStaffView", _AnonUser)
        await _assert_refused("UrlStaffView", _MemberUser)
        await _assert_mounts("UrlStaffView", _StaffUser)

    async def test_as_view_override_is_replayed(self):
        await _assert_refused("UrlAsViewOverrideView", _AnonUser)
        await _assert_mounts("UrlAsViewOverrideView", _MemberUser)

    async def test_the_client_url_does_not_choose_the_route(self):
        """The mount frame's URL names an unwrapped route; the class's own
        routes decide."""
        await _assert_refused("UrlLoginView", _AnonUser, url="/open/")
        await _assert_refused("UrlStaffView", _MemberUser, url="/open/")

    async def test_a_class_routed_twice_gets_every_routes_wrappers(self):
        await _assert_refused("UrlTwiceView", _MemberUser, url="/public-twice/")
        await _assert_mounts("UrlTwiceView", _StaffUser, url="/public-twice/")

    async def test_project_wrapper_that_checks_the_user_is_refused(self):
        await _assert_refused("UrlProjectGateView", _AnonUser)
        await _assert_refused("UrlProjectGateView", _StaffUser)

    async def test_unknown_wrapper(self):
        if EXPECT_UNKNOWN_REFUSED:
            await _assert_refused("UrlUnknownView", _AnonUser)
            await _assert_refused("UrlUnknownView", _MemberUser)
        else:
            await _assert_mounts("UrlUnknownView", _AnonUser)

    async def test_sse_mount_replays_the_wrapper(self):
        mounted, messages = await _sse_mount(_AnonUser(), "UrlLoginView")
        assert mounted is False
        assert "MARKER" not in repr(messages)
        mounted, _ = await _sse_mount(_MemberUser(), "UrlLoginView")
        assert mounted is True


@pytest.mark.django_db
@pytest.mark.usefixtures("_live")
class TestUrlconfWrappersOnThePostFallback:
    def test_anonymous_post_is_refused(self):
        assert _post(UrlLoginView, _AnonUser()).status_code == 403
        assert _post(UrlStaffView, _MemberUser()).status_code == 403
        assert RAN == []

    def test_the_url_map_follows_the_urlconf(self):
        request = RequestFactory().get("/x/")
        request.user = _AnonUser()
        assert check_view_auth(UrlLoginView(), request) is not None
        with override_settings(ROOT_URLCONF="djust.tests._no_such_urlconf_module"):
            clear_url_caches()
            assert check_view_auth(UrlLoginView(), request) is None  # not routed there


# --------------------------------------------------------------------------- #
# R3
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestDecoratorBuiltOnUserPassesTest:
    async def test_replayed(self):
        await _assert_refused("OtpView", _AnonUser)
        await _assert_refused("OtpView", _MemberUser)
        await _assert_mounts("OtpView", _VerifiedUser)

    async def test_not_refused_until_declared(self):
        assert live_auth_refusals(OtpView) == ()


# --------------------------------------------------------------------------- #
# Item 6: post()-only rules apply to events, not to the mount
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestPostOnlyDecorator:
    async def test_mount_is_not_gated(self):
        await _assert_mounts("PostOnlyView", _AnonUser)
        await _assert_mounts("PostOnlyView", _MemberUser)

    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser, _StaffUser])
    async def test_event_is_refused(self, user_cls):
        outputs = await _ws(user_cls, "PostOnlyView", event="touch")
        assert _mounted(outputs)
        assert RAN == [], "a post()-gated handler ran for %s" % user_cls.__name__
        assert any(
            f.get("type") == "error" and "Permission denied" in json.dumps(f)
            for f in _frames(outputs)
        ), outputs

    async def test_event_runs_for_the_permitted_user(self):
        await _ws(_SuperUser, "PostOnlyView", event="touch")
        assert RAN == ["PostOnlyView"]

    async def test_sse_event_is_refused_and_runs_for_the_permitted_user(self):
        mounted, messages = await _sse_mount(_MemberUser(), "PostOnlyView", event="touch")
        assert mounted is True
        assert RAN == []
        assert "Permission denied" in repr(messages)
        mounted, _ = await _sse_mount(_SuperUser(), "PostOnlyView", event="touch")
        assert RAN == ["PostOnlyView"]


@pytest.mark.django_db
class TestPostOnlyDecoratorOnThePostFallback:
    def test_refused(self):
        assert _post(PostOnlyView, _AnonUser()).status_code == 403
        assert _post(PostOnlyView, _MemberUser()).status_code == 403
        assert RAN == []

    def test_mount_check_skips_it_and_event_check_applies_it(self):
        request = RequestFactory().get("/x/")
        request.user = _MemberUser()
        assert check_view_auth(PostOnlyView(), request) is None
        with pytest.raises(PermissionDenied):
            check_view_auth(PostOnlyView(), request, event=True)
        with pytest.raises(PermissionDenied):
            core.check_event_auth(PostOnlyView(), request)
        request.user = _SuperUser()
        assert core.check_event_auth(PostOnlyView(), request) is None


# --------------------------------------------------------------------------- #
# Item 7: async test functions
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestAsyncTestFunc:
    async def test_ws_mount_awaits_the_test(self):
        await _assert_mounts("AsyncTestView", _StaffUser)
        await _assert_refused("AsyncTestView", _MemberUser)
        await _assert_refused("AsyncTestView", _AnonUser)

    async def test_called_on_the_event_loop_thread(self):
        """The loop's own thread cannot block on the test: refused (with an
        error naming sync_to_async) instead of risking a hang."""
        request = RequestFactory().get("/x/")
        request.user = _StaffUser()
        with pytest.raises(PermissionDenied):
            check_view_auth(AsyncTestView(), request)
        request.user = _MemberUser()
        with pytest.raises(PermissionDenied):
            check_view_auth(AsyncTestView(), request)


def test_async_test_from_sync_code():
    request = RequestFactory().get("/x/")
    request.user = _StaffUser()
    assert check_view_auth(AsyncTestView(), request) is None


# --------------------------------------------------------------------------- #
# The system checks (R4, R5, item 9)
# --------------------------------------------------------------------------- #
def _run_checks(urlpatterns=()):
    from djust.checks.components import check_liveviews
    from djust.checks.security import check_security

    urlconf = types.ModuleType("_live_auth_check_urls_%s" % uuid.uuid4().hex)
    urlconf.urlpatterns = list(urlpatterns)
    sys.modules[urlconf.__name__] = urlconf
    try:
        with override_settings(ROOT_URLCONF=urlconf.__name__, LIVEVIEW_ALLOWED_MODULES=[_MOD]):
            clear_url_caches()
            with patch(
                "djust.checks._get_project_app_dirs", return_value=[os.path.dirname(__file__)]
            ):
                return check_security(None) + check_liveviews(None)
    finally:
        clear_url_caches()
        sys.modules.pop(urlconf.__name__, None)


def _for(messages, check_id, label):
    return [m for m in messages if m.id == check_id and m.msg.startswith(label + ":")]


@pytest.mark.django_db
def test_checks_report_refused_views_as_errors_and_the_rest_as_warnings():
    messages = _run_checks(
        [
            path("editor/", _pkg.EditorView.as_view()),
            path("login/", login_required(UrlLoginView.as_view())),
            path("unknown/", _timing_wrapper(UrlUnknownView.as_view())),
        ]
    )

    def label(cls):
        return "%s.%s" % (cls.__module__, cls.__qualname__)

    for refused in (RedirectStaffDispatchView, NotFoundSuperDispatchView, SetupGateView):
        errors = _for(messages, "djust.S012", label(refused))
        assert len(errors) == 1 and errors[0].level >= 40, (refused, errors)
    # Replayed: no Error.
    for replayed in (OtpView, UrlLoginView, AsyncTestView, PostOnlyView):
        assert not _for(messages, "djust.S012", label(replayed)), replayed
    # A setup() gate is named by V013 too.
    assert any("setup()" in m.msg for m in _for(messages, "djust.V013", label(SetupGateView)))
    # djust-tenants' mixin: no V013.
    assert not _for(messages, "djust.V013", label(TenantView))
    # A refused view an installed package defines: V013 with the refusal, no S012.
    assert not _for(messages, "djust.S012", "fake_cms.views.EditorView")
    editor = _for(messages, "djust.V013", "fake_cms.views.EditorView")
    assert editor and "djust refuses live requests" in editor[0].msg
    # An unknown URLconf wrapper: an Error where it is refused, else a warning.
    unknown_errors = _for(messages, "djust.S012", label(UrlUnknownView))
    unknown_warnings = _for(messages, "djust.V013", label(UrlUnknownView))
    assert bool(unknown_errors) is EXPECT_UNKNOWN_REFUSED
    assert unknown_warnings and "URLconf" in unknown_warnings[0].msg


@pytest.mark.django_db
def test_no_duplicate_s012_for_a_view_both_scans_see():
    """The source scan skips classes the inspect-based S012 analysed."""
    messages = _run_checks()
    named = [m for m in messages if m.id == "djust.S012" and "BodyGateView" in m.msg]
    assert len(named) == 1, named
