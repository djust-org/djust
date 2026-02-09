"""Tests for djust.auth — view-level and handler-level auth enforcement."""

from unittest.mock import MagicMock
from django.core.exceptions import PermissionDenied

from djust.auth import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    check_handler_permission,
    check_view_auth,
)
from djust.decorators import event_handler, permission_required


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(authenticated=True, permissions=None):
    """Create a mock request with user auth state."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.is_authenticated = authenticated
    if permissions is None:
        permissions = []
    request.user.has_perms = lambda perms: all(p in permissions for p in perms)
    return request


def _make_anon_request():
    """Create a mock request with no user."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.is_authenticated = False
    request.user.has_perms = lambda perms: False
    return request


def _make_request_no_user():
    """Create a mock request without user attribute."""
    request = MagicMock(spec=[])
    return request


# ---------------------------------------------------------------------------
# check_view_auth tests
# ---------------------------------------------------------------------------


class TestCheckViewAuthLoginRequired:
    def test_no_auth_configured(self):
        """Views without login_required pass auth."""
        from djust.live_view import LiveView

        view = LiveView()
        request = _make_anon_request()
        assert check_view_auth(view, request) is None

    def test_login_required_authenticated(self):
        """Authenticated users pass login_required."""
        from djust.live_view import LiveView

        class SecureView(LiveView):
            login_required = True

        view = SecureView()
        request = _make_request(authenticated=True)
        assert check_view_auth(view, request) is None

    def test_login_required_anonymous(self):
        """Anonymous users get redirected."""
        from djust.live_view import LiveView

        class SecureView(LiveView):
            login_required = True

        view = SecureView()
        request = _make_anon_request()
        result = check_view_auth(view, request)
        assert result is not None
        assert "login" in result

    def test_login_required_no_user(self):
        """Requests without user attribute get redirected."""
        from djust.live_view import LiveView

        class SecureView(LiveView):
            login_required = True

        view = SecureView()
        request = _make_request_no_user()
        result = check_view_auth(view, request)
        assert result is not None

    def test_custom_login_url(self):
        """Custom login_url is returned for unauthenticated users."""
        from djust.live_view import LiveView

        class SecureView(LiveView):
            login_required = True
            login_url = "/my-login/"

        view = SecureView()
        request = _make_anon_request()
        result = check_view_auth(view, request)
        assert result == "/my-login/"

    def test_login_required_false(self):
        """Explicitly False login_required skips auth."""
        from djust.live_view import LiveView

        class PublicView(LiveView):
            login_required = False

        view = PublicView()
        request = _make_anon_request()
        assert check_view_auth(view, request) is None


class TestCheckViewAuthPermissions:
    def test_permission_required_granted(self):
        """User with required permission passes."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            login_required = True
            permission_required = "myapp.view_dashboard"

        view = AdminView()
        request = _make_request(authenticated=True, permissions=["myapp.view_dashboard"])
        assert check_view_auth(view, request) is None

    def test_permission_required_denied(self):
        """User without required permission gets redirected."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            login_required = True
            permission_required = "myapp.view_dashboard"

        view = AdminView()
        request = _make_request(authenticated=True, permissions=[])
        result = check_view_auth(view, request)
        assert result is not None

    def test_multiple_permissions_all_granted(self):
        """User with all required permissions passes."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            login_required = True
            permission_required = ["myapp.view_dashboard", "myapp.edit_dashboard"]

        view = AdminView()
        request = _make_request(
            authenticated=True,
            permissions=["myapp.view_dashboard", "myapp.edit_dashboard"],
        )
        assert check_view_auth(view, request) is None

    def test_multiple_permissions_partial(self):
        """User missing one permission gets redirected."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            login_required = True
            permission_required = ["myapp.view_dashboard", "myapp.edit_dashboard"]

        view = AdminView()
        request = _make_request(
            authenticated=True,
            permissions=["myapp.view_dashboard"],
        )
        result = check_view_auth(view, request)
        assert result is not None

    def test_permission_without_login_check(self):
        """permission_required alone checks perms (user may still be anonymous)."""
        from djust.live_view import LiveView

        class PermOnlyView(LiveView):
            permission_required = "myapp.view_public"

        view = PermOnlyView()
        request = _make_anon_request()
        # Anonymous user without perms -> redirect
        result = check_view_auth(view, request)
        assert result is not None


