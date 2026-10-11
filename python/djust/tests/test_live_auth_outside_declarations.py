"""Access control that only Django's HTTP dispatch runs, on live transports.

Django's ``View.dispatch`` runs a view's own ``dispatch()``, its method
decorators and ``get()``/``post()``. The WebSocket and SSE mount, the HTTP POST
event fallback, the API and ``{% live_render %}`` children never do: they
authorize through ``check_view_auth``, which enforces djust's declarations
(``login_required`` / ``permission_required`` / ``check_permissions()``) and
replays Django's three built-in auth mixins.

``check_view_auth`` step 5 handles what lives in that HTTP-only code:

* Django's ``login_required``, ``staff_member_required``,
  ``user_passes_test(...)`` and ``permission_required(...)`` applied with
  ``@method_decorator`` are replayed: anonymous users get the login redirect,
  authenticated users who fail the test get ``PermissionDenied``, permitted
  users mount. ``login_not_required`` is not access control.
* A gate (a custom ``AccessMixin`` subclass's ``dispatch()``, an override or
  decorator that refuses the user) refuses the request unless a declared rule
  covers it. ``login_required = True`` covers only a login-only gate.
* An override that only reads the user is reported by ``djust.V013`` (a
  warning), not refused.
* An unknown method decorator is not refused on this patch line (see
  ``EXPECT_UNKNOWN_REFUSED``); ``djust.V013`` warns about it.
  ``check_permissions()`` returning True covers one on a public view.

These tests pin every rule on the WebSocket mount, and the two reported shapes
plus the replayed decorators on the SSE mount and the HTTP POST event fallback;
the HTTP GET page, which Django's dispatch already gated, is unchanged; and the
system check reports the views the runtime refuses and the shapes it cannot
run (including URLconf wrappers).

Gate-off: making step 5 a no-op fails the refusal tests; dropping the replay
fails the anonymous refusals of the decorated views; restoring the ``djust_``
prefix exemption or dropping the djust-auth mapping fails their tests;
classing ``login_not_required`` as access control fails the public-page test.
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest

pytest.importorskip("channels")

from django.contrib.admin.views.decorators import staff_member_required  # noqa: E402
from django.contrib.auth.decorators import (  # noqa: E402
    login_required,
    permission_required,
    user_passes_test,
)
from django.contrib.auth.mixins import (  # noqa: E402
    AccessMixin,
    LoginRequiredMixin,
    UserPassesTestMixin,
)
from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402
from django.core.exceptions import PermissionDenied  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402
from django.utils.decorators import method_decorator  # noqa: E402
from django.views.decorators.cache import never_cache  # noqa: E402
from django.views.decorators.csrf import csrf_exempt  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.auth import core  # noqa: E402
from djust.auth.core import (  # noqa: E402
    check_view_auth,
    declares_live_auth,
    http_only_access_control,
    is_framework_module,
    live_auth_refusals,
    live_auth_reports,
)
from djust.auth.mixins import LoginRequiredLiveViewMixin  # noqa: E402
from djust.decorators import event_handler  # noqa: E402

try:
    from django.contrib.auth.decorators import login_not_required  # Django 5.1+
except ImportError:  # pragma: no cover - older Django
    login_not_required = None

#: This line's policy for a method decorator djust cannot classify.
EXPECT_UNKNOWN_REFUSED = False

RAN: list = []
_MOD = __name__


class _AnonUser:
    is_authenticated = False
    is_active = False
    is_anonymous = True
    is_staff = False
    pk = None

    def has_perms(self, perms):
        return False


class _MemberUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    pk = 1

    def has_perms(self, perms):
        return False


class _StaffUser(_MemberUser):
    is_staff = True
    pk = 2


class _PermittedUser(_MemberUser):
    """Holds every permission, is not staff."""

    pk = 3

    def has_perms(self, perms):
        return True


def _template(name, body):
    return f'<div dj-root dj-view="{_MOD}.{name}" dj-id="0">{body}</div>'


class _Base(LiveView):
    abstract = True

    def mount(self, request, **kwargs):
        self.x = 1


def _view(name, bases, body=None, decorators=(), **attrs):
    """Build ``name`` in this module (so the WS mount can import it)."""
    ns = {"template": _template(name, f"{name.upper()}-MARKER"), "__module__": _MOD}
    ns.update(attrs)
    if body:
        ns.update(body)
    cls = type(name, tuple(bases) + (_Base,), ns)
    cls.__qualname__ = name
    for deco in reversed(decorators):
        cls = deco(cls)
    setattr(sys.modules[__name__], name, cls)
    return cls


# --------------------------------------------------------------------------- #
# The two reported shapes
# --------------------------------------------------------------------------- #
class StaffRequiredMixin(AccessMixin):
    """A project access mixin that enforces in dispatch()."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class StaffOnlyView(StaffRequiredMixin, LiveView):
    template = _template("StaffOnlyView", "STAFF-ONLY-MARKER {{ state }}")

    def mount(self, request, **kwargs):
        self.state = "intact"

    @event_handler
    def archive_records(self, **kwargs):
        RAN.append("archive_records")
        self.state = "ARCHIVED"

    def get_context_data(self, **kwargs):
        return {"state": self.state}


