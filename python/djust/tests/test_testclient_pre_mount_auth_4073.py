"""``LiveViewTestClient.mount()`` runs the transports' pre-mount auth sequence (#4073).

Every transport runs ``djust.auth.core.run_pre_mount_auth`` before ``mount()``:
``login_required``, ``permission_required`` and ``check_permissions(request)``
(``check_view_auth``), plus the tenant resolve/bind. The test client skipped it,
so a test mounting a gated view as a user the transports refuse got a mounted
view. Now a refusal is raised, as the post-mount object check already raises:

* an authenticated user the view refuses -> ``PermissionDenied`` (a transport
  answers 403 / a ``permission_denied`` frame);
* an anonymous user on a ``login_required`` view -> ``MountRedirect`` carrying
  the login URL (the HTTP GET answers 302, the socket a ``navigate`` frame).

In both cases ``mount()`` never runs and the client is not marked mounted.
"""

import uuid

import pytest
from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied

from djust import LiveView
from djust.testing import LiveViewTestClient, MountRedirect

pytestmark = pytest.mark.django_db

MOUNTED: list = []


class _Base(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>ok</div>"

    def mount(self, request, **kwargs):
        MOUNTED.append(type(self).__name__)


class LoginView(_Base):
    login_required = True


class PermView(_Base):
    permission_required = "auth.view_user"


class HookView(_Base):
    def check_permissions(self, request):
        return getattr(request.user, "username", "") == "allowed"


class AsyncHookView(_Base):
    async def check_permissions(self, request):
        return getattr(request.user, "username", "") == "allowed"


class OpenView(_Base):
    pass


HOOK_CALLS: list = []


def require_staff(view, request, **kwargs):
    HOOK_CALLS.append(("require_staff", dict(kwargs)))
    if not getattr(request.user, "is_staff", False):
        return "/staff-only/"
    return None


def note_mount(view, request, **kwargs):
    HOOK_CALLS.append(("note_mount", dict(kwargs)))


class HookRedirectView(_Base):
    on_mount = [require_staff]


class HookPassView(_Base):
    on_mount = [note_mount]


@pytest.fixture(autouse=True)
def _reset():
    MOUNTED.clear()
    HOOK_CALLS.clear()
    yield
    MOUNTED.clear()
    HOOK_CALLS.clear()


def _user(name="u", perm=None):
    user = User.objects.create_user(
        username=f"{name}" if name == "allowed" else f"{name}-{uuid.uuid4().hex[:6]}"
    )
    if perm:
        app_label, codename = perm.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )
        user = User.objects.get(pk=user.pk)  # drop the permission cache
    return user


def test_login_required_anonymous_raises_mount_redirect_and_mount_never_runs():
    client = LiveViewTestClient(LoginView)
    with pytest.raises(MountRedirect) as excinfo:
        client.mount()
    assert excinfo.value.url  # the login URL the transports redirect to
    assert "login" in excinfo.value.url
    assert MOUNTED == []
    with pytest.raises(RuntimeError, match="not mounted"):
        client.render()


def test_login_required_with_a_user_mounts():
    LiveViewTestClient(LoginView, user=_user()).mount()
    assert MOUNTED == ["LoginView"]


def test_permission_required_without_the_permission_raises_permission_denied():
    client = LiveViewTestClient(PermView, user=_user())
    with pytest.raises(PermissionDenied):
        client.mount()
    assert MOUNTED == []


def test_permission_required_anonymous_is_a_login_redirect():
    with pytest.raises(MountRedirect):
        LiveViewTestClient(PermView).mount()
    assert MOUNTED == []


def test_permission_required_with_the_permission_mounts():
    LiveViewTestClient(PermView, user=_user(perm="auth.view_user")).mount()
    assert MOUNTED == ["PermView"]


@pytest.mark.parametrize("view", [HookView, AsyncHookView], ids=["sync", "async"])
def test_check_permissions_refusal_raises_permission_denied(view):
    with pytest.raises(PermissionDenied):
        LiveViewTestClient(view, user=_user("other")).mount()
    assert MOUNTED == []


@pytest.mark.parametrize("view", [HookView, AsyncHookView], ids=["sync", "async"])
def test_check_permissions_pass_mounts(view):
    LiveViewTestClient(view, user=_user("allowed")).mount()
    assert MOUNTED == [view.__name__]


def test_ungated_view_mounts_anonymous():
    LiveViewTestClient(OpenView).mount()
    assert MOUNTED == ["OpenView"]


def test_the_parity_helper_instance_is_refused_too():
    """``_build_mounted_instance`` (the HTTP/WS parity helper) mounts through
    the same path, so it cannot mount a view the transports refuse."""
    client = LiveViewTestClient(LoginView)
    with pytest.raises(MountRedirect):
        client._build_mounted_instance()
    assert MOUNTED == []


def test_an_on_mount_hook_redirect_raises_mount_redirect_and_mount_never_runs():
    """Every transport runs the ``on_mount`` hooks between the auth and
    ``mount()``; a hook that returns a URL refuses the mount (302 / navigate)."""
    client = LiveViewTestClient(HookRedirectView, user=_user())
    with pytest.raises(MountRedirect) as excinfo:
        client.mount(item=3)
    assert excinfo.value.url == "/staff-only/"
    assert HOOK_CALLS == [("require_staff", {"item": 3})]
    assert MOUNTED == []


def test_an_on_mount_hook_that_passes_runs_with_the_mount_kwargs_then_mount_runs():
    LiveViewTestClient(HookPassView).mount(item=3)
    assert HOOK_CALLS == [("note_mount", {"item": 3})]
    assert MOUNTED == ["HookPassView"]


def test_an_on_mount_hook_redirect_refuses_the_parity_helper_instance_too():
    with pytest.raises(MountRedirect):
        LiveViewTestClient(HookRedirectView, user=_user())._build_mounted_instance()
    assert MOUNTED == []


def test_the_hooks_do_not_run_when_the_auth_refused():
    class GatedHook(_Base):
        login_required = True
        on_mount = [note_mount]

    with pytest.raises(MountRedirect):
        LiveViewTestClient(GatedHook).mount()
    assert HOOK_CALLS == []


@pytest.mark.asyncio
async def test_an_async_check_permissions_from_a_running_loop_says_what_to_do():
    with pytest.raises(RuntimeError, match="sync_to_async"):
        LiveViewTestClient(AsyncHookView).mount()