class TestCheckViewAuthCustomHook:
    def test_custom_check_passes(self):
        """Custom check_permissions returning True passes."""
        from djust.live_view import LiveView

        class CustomView(LiveView):
            login_required = True

            def check_permissions(self, request):
                return True

        view = CustomView()
        request = _make_request(authenticated=True)
        assert check_view_auth(view, request) is None

    def test_custom_check_returns_false(self):
        """Custom check_permissions returning False redirects."""
        from djust.live_view import LiveView

        class CustomView(LiveView):
            login_required = True

            def check_permissions(self, request):
                return False

        view = CustomView()
        request = _make_request(authenticated=True)
        result = check_view_auth(view, request)
        assert result is not None

    def test_custom_check_raises_permission_denied(self):
        """Custom check_permissions raising PermissionDenied redirects."""
        from djust.live_view import LiveView

        class CustomView(LiveView):
            login_required = True

            def check_permissions(self, request):
                raise PermissionDenied("Nope")

        view = CustomView()
        request = _make_request(authenticated=True)
        result = check_view_auth(view, request)
        assert result is not None

    def test_base_check_permissions_not_called(self):
        """LiveView base check_permissions is not invoked (only subclass overrides)."""
        from djust.live_view import LiveView

        view = LiveView()
        request = _make_request(authenticated=True)
        # Base class has no custom check — should pass cleanly
        assert check_view_auth(view, request) is None


# ---------------------------------------------------------------------------
# check_handler_permission tests
# ---------------------------------------------------------------------------


class TestCheckHandlerPermission:
    def test_no_permission_required(self):
        """Handlers without @permission_required always pass."""

        @event_handler()
        def my_handler(self, **kwargs):
            pass

        request = _make_request(authenticated=True)
        assert check_handler_permission(my_handler, request) is True

    def test_permission_granted(self):
        """Handler with matching permission passes."""

        @permission_required("myapp.delete_item")
        @event_handler()
        def delete_item(self, **kwargs):
            pass

        request = _make_request(authenticated=True, permissions=["myapp.delete_item"])
        assert check_handler_permission(delete_item, request) is True

    def test_permission_denied(self):
        """Handler with missing permission fails."""

        @permission_required("myapp.delete_item")
        @event_handler()
        def delete_item(self, **kwargs):
            pass

        request = _make_request(authenticated=True, permissions=[])
        assert check_handler_permission(delete_item, request) is False

    def test_no_user_on_request(self):
        """Request without user attribute fails."""

        @permission_required("myapp.delete_item")
        @event_handler()
        def delete_item(self, **kwargs):
            pass

        request = _make_request_no_user()
        assert check_handler_permission(delete_item, request) is False

    def test_multiple_permissions(self):
        """Handler requiring multiple permissions checks all of them."""

        @permission_required(["myapp.view_item", "myapp.delete_item"])
        @event_handler()
        def admin_action(self, **kwargs):
            pass

        request = _make_request(
            authenticated=True,
            permissions=["myapp.view_item", "myapp.delete_item"],
        )
        assert check_handler_permission(admin_action, request) is True

        request_partial = _make_request(
            authenticated=True,
            permissions=["myapp.view_item"],
        )
        assert check_handler_permission(admin_action, request_partial) is False


# ---------------------------------------------------------------------------
# Mixin tests
# ---------------------------------------------------------------------------


class TestMixins:
    def test_login_required_mixin(self):
        """LoginRequiredMixin sets login_required = True."""
        from djust.live_view import LiveView

        class MyView(LoginRequiredMixin, LiveView):
            template_name = "test.html"

        view = MyView()
        assert view.login_required is True

    def test_permission_required_mixin(self):
        """PermissionRequiredMixin sets login_required and permission_required."""
        from djust.live_view import LiveView

        class MyView(PermissionRequiredMixin, LiveView):
            template_name = "test.html"
            permission_required = "myapp.view_thing"

        view = MyView()
        assert view.login_required is True
        assert view.permission_required == "myapp.view_thing"

    def test_mixin_auth_enforcement(self):
        """Mixin-configured view enforces auth via check_view_auth."""
        from djust.live_view import LiveView

        class MyView(LoginRequiredMixin, LiveView):
            template_name = "test.html"

        view = MyView()
        # Anonymous user should be redirected
        request = _make_anon_request()
        result = check_view_auth(view, request)
        assert result is not None

        # Authenticated user should pass
        auth_request = _make_request(authenticated=True)
        assert check_view_auth(view, auth_request) is None

    def test_permission_mixin_auth_enforcement(self):
        """PermissionRequiredMixin enforces both login and permission."""
        from djust.live_view import LiveView

        class MyView(PermissionRequiredMixin, LiveView):
            template_name = "test.html"
            permission_required = "myapp.edit_item"

        view = MyView()

        # Anonymous -> redirect
        assert check_view_auth(view, _make_anon_request()) is not None

        # Authenticated without perm -> redirect
        assert check_view_auth(view, _make_request(authenticated=True, permissions=[])) is not None

        # Authenticated with perm -> pass
        assert (
            check_view_auth(
                view,
                _make_request(authenticated=True, permissions=["myapp.edit_item"]),
            )
            is None
        )
