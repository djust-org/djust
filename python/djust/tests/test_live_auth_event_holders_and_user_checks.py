"""HTTP-only access control on live transports, continued: every event path,
stacked decorators, user attributes, delegating decorators.

Companion to ``test_live_auth_denials_and_wrappers.py``.

* What only the page view's ``post()`` runs applies to every event on the
  page: the view's own events, its components' events, its embedded views'
  events and its uploads (WebSocket, SSE, the runtime, the HTTP POST event
  fallback).
* A plain decorator stacked over ``@method_decorator(...)`` does not hide the
  decorators inside it.
* A check of any user attribute (``groups``, ``role``, ``getattr(user, ...)``)
  combined with a denial is a gate, and a rule beyond login: a declared
  ``login_required`` covers a check that reads ``is_authenticated`` /
  ``is_anonymous`` only.
* A project decorator that returns another decorator's wrapper is classified
  by the wrapper it returns.
* A URLconf wrapper without ``functools.wraps`` is named by djust.V013; hot
  view replacement keeps URLconf wrappers applied; an async access test
  called on the event loop's own thread is refused instead of hanging.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import importlib
import json
import os
import sys
import textwrap
import types
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("channels")

from asgiref.sync import sync_to_async  # noqa: E402
from django.contrib.admin.views.decorators import staff_member_required  # noqa: E402
from django.contrib.auth.decorators import login_required, user_passes_test  # noqa: E402
from django.contrib.auth.models import AnonymousUser, Group  # noqa: E402
from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402
from django.core.exceptions import PermissionDenied  # noqa: E402
from django.db import transaction  # noqa: E402
from django.http import Http404  # noqa: E402
from django.shortcuts import redirect  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402
from django.urls import clear_url_caches, path  # noqa: E402
from django.utils.decorators import method_decorator  # noqa: E402
from django.views.decorators.debug import sensitive_variables  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.auth import core  # noqa: E402
from djust.auth.core import check_view_auth, live_auth_refusals  # noqa: E402
from djust.components.descriptors.base import LiveComponent, TypedState  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.tests._ws_frames import drain_extra, receive_until  # noqa: E402

#: This line's policy for a decorator or URLconf wrapper djust cannot classify.
EXPECT_UNKNOWN_REFUSED = True

RAN: list = []
_MOD = __name__


class _Groups:
    def __init__(self, names=()):
        self.names = set(names)

    def filter(self, name=None):
        found = name in self.names

        class _Query:
            def exists(self):
                return found

        return _Query()


class _AnonUser:
    is_authenticated = False
    is_active = False
    is_anonymous = True
    is_staff = False
    is_superuser = False
    pk = None
    role = None
    groups = _Groups()

    def has_perms(self, perms):
        return False


class _MemberUser(_AnonUser):
    is_authenticated = True
    is_active = True
    is_anonymous = False
    pk = 1


class _GroupUser(_MemberUser):
    pk = 3
    groups = _Groups(["editors"])


class _StaffUser(_MemberUser):
    is_staff = True
    pk = 2


class _SuperUser(_StaffUser):
    is_superuser = True
    pk = 5
    role = "admin"
    groups = _Groups(["editors"])

    def has_perms(self, perms):
        return True


def _template(name, body):
    return f'<div dj-root dj-view="{_MOD}.{name}" dj-id="0">{body}</div>'


class _Base(LiveView):
    exposure_policy = "explicit"
    abstract = True

    def mount(self, request, **kwargs):
        self.x = 1

    @event_handler
    def touch(self, **kwargs):
        RAN.append(type(self).__name__)


def _view(name, body=None, decorators=(), base=_Base, **attrs):
    """Build ``name`` in this module (so the WS mount can import it)."""
    ns = {"template": _template(name, f"{name.upper()}-MARKER"), "__module__": _MOD}
    ns.update(attrs)
    if body:
        ns.update(body)
    cls = type(name, (base,), ns)
    cls.__qualname__ = name
    for deco in reversed(decorators):
        cls = deco(cls)
    setattr(sys.modules[__name__], name, cls)
    return cls


_superuser_only = method_decorator(user_passes_test(lambda u: u.is_superuser), name="post")


# --------------------------------------------------------------------------- #
# F1: the page view's post()-only rule on component, embedded and upload paths
# --------------------------------------------------------------------------- #
class Switch(LiveComponent):
    """A component declared on the view class: an explicit view renders and
    routes only those (an instance-assigned one is refused at render)."""

    class State(TypedState):
        on: bool = False

    template = "<span>switch {{ on }}</span>"

    @event_handler()
    def flip(self, **kwargs):
        RAN.append("component-flip")
        self.state.on = not self.state.on


PostGatedWithComponent = _view(
    "PostGatedWithComponent",
    {
        "switch": Switch(),
        "template": _template(
            "PostGatedWithComponent", "POSTGATEDWITHCOMPONENT-MARKER {{ switch }}"
        ),
    },
    decorators=[_superuser_only],
)


class _ChildView(LiveView):
    exposure_policy = "explicit"
    template = "<div>child {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("child-bump")
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count}


@_superuser_only
class _PostGatedParent(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0">parent</div>'

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class _PostGatedPage(_PostGatedParent):
    """The post-gated page embedding ``_ChildView`` as ``child-1``, mounted by
    ``{% live_render %}`` as a browser's page mounts it: an explicit embedded
    view's events need the child mount the tag performs."""

    template = (
        '<div dj-root dj-id="0">{% load live_tags %}parent '
        f'{{% live_render "{_MOD}._ChildView" view_id="child-1" %}}</div>'
    )


