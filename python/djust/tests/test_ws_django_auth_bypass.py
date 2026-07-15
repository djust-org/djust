"""Regression tests for the WebSocket view-auth bypass (findings #13 + #14).

The WS/SSE mount path authorizes via :func:`djust.auth.core.check_view_auth`,
NOT via Django's ``View.dispatch()`` chain. Before the fix, that meant standard
Django authorization — ``LoginRequiredMixin`` / ``UserPassesTestMixin`` /
``@method_decorator(login_required, name="dispatch")`` — and djust's own admin
extension (which gated only via the HTTP ``as_view`` wrapper) were enforced on
the initial HTTP GET but silently bypassed over WebSocket.

These tests pin that ``check_view_auth`` now denies an anonymous/under-privileged
user for the Django auth mixins (auto-honored at runtime), and that the admin
extension declares djust-honored auth so the WS path gates it too.
"""

import pytest
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from djust import LiveView
from djust.auth.core import check_view_auth


def _anon_request():
    req = RequestFactory().get("/secret/")
    req.user = AnonymousUser()
    return req


# --- Django auth mixins are honored on the WS authorizer path (finding #14) ---


class _LoginRequiredView(LoginRequiredMixin, LiveView):
    template_name = None

    def get_context_data(self, **kwargs):
        return {}


class _SuperuserOnlyView(UserPassesTestMixin, LiveView):
    template_name = None

    def test_func(self):
        return bool(self.request.user.is_superuser)

    def get_context_data(self, **kwargs):
        return {}


class _PermRequiredView(PermissionRequiredMixin, LiveView):
    permission_required = "auth.view_user"
    template_name = None

    def get_context_data(self, **kwargs):
        return {}


def test_login_required_mixin_denied_for_anon_on_ws_authorizer():
    """LoginRequiredMixin must NOT be silently bypassed by check_view_auth."""
    result = check_view_auth(_LoginRequiredView(), _anon_request())
    # Anonymous + not raise_exception => redirect URL (not None == allowed)
    assert result is not None, "LoginRequiredMixin bypassed on the WS auth path"


def test_user_passes_test_mixin_denied_for_anon_on_ws_authorizer():
    """UserPassesTestMixin (e.g. is_superuser gate) must not be bypassed."""
    result = check_view_auth(_SuperuserOnlyView(), _anon_request())
    assert result is not None, "UserPassesTestMixin bypassed on the WS auth path"


def test_permission_required_mixin_denied_for_anon_on_ws_authorizer():
    result = check_view_auth(_PermRequiredView(), _anon_request())
    assert result is not None, "PermissionRequiredMixin bypassed on the WS auth path"


def test_login_required_mixin_allows_authenticated(django_user_model):
    """A logged-in user passes the LoginRequiredMixin gate (no over-blocking)."""
    user = django_user_model(username="alice", is_active=True)
    req = RequestFactory().get("/secret/")
    req.user = user
    assert check_view_auth(_LoginRequiredView(), req) is None


def test_user_passes_test_raises_for_authenticated_failure(django_user_model):
    """Authenticated-but-fails-test => PermissionDenied (Django handle_no_permission)."""
    user = django_user_model(username="bob", is_active=True, is_superuser=False)
    req = RequestFactory().get("/secret/")
    req.user = user
    with pytest.raises(PermissionDenied):
        check_view_auth(_SuperuserOnlyView(), req)


def test_check_view_auth_no_side_effect_on_request_attr():
    """check_view_auth must not leave its temporary .request stamped on the view.

    LiveView.__init__ sets self.request = None, so a presence check
    (``"request" in __dict__``) is always True and would be tautological — assert
    on the VALUE/identity instead.
    """
    # Fresh view (request is None): the mixin check sets request temporarily and
    # must restore it to None.
    view = _LoginRequiredView()
    assert view.request is None
    check_view_auth(view, _anon_request())
    assert view.request is None, "check_view_auth left a request stamped on the view"

    # Preset request: must be preserved by identity, never overwritten/cleared.
    view2 = _LoginRequiredView()
    sentinel = _anon_request()
    view2.request = sentinel
    check_view_auth(view2, _anon_request())
    assert view2.request is sentinel, "check_view_auth clobbered a preset request"


# --- djust admin extension gates the WS path too (finding #13) ---


def test_admin_views_declare_ws_honored_auth():
    """AdminBaseMixin must declare djust-honored auth so the WS path gates it."""
    from djust.admin_ext.views import ModelDeleteView, ModelListView

    req = _anon_request()
    for V in (ModelListView, ModelDeleteView):
        try:
            result = check_view_auth(V(), req)
        except PermissionDenied:
            result = "denied"
        assert result not in (None,), (
            "%s admin view is not staff-gated on the WS auth path" % V.__name__
        )


# --- S012 system check: flag the un-honorable decorated-dispatch pattern (#14) ---
# (reallocated from a duplicate djust.S004, #2070 -- see docs/system-checks.md)

import ast  # noqa: E402

from djust.checks.security import (  # noqa: E402
    _is_dispatch_auth_method_decorator,
    _is_liveview_subclass,
    _liveview_auth_dispatch_method,
)


def _classdef(src):
    return ast.parse(src).body[0]


def test_s012_flags_decorated_dispatch_on_liveview():
    """The exact un-portable pattern must be detected (empirical canary)."""
    cls = _classdef(
        "@method_decorator(login_required, name='dispatch')\n"
        "class SecretView(LiveView):\n    pass\n"
    )
    assert _is_liveview_subclass(cls)
    assert any(_is_dispatch_auth_method_decorator(d) for d in cls.decorator_list)


def test_s012_flags_permission_required_call_form():
    cls = _classdef(
        "@method_decorator(permission_required('app.view'), name='dispatch')\n"
        "class V(SomeMixin, LiveView):\n    pass\n"
    )
    assert _is_liveview_subclass(cls)
    assert any(_is_dispatch_auth_method_decorator(d) for d in cls.decorator_list)


def test_s012_ignores_plain_liveview():
    cls = _classdef("class PublicView(LiveView):\n    pass\n")
    assert not any(_is_dispatch_auth_method_decorator(d) for d in cls.decorator_list)


def test_s012_ignores_decorator_not_targeting_dispatch():
    cls = _classdef("@method_decorator(login_required, name='get')\nclass V(LiveView):\n    pass\n")
    assert not any(_is_dispatch_auth_method_decorator(d) for d in cls.decorator_list)


def test_s012_ignores_non_liveview_with_decorated_dispatch():
    """A plain Django View with decorated dispatch is correct — must not flag."""
    cls = _classdef(
        "@method_decorator(login_required, name='dispatch')\nclass V(View):\n    pass\n"
    )
    assert not _is_liveview_subclass(cls)


def test_s012_flags_overridden_dispatch_with_auth():
    """An overridden dispatch() doing auth is HTTP-only — must be flagged."""
    cls = _classdef(
        "class V(LiveView):\n"
        "    def dispatch(self, request, *a, **k):\n"
        "        if not request.user.is_authenticated:\n"
        "            raise PermissionDenied()\n"
        "        return super().dispatch(request, *a, **k)\n"
    )
    assert _liveview_auth_dispatch_method(cls) is not None


def test_s012_ignores_benign_overridden_dispatch():
    """A dispatch() override that does NO auth must not be flagged."""
    cls = _classdef(
        "class V(LiveView):\n"
        "    def dispatch(self, request, *a, **k):\n"
        "        request.foo = 1\n"
        "        return super().dispatch(request, *a, **k)\n"
    )
    assert _liveview_auth_dispatch_method(cls) is None