@method_decorator(login_required, name="get")
class GatedGetView(LiveView):
    template = _template("GatedGetView", "GATED-GET-MARKER")

    def mount(self, request, **kwargs):
        self.x = 1

    @event_handler
    def flip(self, **kwargs):
        RAN.append("flip")


# I2: login_required = True does not cover a staff gate.
LoginDeclaredStaffOnlyView = _view(
    "LoginDeclaredStaffOnlyView", [StaffRequiredMixin], login_required=True
)
DeclaredStaffOnlyView = _view(
    "DeclaredStaffOnlyView",
    [StaffRequiredMixin],
    {"check_permissions": lambda self, request: request.user.is_staff},
    login_required=True,
)

# --------------------------------------------------------------------------- #
# Django auth decorators, replayed
# --------------------------------------------------------------------------- #
GatedDispatchView = _view(
    "GatedDispatchView", [], decorators=[method_decorator(login_required, name="dispatch")]
)
CustomLoginUrlView = _view(
    "CustomLoginUrlView",
    [],
    decorators=[method_decorator(login_required(login_url="/custom-login/"), name="dispatch")],
)
PartialLoginView = _view(
    "PartialLoginView",
    [],
    decorators=[
        method_decorator(functools.partial(login_required, login_url="/partial-login/"), name="get")
    ],
)
StaffTestView = _view(
    "StaffTestView",
    [],
    decorators=[method_decorator(user_passes_test(lambda u: u.is_staff), name="get")],
)
PermissionView = _view(
    "PermissionView",
    [],
    decorators=[method_decorator(permission_required("app.view_thing"), name="dispatch")],
)
PermissionRaiseView = _view(
    "PermissionRaiseView",
    [],
    decorators=[
        method_decorator(
            permission_required("app.view_thing", raise_exception=True), name="dispatch"
        )
    ],
)
StaffMemberView = _view(
    "StaffMemberView", [], decorators=[method_decorator(staff_member_required, name="dispatch")]
)
# I2 again: a declared login rule plus a replayed staff test still refuses members.
LoginDeclaredStaffTestView = _view(
    "LoginDeclaredStaffTestView",
    [],
    decorators=[method_decorator(user_passes_test(lambda u: u.is_staff), name="get")],
    login_required=True,
)
if login_not_required is not None:
    PublicNotRequiredView = _view(
        "PublicNotRequiredView",
        [],
        decorators=[method_decorator(login_not_required, name="dispatch")],
    )


# --------------------------------------------------------------------------- #
# Django's replayed mixins and their subclasses
# --------------------------------------------------------------------------- #
class TenantSetupMixin(LoginRequiredMixin):
    """A LoginRequiredMixin subclass whose dispatch() only does setup."""

    def dispatch(self, request, *args, **kwargs):
        self.tenant = getattr(request, "tenant", None)
        return super().dispatch(request, *args, **kwargs)


class StaffGateLoginMixin(LoginRequiredMixin):
    """A LoginRequiredMixin subclass whose dispatch() adds a staff gate."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class DjangoTestFuncMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


DjangoLoginMixinView = _view("DjangoLoginMixinView", [LoginRequiredMixin])
DjangoTestFuncView = _view("DjangoTestFuncView", [DjangoTestFuncMixin])
TenantSetupView = _view("TenantSetupView", [TenantSetupMixin])
StaffGateLoginMixinView = _view("StaffGateLoginMixinView", [StaffGateLoginMixin])


# --------------------------------------------------------------------------- #
# Overrides
# --------------------------------------------------------------------------- #
def _refusing_get(self, request, *args, **kwargs):
    if not request.user.is_authenticated:
        raise PermissionDenied
    return LiveView.get(self, request, *args, **kwargs)


def _reading_get(self, request, *args, **kwargs):
    if request.user.is_authenticated:
        request.session["seen"] = 1
    return LiveView.get(self, request, *args, **kwargs)


def _plain_get(self, request, *args, **kwargs):
    self.seen_get = True
    return LiveView.get(self, request, *args, **kwargs)


AuthGetOverrideView = _view("AuthGetOverrideView", [], {"get": _refusing_get})
LoginDeclaredAuthGetView = _view(
    "LoginDeclaredAuthGetView", [], {"get": _refusing_get}, login_required=True
)
ReadingGetView = _view("ReadingGetView", [], {"get": _reading_get})
PlainGetOverrideView = _view("PlainGetOverrideView", [], {"get": _plain_get})
NonAccessDecoratorView = _view(
    "NonAccessDecoratorView",
    [],
    decorators=[
        method_decorator(csrf_exempt, name="dispatch"),
        method_decorator(never_cache, name="get"),
    ],
)
DjustDispatchMixinView = _view("DjustDispatchMixinView", [LoginRequiredLiveViewMixin])


# --------------------------------------------------------------------------- #
# Decorators djust does not know
# --------------------------------------------------------------------------- #
def log_request(view_func):
    """A project decorator with no access control."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        return view_func(request, *args, **kwargs)

    return wrapper