# --------------------------------------------------------------------------- #
# F2: a plain decorator stacked over @method_decorator(...)
# --------------------------------------------------------------------------- #
def _logged(fn):
    """A project decorator with no access control."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


def _is_staff(user):
    return user.is_staff


class AtomicOverStaffView(_Base):
    template = _template("AtomicOverStaffView", "ATOMICOVERSTAFFVIEW-MARKER")

    @transaction.atomic
    @method_decorator(staff_member_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class SensitiveOverStaffView(_Base):
    template = _template("SensitiveOverStaffView", "SENSITIVEOVERSTAFFVIEW-MARKER")

    @sensitive_variables()
    @method_decorator(user_passes_test(_is_staff))
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class LoggedOverStaffView(_Base):
    template = _template("LoggedOverStaffView", "LOGGEDOVERSTAFFVIEW-MARKER")

    @_logged
    @method_decorator(staff_member_required)
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# --------------------------------------------------------------------------- #
# F3: any user attribute
# --------------------------------------------------------------------------- #
def _dispatch_groups_redirect(self, request, *args, **kwargs):
    if not request.user.groups.filter(name="editors").exists():
        return redirect("/")
    return LiveView.dispatch(self, request, *args, **kwargs)


def _dispatch_role_404(self, request, *args, **kwargs):
    if getattr(request.user, "role", None) != "admin":
        raise Http404
    return LiveView.dispatch(self, request, *args, **kwargs)


def _get_getattr_staff_redirect(self, request, *args, **kwargs):
    if not getattr(request.user, "is_staff", False):
        return redirect("/")
    return LiveView.get(self, request, *args, **kwargs)


def _get_anonymous_or_role_raise(self, request, *args, **kwargs):
    if request.user.is_anonymous or request.user.role != "admin":
        raise PermissionDenied
    return LiveView.get(self, request, *args, **kwargs)


def _dispatch_login_or_group_redirect(self, request, *args, **kwargs):
    if not request.user.is_authenticated or not request.user.groups.filter(name="editors").exists():
        return redirect("/")
    return LiveView.dispatch(self, request, *args, **kwargs)


def _get_alias_login_raise(self, request, *args, **kwargs):
    user = request.user
    if not user.is_authenticated:
        raise PermissionDenied
    return LiveView.get(self, request, *args, **kwargs)


GroupsRedirectView = _view("GroupsRedirectView", {"dispatch": _dispatch_groups_redirect})
RoleNotFoundView = _view("RoleNotFoundView", {"dispatch": _dispatch_role_404})
GetattrStaffRedirectView = _view("GetattrStaffRedirectView", {"get": _get_getattr_staff_redirect})
LoginDeclaredGroupGateView = _view(
    "LoginDeclaredGroupGateView",
    {"dispatch": _dispatch_login_or_group_redirect},
    login_required=True,
)
LoginDeclaredRoleRaiseView = _view(
    "LoginDeclaredRoleRaiseView", {"get": _get_anonymous_or_role_raise}, login_required=True
)
RoleRaiseNoDeclarationView = _view(
    "RoleRaiseNoDeclarationView", {"get": _get_anonymous_or_role_raise}
)
RoleRaiseCheckedView = _view(
    "RoleRaiseCheckedView",
    {
        "get": _get_anonymous_or_role_raise,
        "check_permissions": lambda self, request: getattr(request.user, "role", None) == "admin",
    },
)
LoginDeclaredAliasLoginView = _view(
    "LoginDeclaredAliasLoginView", {"get": _get_alias_login_raise}, login_required=True
)


# A role or staff check read through a local name: LOAD_FAST_BORROW on Python
# 3.14+ (GHSA-4h5m-xcmg-vvmw).
def _get_alias_role_raise(self, request, *args, **kwargs):
    user = request.user
    if user.role != "admin":
        raise PermissionDenied
    return LiveView.get(self, request, *args, **kwargs)


def _dispatch_two_hop_alias_staff_redirect(self, request, *args, **kwargs):
    u = request.user
    who = u
    if not who.is_staff:
        return redirect("/")
    return LiveView.dispatch(self, request, *args, **kwargs)


LoginDeclaredAliasRoleView = _view(
    "LoginDeclaredAliasRoleView", {"get": _get_alias_role_raise}, login_required=True
)
LoginDeclaredAliasStaffView = _view(
    "LoginDeclaredAliasStaffView",
    {"dispatch": _dispatch_two_hop_alias_staff_redirect},
    login_required=True,
)
ALIAS_REFUSED = ["LoginDeclaredAliasRoleView", "LoginDeclaredAliasStaffView"]


def _role_admin_required(view):
    """A project decorator whose code reads a user attribute and redirects."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if getattr(request.user, "role", None) != "admin":
            return redirect("/")
        return view(request, *args, **kwargs)

    return wrapper


RoleDecoratorView = _view(
    "RoleDecoratorView",
    decorators=[method_decorator(_role_admin_required, name="dispatch")],
    login_required=True,
)


# --------------------------------------------------------------------------- #
# F4: a decorator that delegates to another one
# --------------------------------------------------------------------------- #
def _staff_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def require_staff(view):
    return _staff_required(view)


DelegatingDecoratorView = _view(
    "DelegatingDecoratorView", decorators=[method_decorator(require_staff, name="dispatch")]
)

# --------------------------------------------------------------------------- #
# Minor: a URLconf wrapper without functools.wraps
# --------------------------------------------------------------------------- #
UnwrappedRouteView = _view("UnwrappedRouteView")
HotReloadRoutedView = _view("HotReloadRoutedView")


def _no_wraps_staff(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


_urls = types.ModuleType("_live_auth_event_holders_urls")
_urls.urlpatterns = [
    path("no-wraps/", _no_wraps_staff(UnwrappedRouteView.as_view())),
    path("hot/", login_required(HotReloadRoutedView.as_view())),
]
sys.modules[_urls.__name__] = _urls


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #
@pytest.fixture
def _live():
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[_MOD],
        DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MOD],
        ROOT_URLCONF=_urls.__name__,
    ):
        clear_url_caches()
        yield
    clear_url_caches()


@pytest.fixture(autouse=True)
def _clear_ran():
    RAN.clear()
    yield
    RAN.clear()


ws = pytest.mark.usefixtures("_live")


def _login(user_cls):
    """A saved user, logged into a live session, standing in for ``user_cls``.

    An explicit view binds to the session it was mounted on and re-reads its
    user there, so the stub users (which no session can name) become real ones
    with the same flags: anonymous, a member, a member of the ``editors``
    group, staff, a superuser. Group membership is a real ``Group`` row, so it
    reads the same at the mount and on every event. ``role`` is set on the
    user object, as a project's middleware or user model would provide it;
    the views that read it judge it at the mount.
    """
    from django.contrib.auth import get_user_model

    from djust.testing import live_session

    if user_cls is _AnonUser:
        user = AnonymousUser()
        user.role = None
        return user, live_session(None)
    name = "auth" + uuid.uuid4().hex[:10]
    users = get_user_model().objects
    if user_cls is _SuperUser:
        user = users.create_superuser(name, password="pw-auth-x")
    else:
        user = users.create_user(name, password="pw-auth-x", is_staff=user_cls is _StaffUser)
    if user_cls in (_GroupUser, _SuperUser):
        user.groups.add(Group.objects.get_or_create(name="editors")[0])
    user.role = user_cls.role
    return user, live_session(user)


def _mount_or_close(frames):
    """Nothing more is coming for a mount: the view mounted, or the socket closed."""
    return any(f.get("type") in ("mount", "websocket.close") for f in frames)


def _answered(refs):
    """``done`` predicate: every event in ``refs`` was answered, or the socket closed.

    An event is answered by a frame carrying its ``ref`` (``patch``, ``noop``, ...)
    or by the ``error`` that refuses it.
    """

    def done(frames):
        if any(f.get("type") in ("error", "websocket.close") for f in frames):
            return True
        return set(refs) <= {f.get("ref") for f in frames}

    return done


async def _ws(user_cls, view_name, events=()):
    """Mount ``view_name`` over the real consumer, then send ``events``
    (``(name, params)`` pairs).

    Returns the frames received, each decoded (a close is the raw ASGI message,
    whose ``type`` is ``websocket.close``). The mount, and each event, is always
    answered, so the waits are ``receive_until`` (a 10 s deadline that only a hung
    turn reaches): a short window here gave up when a runner stalled and left the assertions a list with
    nothing in it (#3726).
    """
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    user, session = await sync_to_async(_login)(user_cls)
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope.update(user=user, session=session)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()  # connect ack
    await communicator.send_json_to(
        {"type": "mount", "view": f"{_MOD}.{view_name}", "params": {}, "url": "/x/"}
    )
    outputs = await receive_until(
        communicator,
        _mount_or_close,
        allow_close=True,
        what="the mount or the close that refuses it",
    )
    if events and outputs[-1].get("type") == "mount":
        refs = []
        for ref, (event, params) in enumerate(events, start=1):
            refs.append(ref)
            await communicator.send_json_to(
                {"type": "event", "event": event, "params": dict(params), "ref": ref}
            )
        outputs += await receive_until(
            communicator, _answered(refs), allow_close=True, what="the answer to each event"
        )
        if outputs[-1].get("type") != "websocket.close":
            outputs += await drain_extra(communicator)  # "and nothing else arrived"
    try:
        await communicator.disconnect()
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    return outputs


def _frames(outputs):
    return [o for o in outputs if o.get("type") != "websocket.close"]


def _mounted(outputs):
    return any(f.get("type") == "mount" for f in _frames(outputs))


def _denied(frames):
    """An ``error`` frame refusing the event for permission.

    A legacy view answers "Permission denied" and stays mounted; an explicit
    view applies the same rule while authorizing the turn and ends the view
    ("Event authorization failed", then close 4403). Both carry the code.
    """
    return any(f.get("type") == "error" and f.get("code") == "permission_denied" for f in frames)


async def _assert_refused(view_name, user_cls):
    outputs = await _ws(user_cls, view_name)
    assert not _mounted(outputs), f"{view_name} mounted for {user_cls.__name__}: {outputs!r}"
    assert "MARKER" not in repr(outputs)


async def _assert_mounts(view_name, user_cls):
    outputs = await _ws(user_cls, view_name)
    assert _mounted(outputs), f"{view_name} did not mount for {user_cls.__name__}: {outputs!r}"
    assert f"{view_name.upper()}-MARKER" in repr(outputs)