def staff_required(view_func):
    """A project decorator that refuses non-staff users."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def _third_party_module(name, filename, source):
    """A module that looks installed from site-packages."""
    module = types.ModuleType(name)
    module.__file__ = filename
    exec(compile(source, filename, "exec"), module.__dict__)  # noqa: S102
    sys.modules[name] = module
    return module


_csp = _third_party_module(
    "fake_csp.decorators",
    "/opt/site-packages/fake_csp/decorators.py",
    "import functools\n"
    "def csp_update(**policy):\n"
    "    def decorator(view_func):\n"
    "        @functools.wraps(view_func)\n"
    "        def wrapper(request, *args, **kwargs):\n"
    "            response = view_func(request, *args, **kwargs)\n"
    "            response._csp_update = policy\n"
    "            return response\n"
    "        return wrapper\n"
    "    return decorator\n",
)

ProjectDecoratorView = _view(
    "ProjectDecoratorView", [], decorators=[method_decorator(log_request, name="dispatch")]
)
ProjectDecoratorPublicView = _view(
    "ProjectDecoratorPublicView",
    [],
    {"check_permissions": lambda self, request: True},
    decorators=[method_decorator(log_request, name="dispatch")],
)
ThirdPartyDecoratorView = _view(
    "ThirdPartyDecoratorView",
    [],
    decorators=[method_decorator(_csp.csp_update(SCRIPT_SRC="'self'"), name="dispatch")],
)
ProjectStaffDecoratorView = _view(
    "ProjectStaffDecoratorView",
    [],
    decorators=[method_decorator(staff_required, name="get")],
    login_required=True,
)

# --------------------------------------------------------------------------- #
# Modules named djust_* that are not djust (I1)
# --------------------------------------------------------------------------- #
_djust_auth = _third_party_module(
    "djust_auth.mixins",
    "/opt/site-packages/djust_auth/mixins.py",
    # The shape PyPI djust-auth 0.3.0 ships.
    "from urllib.parse import urlencode\n"
    "from django.conf import settings\n"
    "from django.shortcuts import redirect\n"
    "class LoginRequiredLiveViewMixin:\n"
    "    login_url = None\n"
    "    def dispatch(self, request, *args, **kwargs):\n"
    "        if not request.user.is_authenticated:\n"
    "            login_url = self.login_url or getattr(settings, 'LOGIN_URL', '/accounts/login/')\n"
    "            return redirect(f\"{login_url}?{urlencode({'next': request.get_full_path()})}\")\n"
    "        return super().dispatch(request, *args, **kwargs)\n",
)
_djust_shop = _third_party_module(
    "djust_shop.views",
    __file__.replace(".py", "_djust_shop.py"),
    "from django.contrib.auth.mixins import AccessMixin\n"
    "class StaffMixin(AccessMixin):\n"
    "    def dispatch(self, request, *args, **kwargs):\n"
    "        if not request.user.is_staff:\n"
    "            return self.handle_no_permission()\n"
    "        return super().dispatch(request, *args, **kwargs)\n"
    "from djust import LiveView\n"
    "class ShopView(StaffMixin, LiveView):\n"
    "    template = '<div>shop</div>'\n",
)
DjustAuthPackageView = _view("DjustAuthPackageView", [_djust_auth.LoginRequiredLiveViewMixin])
DjustNamedAppView = _view("DjustNamedAppView", [_djust_shop.StaffMixin])


@pytest.fixture
def _allow_this_module():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
        yield


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


async def _ws(user_cls, view_name, event=None):
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
        {"type": "mount", "view": f"{_MOD}.{view_name}", "params": {}, "url": "/x/"}
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


def _navigated_to(outputs):
    return [f.get("to") for f in _frames(outputs) if f.get("type") == "navigate"]


async def _assert_login_redirect(view_name, user_cls=_AnonUser, to=None):
    outputs = await _ws(user_cls, view_name)
    assert not _mounted(outputs), f"{view_name} mounted without login: {outputs!r}"
    assert "MARKER" not in repr(outputs)
    targets = _navigated_to(outputs)
    assert targets, outputs
    if to is not None:
        assert any(t and t.startswith(to) for t in targets), targets
    assert _close_code(outputs) == 4403, outputs


async def _assert_forbidden(view_name, user_cls):
    outputs = await _ws(user_cls, view_name)
    assert not _mounted(outputs), f"{view_name} mounted for {user_cls.__name__}: {outputs!r}"
    assert "MARKER" not in repr(outputs)
    assert any(f.get("type") == "error" for f in _frames(outputs)), outputs
    assert _close_code(outputs) == 4403, outputs


async def _assert_mounts(view_name, user_cls):
    outputs = await _ws(user_cls, view_name)
    assert _mounted(outputs), f"{view_name} did not mount for {user_cls.__name__}: {outputs!r}"


ws = pytest.mark.usefixtures("_allow_this_module")


# --------------------------------------------------------------------------- #
# WebSocket mount: the reported shapes
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestReportedShapes:
    @pytest.mark.parametrize("view_name", ["StaffOnlyView", "GatedGetView", "AuthGetOverrideView"])
    async def test_anonymous_mount_is_refused(self, view_name):
        await _assert_login_redirect(view_name)

    async def test_anonymous_event_after_refused_mount_does_not_run(self):
        outputs = await _ws(_AnonUser, "StaffOnlyView", event="archive_records")
        assert RAN == [], "a handler ran on a view whose mount was refused"
        assert "ARCHIVED" not in repr(outputs)

    @pytest.mark.parametrize("user_cls", [_MemberUser, _StaffUser])
    async def test_custom_access_mixin_refuses_until_declared(self, user_cls):
        await _assert_forbidden("StaffOnlyView", user_cls)

    @pytest.mark.parametrize("user_cls", [_MemberUser, _StaffUser])
    async def test_login_required_alone_does_not_cover_a_custom_access_mixin(self, user_cls):
        await _assert_forbidden("LoginDeclaredStaffOnlyView", user_cls)

    async def test_login_required_alone_still_sends_anonymous_users_to_login(self):
        await _assert_login_redirect("LoginDeclaredStaffOnlyView")

    async def test_declared_rule_admits_the_permitted_user(self):
        outputs = await _ws(_StaffUser, "DeclaredStaffOnlyView")
        assert _mounted(outputs), outputs
        assert "DECLAREDSTAFFONLYVIEW-MARKER" in repr(outputs)

    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser])
    async def test_declared_rule_refuses_the_others(self, user_cls):
        outputs = await _ws(user_cls, "DeclaredStaffOnlyView")
        assert not _mounted(outputs), outputs
        assert "MARKER" not in repr(outputs)


# --------------------------------------------------------------------------- #
# WebSocket mount: Django auth decorators replayed
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestReplayedDecorators:
    @pytest.mark.parametrize(
        "view_name",
        [
            "GatedGetView",
            "GatedDispatchView",
            "PartialLoginView",
            "StaffTestView",
            "PermissionView",
        ],
    )
    async def test_anonymous_gets_the_login_redirect(self, view_name):
        await _assert_login_redirect(view_name)

    async def test_login_url_of_the_decorator_is_used(self):
        await _assert_login_redirect("CustomLoginUrlView", to="/custom-login/")
        await _assert_login_redirect("PartialLoginView", to="/partial-login/")

    @pytest.mark.parametrize(
        "view_name", ["GatedGetView", "GatedDispatchView", "CustomLoginUrlView", "PartialLoginView"]
    )
    async def test_login_required_admits_a_logged_in_user(self, view_name):
        await _assert_mounts(view_name, _MemberUser)

    async def test_user_passes_test_runs_its_test(self):
        await _assert_forbidden("StaffTestView", _MemberUser)
        await _assert_mounts("StaffTestView", _StaffUser)

    async def test_permission_required_checks_the_permission(self):
        await _assert_forbidden("PermissionView", _MemberUser)
        await _assert_mounts("PermissionView", _PermittedUser)

    async def test_permission_required_raise_exception(self):
        await _assert_forbidden("PermissionRaiseView", _AnonUser)
        await _assert_forbidden("PermissionRaiseView", _MemberUser)
        await _assert_mounts("PermissionRaiseView", _PermittedUser)

    async def test_staff_member_required(self):
        await _assert_login_redirect("StaffMemberView")
        await _assert_forbidden("StaffMemberView", _MemberUser)
        await _assert_mounts("StaffMemberView", _StaffUser)

    async def test_replayed_test_applies_on_top_of_a_declared_login_rule(self):
        await _assert_forbidden("LoginDeclaredStaffTestView", _MemberUser)
        await _assert_mounts("LoginDeclaredStaffTestView", _StaffUser)

    @pytest.mark.skipif(login_not_required is None, reason="Django < 5.1")
    async def test_login_not_required_public_page_mounts(self):
        await _assert_mounts("PublicNotRequiredView", _AnonUser)
        await _assert_mounts("PublicNotRequiredView", _MemberUser)


# --------------------------------------------------------------------------- #
# WebSocket mount: mixins, overrides, unknown decorators, djust_* modules
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestOtherShapes:
    async def test_django_login_mixin_still_replayed(self):
        await _assert_login_redirect("DjangoLoginMixinView")
        await _assert_mounts("DjangoLoginMixinView", _MemberUser)

    async def test_django_test_func_still_replayed(self):
        await _assert_forbidden("DjangoTestFuncView", _MemberUser)
        await _assert_mounts("DjangoTestFuncView", _StaffUser)

    async def test_login_mixin_subclass_with_setup_dispatch_mounts_for_logged_in(self):
        await _assert_login_redirect("TenantSetupView")
        await _assert_mounts("TenantSetupView", _MemberUser)

    @pytest.mark.parametrize("user_cls", [_MemberUser, _StaffUser])
    async def test_login_mixin_subclass_with_a_gate_refuses_until_declared(self, user_cls):
        await _assert_forbidden("StaffGateLoginMixinView", user_cls)

    async def test_refusing_override_covered_by_declared_login(self):
        await _assert_forbidden("AuthGetOverrideView", _MemberUser)
        await _assert_login_redirect("LoginDeclaredAuthGetView")
        await _assert_mounts("LoginDeclaredAuthGetView", _MemberUser)

    @pytest.mark.parametrize(
        "view_name", ["NonAccessDecoratorView", "PlainGetOverrideView", "ReadingGetView"]
    )
    async def test_views_without_a_gate_still_mount(self, view_name):
        await _assert_mounts(view_name, _AnonUser)

    async def test_djust_login_dispatch_mixin_is_enforced(self):
        await _assert_login_redirect("DjustDispatchMixinView")
        await _assert_mounts("DjustDispatchMixinView", _MemberUser)

    async def test_djust_auth_package_login_mixin_is_enforced(self):
        await _assert_login_redirect("DjustAuthPackageView")
        await _assert_mounts("DjustAuthPackageView", _MemberUser)

    async def test_project_app_named_djust_is_inspected(self):
        await _assert_login_redirect("DjustNamedAppView")
        await _assert_forbidden("DjustNamedAppView", _StaffUser)

    async def test_project_decorator_that_checks_the_user_is_refused(self):
        await _assert_forbidden("ProjectStaffDecoratorView", _MemberUser)
        await _assert_forbidden("ProjectStaffDecoratorView", _StaffUser)
        await _assert_login_redirect("ProjectStaffDecoratorView")

    @pytest.mark.parametrize("view_name", ["ProjectDecoratorView", "ThirdPartyDecoratorView"])
    async def test_unknown_decorator(self, view_name):
        if EXPECT_UNKNOWN_REFUSED:
            await _assert_login_redirect(view_name)
            await _assert_forbidden(view_name, _MemberUser)
        else:
            await _assert_mounts(view_name, _AnonUser)
            await _assert_mounts(view_name, _MemberUser)

    async def test_check_permissions_keeps_an_unknown_decorator_on_a_public_view(self):
        await _assert_mounts("ProjectDecoratorPublicView", _AnonUser)


# --------------------------------------------------------------------------- #
# SSE mount
# --------------------------------------------------------------------------- #
async def _sse_mount(user, view_name):
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
    messages = []
    while not session.queue.empty():
        messages.append(session.queue.get_nowait())
    return session.runtime.view_instance is not None, messages


@pytest.mark.django_db
@pytest.mark.asyncio
@ws
class TestSSEMount:
    @pytest.mark.parametrize("view_name", ["StaffOnlyView", "GatedGetView", "StaffTestView"])
    async def test_anonymous_mount_is_refused(self, view_name):
        mounted, messages = await _sse_mount(_AnonUser(), view_name)
        assert mounted is False
        assert "MARKER" not in repr(messages)

    @pytest.mark.parametrize(
        "user,view_name",
        [
            (_StaffUser(), "DeclaredStaffOnlyView"),
            (_MemberUser(), "GatedGetView"),
            (_StaffUser(), "StaffTestView"),
            (_PermittedUser(), "PermissionView"),
        ],
    )
    async def test_permitted_user_mounts(self, user, view_name):
        mounted, _ = await _sse_mount(user, view_name)
        assert mounted is True

    async def test_failing_user_is_refused(self):
        mounted, _ = await _sse_mount(_MemberUser(), "StaffTestView")
        assert mounted is False


# --------------------------------------------------------------------------- #
# HTTP POST event fallback and HTTP GET
# --------------------------------------------------------------------------- #
def _request(method, user, body=None):
    factory = RequestFactory()
    if method == "post":
        request = factory.post("/x/", data=json.dumps(body or {}), content_type="application/json")
    else:
        request = factory.get("/x/")
    request.user = user
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
class TestHttpPaths:
    def test_anonymous_post_event_to_gated_get_view_is_refused(self):
        request = _request("post", _AnonUser(), {"event": "flip", "params": {}})
        response = GatedGetView.as_view()(request)
        assert response.status_code in (302, 403), response.status_code
        assert RAN == [], "the handler ran for a POST without login"

    def test_anonymous_post_event_to_staff_view_is_refused(self):
        request = _request("post", _AnonUser(), {"event": "archive_records", "params": {}})
        response = StaffOnlyView.as_view()(request)
        assert response.status_code in (302, 403)
        assert RAN == []

    def test_post_event_called_past_dispatch_is_refused(self):
        """``post()`` itself refuses, whatever dispatch() did or did not run."""
        request = _request("post", _AnonUser(), {"event": "flip", "params": {}})
        response = GatedGetView().post(request)
        assert response.status_code == 403
        assert RAN == []

    def test_post_event_for_a_logged_in_user_passes_the_replayed_decorator(self):
        """The replay admits the permitted user; whatever the POST then does,
        it is not the auth refusal."""
        request = _request("post", _MemberUser(), {"event": "flip", "params": {}})
        assert check_view_auth(GatedGetView(), request) is None

    def test_http_get_still_renders_for_the_permitted_user(self):
        """The page itself was already gated by Django's dispatch; unchanged."""
        response = StaffOnlyView.as_view()(_request("get", _StaffUser()))
        assert response.status_code == 200

    def test_http_get_still_redirects_anonymous(self):
        response = GatedGetView.as_view()(_request("get", _AnonUser()))
        assert response.status_code == 302