def _event_post(mount_request):
    """The ``/message/`` POST of the mount's browser: the same session, its user
    read from it as ``AuthenticationMiddleware`` reads it."""
    from django.contrib.auth import get_user

    post = RequestFactory().post("/x/")
    post.session = type(mount_request.session)(mount_request.session.session_key)
    post.user = get_user(post)
    return post


async def _sse(user, view_name, event=None, params=None):
    """Mount ``view_name`` over SSE, then send ``event``.

    Returns whether the MOUNT succeeded (an explicit view whose event is
    refused ends there, so the view is gone after a refused event) and the
    messages queued.
    """
    from djust.sse import SSESession

    request = RequestFactory().get("/x/")
    request.user, request.session = await sync_to_async(_login)(type(user))
    session = SSESession(str(uuid.uuid4()))
    session._owner_user_pk = request.user.pk
    session._request = request
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": f"{_MOD}.{view_name}", "url": "/x/", "params": {}}
    )
    mounted = session.runtime.view_instance is not None
    if event is not None and mounted:
        session._event_request = await sync_to_async(_event_post)(request)
        await session.runtime.dispatch_event({"event": event, "params": dict(params or {})})
    messages = []
    while not session.queue.empty():
        messages.append(session.queue.get_nowait())
    return mounted, messages


def _post(view_cls, user, event="touch", params=None):
    """The HTTP POST event fallback itself (``RequestMixin.post``)."""
    request = RequestFactory().post(
        "/x/",
        data=json.dumps({"event": event, "params": dict(params or {})}),
        content_type="application/json",
    )
    request.user = user
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return LiveView.post(view_cls(), request)


class _MockTransport:
    """A transport that records frames (the runtime is shared by WS and SSE)."""

    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.client_ip = None
        self._client_ip = None
        self.sent: List[Dict[str, Any]] = []

    async def send(self, data):
        self.sent.append(data)

    async def send_error(self, error, **kwargs):
        self.sent.append({"type": "error", "error": error, **kwargs})

    async def close(self, code=1000):
        self.sent.append({"type": "close", "code": code})

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self):
        return None

    def on_view_mounted(self, view_instance):
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view):
        yield


def _request(user):
    request = RequestFactory().get("/x/")
    request.user = user
    request.session = MagicMock()
    return request


async def _runtime_with(parent, user_cls):
    """``parent`` mounted for real on a runtime, by a saved ``user_cls`` user."""
    from djust.runtime import ViewRuntime
    from djust.tests._live_session import amount_on_runtime

    user, _session = await sync_to_async(_login)(user_cls)
    transport = _MockTransport()
    runtime = ViewRuntime(transport)
    await amount_on_runtime(runtime, parent, user=user, url="/x/")
    return runtime, transport


# --------------------------------------------------------------------------- #
# F1
# --------------------------------------------------------------------------- #
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
class TestPostOnlyRuleOnComponentEvents:
    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser, _StaffUser])
    async def test_ws_component_event_is_refused(self, user_cls):
        outputs = await _ws(
            user_cls,
            "PostGatedWithComponent",
            events=[("flip", {"component_id": "switch"})],
        )
        assert _mounted(outputs)
        assert RAN == [], "a component handler ran for %s" % user_cls.__name__
        assert _denied(_frames(outputs)), outputs

    async def test_ws_component_event_runs_for_the_permitted_user(self):
        await _ws(
            _SuperUser, "PostGatedWithComponent", events=[("flip", {"component_id": "switch"})]
        )
        assert RAN == ["component-flip"]

    async def test_sse_component_event(self):
        mounted, messages = await _sse(
            _MemberUser(), "PostGatedWithComponent", "flip", {"component_id": "switch"}
        )
        assert mounted is True
        assert RAN == []
        assert "permission_denied" in repr(messages)
        await _sse(_SuperUser(), "PostGatedWithComponent", "flip", {"component_id": "switch"})
        assert RAN == ["component-flip"]


@pytest.mark.django_db
class TestPostOnlyRuleOnThePostFallback:
    def test_component_event_is_refused(self):
        response = _post(
            PostGatedWithComponent, _MemberUser(), event="flip", params={"component_id": "switch"}
        )
        assert response.status_code == 403
        assert RAN == []


async def _sticky_parent(user_cls):
    """The post-gated page, mounted by ``user_cls``, holding an embedded view."""
    parent = _PostGatedPage()
    runtime, transport = await _runtime_with(parent, user_cls)
    child = parent._get_all_child_views()["child-1"]
    assert isinstance(child, _ChildView) and child.count == 0
    return runtime, transport, child


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
class TestPostOnlyRuleOnTheRuntime:
    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser, _StaffUser])
    async def test_embedded_child_event_is_refused(self, user_cls):
        runtime, transport, child = await _sticky_parent(user_cls)
        await runtime.dispatch_event(
            {"type": "event", "event": "bump", "params": {"view_id": "child-1"}, "ref": 1}
        )
        assert RAN == [] and child.count == 0
        assert _denied(transport.sent), transport.sent

    async def test_embedded_child_event_runs_for_the_permitted_user(self):
        runtime, _transport, _child = await _sticky_parent(_SuperUser)
        await runtime.dispatch_event(
            {"type": "event", "event": "bump", "params": {"view_id": "child-1"}, "ref": 1}
        )
        assert RAN == ["child-bump"]

    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser])
    async def test_component_event_is_refused(self, user_cls):
        runtime, transport = await _runtime_with(PostGatedWithComponent(), user_cls)
        await runtime.dispatch_event(
            {"type": "event", "event": "flip", "params": {"component_id": "switch"}, "ref": 1}
        )
        assert RAN == []
        assert _denied(transport.sent), transport.sent

    async def test_component_event_runs_for_the_permitted_user(self):
        runtime, _transport = await _runtime_with(PostGatedWithComponent(), _SuperUser)
        await runtime.dispatch_event(
            {"type": "event", "event": "flip", "params": {"component_id": "switch"}, "ref": 1}
        )
        assert RAN == ["component-flip"]

    async def test_a_view_without_a_request_is_refused(self):
        from djust.websocket_utils import _post_only_rules_deny

        parent = _PostGatedParent()
        child = _ChildView()
        child._parent_view = parent
        assert await _post_only_rules_deny(child) is True


async def _upload_consumer(user_cls):
    """The post-gated page mounted for real on a consumer by ``user_cls``, with uploads."""
    from djust.tests._live_session import amount_on_consumer
    from djust.uploads import UploadManager
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    user, _session = await sync_to_async(_login)(user_cls)
    view = await amount_on_consumer(
        consumer, f"{_MOD}._PostGatedParent", user=user, url="/x/", view=_PostGatedParent()
    )
    consumer.send_json.reset_mock()
    consumer.send_error.reset_mock()
    manager = UploadManager()
    manager.configure("doc", accept=".pdf", max_entries=2)
    view._upload_manager = manager
    return consumer, manager


def _resume_upload_consumer(user):
    from djust.uploads import UploadManager
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    view = _PostGatedParent()
    view.request = _request(user)
    manager = UploadManager()
    manager.configure("doc", accept=".pdf", max_entries=2)
    view._upload_manager = manager
    consumer.view_instance = view
    consumer.scope = {"session": MagicMock(session_key="k")}
    return consumer, manager


_REGISTER = {
    "type": "upload_register",
    "upload_name": "doc",
    "ref": "ref-1",
    "client_name": "a.pdf",
    "client_type": "application/pdf",
    "client_size": 10,
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
class TestPostOnlyRuleOnUploads:
    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser])
    async def test_upload_register_is_refused(self, user_cls):
        consumer, manager = await _upload_consumer(user_cls)
        await consumer._handle_upload_register(dict(_REGISTER))
        assert "ref-1" not in manager._entries
        consumer.send_json.assert_not_awaited()
        assert "Permission denied" in repr(consumer.send_error.await_args)

    async def test_upload_register_for_the_permitted_user(self):
        consumer, manager = await _upload_consumer(_SuperUser)
        await consumer._handle_upload_register(dict(_REGISTER))
        assert "ref-1" in manager._entries
        assert consumer.send_json.await_args.args[0]["type"] == "upload_registered"

    @pytest.mark.parametrize(
        "user_cls, status", [(_MemberUser, "not_found"), (_SuperUser, "resumed")]
    )
    async def test_upload_resume(self, monkeypatch, user_cls, status):
        consumer, manager = _resume_upload_consumer(user_cls())
        manager.resume_entry = lambda upload_id, session_key, owner=None: object()
        monkeypatch.setattr(
            "djust.uploads.resumable.resolve_resume_request",
            lambda **kwargs: {
                "type": "upload_resumed",
                "ref": "ref-1",
                "status": "resumed",
                "bytes_received": 5,
                "chunks_received": [0],
            },
        )
        await consumer._handle_upload_resume({"type": "upload_resume", "ref": "ref-1"})
        assert consumer.send_json.await_args.args[0]["status"] == status


# --------------------------------------------------------------------------- #
# F2
# --------------------------------------------------------------------------- #
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
class TestDecoratorOverMethodDecorator:
    async def test_inner_django_decorator_is_replayed(self):
        await _assert_refused("SensitiveOverStaffView", _AnonUser)
        await _assert_refused("SensitiveOverStaffView", _MemberUser)
        await _assert_mounts("SensitiveOverStaffView", _StaffUser)

    @pytest.mark.parametrize("view_name", ["AtomicOverStaffView", "LoggedOverStaffView"])
    async def test_inner_decorator_under_a_non_django_one(self, view_name):
        await _assert_refused(view_name, _AnonUser)
        await _assert_refused(view_name, _MemberUser)
        if EXPECT_UNKNOWN_REFUSED:
            # The outer decorator is unknown: refused on this line.
            await _assert_refused(view_name, _StaffUser)
        else:
            await _assert_mounts(view_name, _StaffUser)


def test_inner_decorators_are_found():
    for view_cls in (AtomicOverStaffView, SensitiveOverStaffView, LoggedOverStaffView):
        analysis = core._analyse(view_cls)
        assert len(analysis.replays) == 1, (view_cls, analysis)
    assert live_auth_refusals(SensitiveOverStaffView) == ()