# --------------------------------------------------------------------------- #
# The shared predicate
# --------------------------------------------------------------------------- #
class TestPredicate:
    @pytest.mark.parametrize(
        "view_cls",
        [
            StaffOnlyView,
            AuthGetOverrideView,
            DeclaredStaffOnlyView,
            LoginDeclaredStaffOnlyView,
            StaffGateLoginMixinView,
            ProjectStaffDecoratorView,
            DjustNamedAppView,
        ],
    )
    def test_reports_gates(self, view_cls):
        assert http_only_access_control(view_cls)

    @pytest.mark.parametrize(
        "view_cls",
        [
            LiveView,
            GatedGetView,
            GatedDispatchView,
            StaffTestView,
            PermissionView,
            StaffMemberView,
            DjangoLoginMixinView,
            DjangoTestFuncView,
            TenantSetupView,
            NonAccessDecoratorView,
            PlainGetOverrideView,
            ReadingGetView,
            DjustDispatchMixinView,
            DjustAuthPackageView,
        ],
    )
    def test_quiet_without_a_gate(self, view_cls):
        assert http_only_access_control(view_cls) == ()

    @pytest.mark.skipif(login_not_required is None, reason="Django < 5.1")
    def test_login_not_required_is_not_access_control(self):
        assert http_only_access_control(PublicNotRequiredView) == ()
        assert live_auth_reports(PublicNotRequiredView) == ()

    def test_declared_rules_cover_gates(self):
        assert live_auth_refusals(DeclaredStaffOnlyView) == ()
        assert live_auth_refusals(LoginDeclaredAuthGetView) == ()
        assert live_auth_refusals(LoginDeclaredStaffOnlyView)  # login does not cover staff
        assert live_auth_refusals(ProjectStaffDecoratorView)
        assert live_auth_refusals(ProjectDecoratorPublicView) == ()

    def test_reading_override_is_reported_not_refused(self):
        assert live_auth_refusals(ReadingGetView) == ()
        assert [f.label for f in live_auth_reports(ReadingGetView)]

    def test_unknown_decorator_policy(self):
        assert core._UNKNOWN_DECORATORS_REFUSE is EXPECT_UNKNOWN_REFUSED
        for view_cls in (ProjectDecoratorView, ThirdPartyDecoratorView):
            assert bool(live_auth_refusals(view_cls)) is EXPECT_UNKNOWN_REFUSED
            assert bool(live_auth_reports(view_cls)) is not EXPECT_UNKNOWN_REFUSED

    def test_framework_modules(self):
        assert is_framework_module("djust.auth.mixins")
        assert is_framework_module("django.contrib.auth.mixins")
        assert not is_framework_module("djust_auth.mixins")
        assert not is_framework_module("djust_shop.views")
        assert not is_framework_module("djust.tests.test_x")

    def test_declarations(self):
        assert declares_live_auth(DeclaredStaffOnlyView)
        assert declares_live_auth(DjustDispatchMixinView)
        assert declares_live_auth(DjustAuthPackageView)
        assert not declares_live_auth(StaffOnlyView)
        assert not declares_live_auth(type("Public", (StaffOnlyView,), {"login_required": False}))

    def test_login_required_false_is_not_a_declaration(self):
        public = type("PublicStaffView", (StaffOnlyView,), {"login_required": False})
        request = RequestFactory().get("/x/")
        request.user = _AnonUser()
        assert check_view_auth(public(), request) is not None

    def test_http_dispatched_skips_step_5(self):
        request = RequestFactory().get("/x/")
        request.user = _StaffUser()
        assert check_view_auth(StaffOnlyView(), request, http_dispatched=True) is None
        with pytest.raises(PermissionDenied):
            check_view_auth(StaffOnlyView(), request)
        request.user = _AnonUser()
        assert check_view_auth(GatedGetView(), request, http_dispatched=True) is None
        assert check_view_auth(GatedGetView(), request) is not None

    def test_method_decorator_wrapper_is_recognised(self):
        """Pins Django's shared method_decorator wrapper (django.utils.decorators):
        if a Django release changes it, this fails instead of failing open."""
        decorators, inner, readable = core._unwrap_method_decorator(GatedGetView.__dict__["get"])
        assert readable and login_required in decorators
        assert getattr(inner, "__code__", None) is not core._method_decorator_code()

    def test_unreadable_method_decorator_wrapper_is_a_gate(self):
        def _multi_decorate_lookalike(self, request, *args, **kwargs):
            return None

        _multi_decorate_lookalike.__qualname__ = "_multi_decorate.<locals>._wrapper"
        view_cls = _view("OpaqueWrapperView", [], {"dispatch": _multi_decorate_lookalike})
        assert http_only_access_control(view_cls)