# --------------------------------------------------------------------------- #
# F3
# --------------------------------------------------------------------------- #
F3_REFUSED = ["GroupsRedirectView", "RoleNotFoundView", "GetattrStaffRedirectView"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
class TestUserAttributeGates:
    @pytest.mark.parametrize("view_name", F3_REFUSED)
    @pytest.mark.parametrize("user_cls", [_AnonUser, _StaffUser, _SuperUser])
    async def test_refused_until_declared(self, view_name, user_cls):
        await _assert_refused(view_name, user_cls)

    @pytest.mark.parametrize(
        "view_name",
        ["LoginDeclaredGroupGateView", "LoginDeclaredRoleRaiseView", "RoleDecoratorView"]
        + ALIAS_REFUSED,
    )
    @pytest.mark.parametrize("user_cls", [_MemberUser, _GroupUser, _StaffUser])
    async def test_login_rule_does_not_cover_it(self, view_name, user_cls):
        await _assert_refused(view_name, user_cls)

    async def test_declared_rule_admits_the_permitted_user(self):
        await _assert_mounts("RoleRaiseCheckedView", _SuperUser)
        await _assert_refused("RoleRaiseCheckedView", _StaffUser)

    async def test_login_rule_covers_a_login_only_check_through_an_alias(self):
        await _assert_refused("LoginDeclaredAliasLoginView", _AnonUser)
        await _assert_mounts("LoginDeclaredAliasLoginView", _MemberUser)


@pytest.mark.django_db
class TestUserAttributeGatesOnThePostFallback:
    @pytest.mark.parametrize(
        "view_name", F3_REFUSED + ["LoginDeclaredRoleRaiseView"] + ALIAS_REFUSED
    )
    def test_refused(self, view_name):
        response = _post(getattr(sys.modules[__name__], view_name), _StaffUser())
        assert response.status_code == 403
        assert RAN == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
async def test_user_attribute_gate_over_sse():
    mounted, messages = await _sse(_StaffUser(), "GroupsRedirectView")
    assert mounted is False
    assert "MARKER" not in repr(messages)


def test_user_attributes_read():
    def attrs(func):
        return core._user_attributes(func.__code__)

    assert attrs(_dispatch_groups_redirect) == {"groups"}
    assert attrs(_dispatch_role_404) == {"role"}
    assert attrs(_get_getattr_staff_redirect) == {"is_staff"}
    assert attrs(_get_anonymous_or_role_raise) == {"is_anonymous", "role"}
    assert attrs(_get_alias_login_raise) == {"is_authenticated"}
    assert attrs(_get_alias_role_raise) == {"role"}
    assert attrs(_dispatch_two_hop_alias_staff_redirect) == {"is_staff"}

    def helper_call(self, request):
        if not _has_role(request.user, "admin"):
            raise PermissionDenied

    assert attrs(helper_call) == {"*"}


def _has_role(user, role):  # pragma: no cover - only its call is read
    return getattr(user, "role", None) == role


class _Inst:
    def __init__(self, opname, argval):
        self.opname = opname
        self.argval = argval


@pytest.mark.parametrize(
    "opname, argval, expected",
    [
        ("LOAD_FAST", "user", True),
        ("LOAD_FAST_BORROW", "user", True),  # Python 3.14+
        ("LOAD_FAST_SOME_FUTURE_FORM", "user", True),
        ("LOAD_FAST_BORROW", "other", False),
        ("LOAD_UNKNOWN_FUTURE_LOAD", "user", None),  # fail closed
        ("LOAD_CONST", "user", False),  # the string "user", not the user
        ("LOAD_FAST_BORROW_LOAD_FAST_BORROW", ("self", "user"), None),
        ("STORE_FAST_LOAD_FAST", ("x", "user"), None),
        ("LOAD_FAST_LOAD_FAST", ("self", "request"), False),
    ],
)
def test_loads_user_recognises_every_local_load(opname, argval, expected):
    assert core._loads_user(_Inst(opname, argval), {"user"}) is expected


# --------------------------------------------------------------------------- #
# F4
# --------------------------------------------------------------------------- #
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
class TestDelegatingDecorator:
    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser, _StaffUser])
    async def test_refused_until_declared(self, user_cls):
        await _assert_refused("DelegatingDecoratorView", user_cls)


def test_delegating_decorator_is_a_gate_beyond_login():
    findings = core._analyse(DelegatingDecoratorView).findings
    assert [f.kind for f in findings] == [core._GATE], findings
    assert findings[0].beyond_login is True
    assert "checks the user" in findings[0].label


# --------------------------------------------------------------------------- #
# The system checks
# --------------------------------------------------------------------------- #
def _run_checks(urlpatterns=()):
    from djust.checks.components import check_liveviews
    from djust.checks.security import check_security

    urlconf = types.ModuleType("_live_auth_holders_check_urls_%s" % uuid.uuid4().hex)
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


def _for(messages, check_id, cls):
    label = "%s.%s" % (cls.__module__, cls.__qualname__)
    return [m for m in messages if m.id == check_id and m.msg.startswith(label + ":")]


@pytest.mark.django_db
def test_checks():
    messages = _run_checks([path("no-wraps/", _no_wraps_staff(UnwrappedRouteView.as_view()))])
    for refused in (
        GroupsRedirectView,
        RoleNotFoundView,
        LoginDeclaredRoleRaiseView,
        RoleRaiseNoDeclarationView,
        DelegatingDecoratorView,
    ):
        errors = _for(messages, "djust.S012", refused)
        assert len(errors) == 1 and errors[0].level >= 40, (refused, errors)
        # A rule beyond login: the fix does not offer login_required.
        assert "login_required" not in errors[0].fix_hint, errors[0].fix_hint
        assert "which runs only on the HTTP request" not in errors[0].msg
        assert "this access control runs only on the HTTP request:" in errors[0].msg
    assert not _for(messages, "djust.S012", SensitiveOverStaffView)
    assert not _for(messages, "djust.S012", LoginDeclaredAliasLoginView)
    # The hint does not lead with login_required.
    hint = _for(messages, "djust.S012", GroupsRedirectView)[0].hint
    assert hint.index("permission_required") < hint.index("login_required")
    assert "`is_authenticated` / `is_anonymous` alone" in hint
    # A URLconf wrapper djust cannot read is named by V013.
    hidden = _for(messages, "djust.V013", UnwrappedRouteView)
    assert hidden and "no-wraps/'" in hidden[0].msg and "functools.wraps" in hidden[0].msg


@pytest.mark.django_db
def test_login_only_refusal_offers_login_required():
    class LoginOnlyGateView(_Base):
        template = "<div>x</div>"

        def get(self, request, *args, **kwargs):
            if not request.user.is_authenticated:
                raise PermissionDenied
            return super().get(request, *args, **kwargs)

    LoginOnlyGateView.__module__ = _MOD
    LoginOnlyGateView.__qualname__ = "LoginOnlyGateView"
    try:
        messages = _run_checks()
        errors = _for(messages, "djust.S012", LoginOnlyGateView)
        assert len(errors) == 1
        assert errors[0].fix_hint.index("login_required") < errors[0].fix_hint.index(
            "permission_required"
        )
    finally:
        del LoginOnlyGateView


# --------------------------------------------------------------------------- #
# Minor: hot view replacement, the event loop thread
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.usefixtures("_live")
def test_a_reloaded_class_keeps_its_urlconf_wrapper():
    request = _request(_AnonUser())
    assert check_view_auth(HotReloadRoutedView(), request) is not None
    reloaded = type(
        "HotReloadRoutedView",
        (_Base,),
        {"__module__": _MOD, "template": HotReloadRoutedView.template},
    )
    reloaded.__qualname__ = "HotReloadRoutedView"
    assert check_view_auth(reloaded(), request) is not None
    request.user = _MemberUser()
    assert check_view_auth(reloaded(), request) is None


@pytest.mark.django_db
@pytest.mark.usefixtures("_live")
def test_hot_view_replacement_forgets_the_url_map(tmp_path):
    from djust import hot_view_replacement

    name = "_hvr_live_auth_%s" % uuid.uuid4().hex
    source = tmp_path / ("%s.py" % name)
    source.write_text(
        textwrap.dedent(
            """
            from djust import LiveView

            class Reloaded(LiveView):
                exposure_policy = "explicit"
                template = "<div>x</div>"
            """
        )
    )
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module(name)
        core._url_map(None)
        assert core._url_cache
        assert hot_view_replacement.reload_module_if_liveview(str(source)) is not None
        assert not core._url_cache
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop(name, None)


async def _async_is_staff(user):
    await asyncio.sleep(0)
    return bool(user.is_staff)


AsyncStaffView = _view(
    "AsyncStaffView", decorators=[method_decorator(user_passes_test(_async_is_staff), name="get")]
)


@pytest.mark.django_db
@pytest.mark.asyncio
class TestAsyncTestOnTheLoopThread:
    async def test_refused_with_a_clear_error(self, caplog):
        request = _request(_StaffUser())
        # Called directly on the running loop's thread: refused (a permitted
        # user too) with an error naming the fix, instead of blocking.
        with pytest.raises(PermissionDenied):
            check_view_auth(AsyncStaffView(), request)
        assert "sync_to_async" in caplog.text

    async def test_through_sync_to_async(self):
        from asgiref.sync import sync_to_async

        request = _request(_StaffUser())
        assert await sync_to_async(check_view_auth)(AsyncStaffView(), request) is None
        request.user = _MemberUser()
        with pytest.raises(PermissionDenied):
            await sync_to_async(check_view_auth)(AsyncStaffView(), request)


# --------------------------------------------------------------------------- #
# A response the override builds itself, import aliases, get_object_or_404
# --------------------------------------------------------------------------- #
from django.http import HttpResponse  # noqa: E402
from django.http import HttpResponseRedirect as _Moved  # noqa: E402
from django.shortcuts import get_object_or_404, render  # noqa: E402
from django.shortcuts import redirect as _go  # noqa: E402
from django.template.response import TemplateResponse  # noqa: E402


def _get_staff_renders_denied_page(self, request, *args, **kwargs):
    if not request.user.is_staff:
        return render(request, "denied.html")
    return LiveView.get(self, request, *args, **kwargs)


def _get_role_paywall(self, request, *args, **kwargs):
    if getattr(request.user, "role", None) != "admin":
        return HttpResponse("Upgrade required")
    return LiveView.get(self, request, *args, **kwargs)


def _get_perm_template_response(self, request, *args, **kwargs):
    if not request.user.has_perms(["app.view"]):
        return TemplateResponse(request, "denied.html")
    return LiveView.get(self, request, *args, **kwargs)


def _dispatch_method_not_allowed(self, request, *args, **kwargs):
    if not request.user.is_superuser:
        return self.http_method_not_allowed(request, *args, **kwargs)
    return LiveView.dispatch(self, request, *args, **kwargs)


def _post_staff_http_response(self, request, *args, **kwargs):
    if not request.user.is_staff:
        return HttpResponse("nope")
    return LiveView.post(self, request, *args, **kwargs)


def _dispatch_alias_redirect(self, request, *args, **kwargs):
    if not request.user.is_staff:
        return _go("/")
    return LiveView.dispatch(self, request, *args, **kwargs)


def _get_alias_redirect_class(self, request, *args, **kwargs):
    if getattr(request.user, "role", None) != "admin":
        return _Moved("/")
    return LiveView.get(self, request, *args, **kwargs)