# --------------------------------------------------------------------------- #
# The system checks
# --------------------------------------------------------------------------- #
def _s012_for(errors, view_cls):
    label = f"{view_cls.__module__}.{view_cls.__qualname__}:"
    return [e for e in errors if e.id == "djust.S012" and e.msg.startswith(label)]


def _run_security_check(urlpatterns=()):
    import os
    from unittest.mock import patch

    from django.urls import clear_url_caches

    from djust.checks.security import check_security

    urlconf = types.ModuleType("_live_auth_urls_%s" % uuid.uuid4().hex)
    urlconf.urlpatterns = list(urlpatterns)
    sys.modules[urlconf.__name__] = urlconf
    try:
        with override_settings(ROOT_URLCONF=urlconf.__name__):
            clear_url_caches()
            # The check covers views defined under the project's app dirs; make
            # this test module's directory one.
            with patch(
                "djust.checks._get_project_app_dirs", return_value=[os.path.dirname(__file__)]
            ):
                return check_security(None)
    finally:
        clear_url_caches()
        sys.modules.pop(urlconf.__name__, None)


@pytest.mark.django_db
def test_system_check_reports_refused_and_unrunnable_views():
    errors = _run_security_check()

    reported = [
        StaffOnlyView,
        AuthGetOverrideView,
        LoginDeclaredStaffOnlyView,
        StaffGateLoginMixinView,
        ProjectStaffDecoratorView,
        DjustNamedAppView,
    ]
    if EXPECT_UNKNOWN_REFUSED:
        reported.append(ProjectDecoratorView)
    for view_cls in reported:
        assert _s012_for(errors, view_cls), f"no djust.S012 for {view_cls.__name__}"
        assert all(e.level >= 40 for e in _s012_for(errors, view_cls))  # Error
    quiet = [
        GatedGetView,
        GatedDispatchView,
        StaffTestView,
        PermissionView,
        StaffMemberView,
        DeclaredStaffOnlyView,
        LoginDeclaredAuthGetView,
        DjangoLoginMixinView,
        DjangoTestFuncView,
        TenantSetupView,
        NonAccessDecoratorView,
        PlainGetOverrideView,
        DjustDispatchMixinView,
        DjustAuthPackageView,
        ProjectDecoratorPublicView,
        ReadingGetView,  # reads the user without refusing: djust.V013 only
    ]
    if not EXPECT_UNKNOWN_REFUSED:
        quiet.append(ProjectDecoratorView)  # not refused on this line: djust.V013
    if login_not_required is not None:
        quiet.append(PublicNotRequiredView)
    for view_cls in quiet:
        assert not _s012_for(errors, view_cls), f"unexpected djust.S012 for {view_cls.__name__}"
    # A LiveView defined in an app named djust_* is the project's, not djust's.
    assert _s012_for(errors, _djust_shop.ShopView), "no djust.S012 for djust_shop.views.ShopView"
    # S012 is an Error only for views the runtime refuses: a third-party
    # decorator is refused on this line (and so reported) or left to
    # djust.V013's warning.
    assert bool(_s012_for(errors, ThirdPartyDecoratorView)) is EXPECT_UNKNOWN_REFUSED