def _dispatch_owned_object(self, request, *args, **kwargs):
    get_object_or_404(Group, name="editors", user=request.user)
    return LiveView.dispatch(self, request, *args, **kwargs)


def _get_reads_staff_then_super(self, request, *args, **kwargs):
    self.show_admin_link = request.user.is_staff
    return LiveView.get(self, request, *args, **kwargs)


def _get_renders_without_reading_the_user(self, request, *args, **kwargs):
    if request.GET.get("legacy"):
        return render(request, "legacy.html")
    return LiveView.get(self, request, *args, **kwargs)


OWN_PAGE_VIEWS = {
    "StaffRendersDeniedPageView": {"get": _get_staff_renders_denied_page},
    "RolePaywallView": {"get": _get_role_paywall},
    "PermTemplateResponseView": {"get": _get_perm_template_response},
    "MethodNotAllowedView": {"dispatch": _dispatch_method_not_allowed},
    "AliasRedirectView": {"dispatch": _dispatch_alias_redirect},
    "AliasRedirectClassView": {"get": _get_alias_redirect_class},
    "OwnedObjectView": {"dispatch": _dispatch_owned_object},
}
for _name, _body in OWN_PAGE_VIEWS.items():
    _view(_name, _body)
PostRendersForNonStaffView = _view(
    "PostRendersForNonStaffView", {"post": _post_staff_http_response}
)
ReadsStaffThenSuperView = _view("ReadsStaffThenSuperView", {"get": _get_reads_staff_then_super})
RendersWithoutUserView = _view(
    "RendersWithoutUserView", {"get": _get_renders_without_reading_the_user}
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@ws
class TestOwnResponseGates:
    @pytest.mark.parametrize("view_name", sorted(OWN_PAGE_VIEWS))
    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser, _StaffUser, _SuperUser])
    async def test_refused_on_the_websocket(self, view_name, user_cls):
        await _assert_refused(view_name, user_cls)

    @pytest.mark.parametrize("view_name", sorted(OWN_PAGE_VIEWS))
    async def test_refused_over_sse(self, view_name):
        for user in (_AnonUser(), _SuperUser()):
            mounted, messages = await _sse(user, view_name)
            assert mounted is False, (view_name, type(user).__name__)
            assert "MARKER" not in repr(messages)

    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser, _StaffUser, _SuperUser])
    async def test_post_only_page_refuses_events(self, user_cls):
        outputs = await _ws(user_cls, "PostRendersForNonStaffView", events=[("touch", {})])
        assert _mounted(outputs)
        assert RAN == []
        assert _denied(_frames(outputs)), outputs

    @pytest.mark.parametrize("user_cls", [_AnonUser, _MemberUser, _StaffUser])
    async def test_an_override_that_only_reads_the_user_is_not_refused(self, user_cls):
        await _assert_mounts("ReadsStaffThenSuperView", user_cls)
        await _assert_mounts("RendersWithoutUserView", user_cls)


@pytest.mark.django_db
class TestOwnResponseGatesOnThePostFallback:
    @pytest.mark.parametrize("view_name", sorted(OWN_PAGE_VIEWS) + ["PostRendersForNonStaffView"])
    @pytest.mark.parametrize("user_cls", [_StaffUser, _SuperUser])
    def test_refused(self, view_name, user_cls):
        response = _post(getattr(sys.modules[__name__], view_name), user_cls())
        assert response.status_code == 403
        assert RAN == []


def test_own_response_and_alias_analysis():
    def denies(func, namespace=None):
        return core._denies(func.__code__, namespace)

    for func in (
        _get_staff_renders_denied_page,
        _get_role_paywall,
        _get_perm_template_response,
        _dispatch_method_not_allowed,
        _post_staff_http_response,
        _dispatch_owned_object,
    ):
        assert denies(func), func.__name__
    # An alias is resolved through the module's names.
    for func in (_dispatch_alias_redirect, _get_alias_redirect_class):
        assert not denies(func), func.__name__
        assert denies(func, func.__globals__), func.__name__
    assert core._is_django_denial(_go) and core._is_django_denial(_Moved)
    assert not core._is_django_denial(_has_role) and not core._is_django_denial(Group)
    assert core._user_attributes(_dispatch_owned_object.__code__) == {"*"}
    for name in OWN_PAGE_VIEWS:
        findings = core._analyse(getattr(sys.modules[__name__], name)).findings
        assert [f.kind for f in findings] == [core._GATE], (name, findings)
        assert findings[0].beyond_login is True, name
    # Reading the user without denying, or building a page without reading
    # the user, is not a gate.
    assert [f.kind for f in core._analyse(ReadsStaffThenSuperView).findings] == [core._PREDICATE]
    assert core._analyse(RendersWithoutUserView).findings == ()


@pytest.mark.django_db
def test_own_response_checks():
    messages = _run_checks()
    for name in sorted(OWN_PAGE_VIEWS) + ["PostRendersForNonStaffView"]:
        cls = getattr(sys.modules[__name__], name)
        errors = _for(messages, "djust.S012", cls)
        assert len(errors) == 1 and errors[0].level >= 40, (name, errors)
        assert "login_required" not in errors[0].fix_hint, (name, errors[0].fix_hint)
    assert not _for(messages, "djust.S012", ReadsStaffThenSuperView)
    assert _for(messages, "djust.V013", ReadsStaffThenSuperView)
    assert not _for(messages, "djust.S012", RendersWithoutUserView)