def _url_checks_wrapper(view_func):
    """A project URLconf wrapper that refuses non-staff users."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


@pytest.mark.django_db
def test_system_check_reports_urlconf_wrappers():
    """Django's URLconf wrappers are replayed (no Error); a wrapper whose code
    checks the user is refused and reported as an Error."""
    from django.urls import path

    WrappedView = _view("WrappedView", [])
    WrappedDeclaredView = _view("WrappedDeclaredView", [], login_required=True)
    WrappedStaffView = _view("WrappedStaffView", [], login_required=True)
    ProjectWrappedView = _view("ProjectWrappedView", [])

    class AsViewOverrideView(_Base):
        template = _template("AsViewOverrideView", "AS-VIEW-MARKER")

        @classmethod
        def as_view(cls, **initkwargs):
            return login_required(super().as_view(**initkwargs))

    AsViewOverrideView.__qualname__ = "AsViewOverrideView"
    sys.modules[__name__].AsViewOverrideView = AsViewOverrideView

    errors = _run_security_check(
        [
            path("wrapped/", login_required(WrappedView.as_view())),
            path("declared/", login_required(WrappedDeclaredView.as_view())),
            path("staff/", user_passes_test(lambda u: u.is_staff)(WrappedStaffView.as_view())),
            path("project/", _url_checks_wrapper(ProjectWrappedView.as_view())),
            path("bare/", PlainGetOverrideView.as_view()),
            path("cached/", never_cache(NonAccessDecoratorView.as_view())),
            path("override/", AsViewOverrideView.as_view()),
        ]
    )
    url_errors = [e for e in errors if e.id == "djust.S012" and "URLconf" in e.msg]

    def _named(view_cls):
        return [e for e in url_errors if f".{view_cls.__qualname__}:" in e.msg]

    for replayed in (WrappedView, WrappedDeclaredView, WrappedStaffView, AsViewOverrideView):
        assert not _named(replayed), url_errors
    assert _named(ProjectWrappedView), url_errors
    assert "project/" in _named(ProjectWrappedView)[0].msg
    assert all(e.level >= 40 for e in _named(ProjectWrappedView))
    assert not _named(PlainGetOverrideView)
    assert not _named(NonAccessDecoratorView)


@pytest.mark.django_db
def test_v013_names_method_decorators():
    from djust.checks.components import check_liveviews

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
        warnings = [w for w in check_liveviews(None) if w.id == "djust.V013"]
    third_party = [w for w in warnings if w.msg.startswith(f"{_MOD}.ThirdPartyDecoratorView:")]
    assert third_party, warnings
    assert "@method_decorator" in third_party[0].msg
    assert "any other decorator does not run on live transports" in third_party[0].msg
    if EXPECT_UNKNOWN_REFUSED:
        assert "djust refuses live requests to this view" in third_party[0].msg
    else:
        assert "Live transports do not run" in third_party[0].msg
    assert not [w for w in warnings if w.msg.startswith(f"{_MOD}.DjustAuthPackageView:")]
    assert [w for w in warnings if w.msg.startswith("djust_shop.views.ShopView:")], warnings


def test_view_in_an_app_named_djust_is_refused_anonymously():
    request = RequestFactory().get("/x/")
    request.user = _AnonUser()
    assert check_view_auth(_djust_shop.ShopView(), request) is not None
