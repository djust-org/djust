"""Integration tests for auth enforcement through WebSocket consumer paths.

These tests verify that check_view_auth and check_handler_permission
are correctly wired into the mount and event handling code paths in
websocket.py and websocket_utils.py.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from djust.auth import check_view_auth
from djust.decorators import event_handler, permission_required
from djust.websocket_utils import _validate_event_security


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_user(authenticated=True, permissions=None):
    user = MagicMock()
    user.is_authenticated = authenticated
    perms = permissions or []
    user.has_perms = lambda ps: all(p in perms for p in ps)
    return user


def _mock_request(authenticated=True, permissions=None):
    request = MagicMock()
    request.user = _mock_user(authenticated, permissions)
    return request


# ---------------------------------------------------------------------------
# Mount auth integration
# ---------------------------------------------------------------------------


class TestMountAuthIntegration:
    """Test that check_view_auth correctly gates mount()."""

    def test_login_required_blocks_mount(self):
        """Login-required view returns redirect URL for anonymous users."""
        from djust.live_view import LiveView

        class ProtectedView(LiveView):
            template_name = "protected.html"
            login_required = True

        view = ProtectedView()
        request = _mock_request(authenticated=False)
        result = check_view_auth(view, request)
        assert result is not None
        # Should be a URL string
        assert isinstance(result, str)

    def test_login_required_allows_authenticated(self):
        """Login-required view returns None (pass) for authenticated users."""
        from djust.live_view import LiveView

        class ProtectedView(LiveView):
            template_name = "protected.html"
            login_required = True

        view = ProtectedView()
        request = _mock_request(authenticated=True)
        assert check_view_auth(view, request) is None

    def test_permission_required_blocks_unpermitted(self):
        """View with permission_required blocks users without that permission."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            template_name = "admin.html"
            login_required = True
            permission_required = "admin.access_dashboard"

        view = AdminView()
        request = _mock_request(authenticated=True, permissions=[])
        result = check_view_auth(view, request)
        assert result is not None

    def test_check_order_login_before_perms(self):
        """Login check happens before permission check."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            template_name = "admin.html"
            login_required = True
            permission_required = "admin.access_dashboard"

        view = AdminView()
        # Anonymous user — should be caught by login check, not perm check
        request = _mock_request(authenticated=False)
        result = check_view_auth(view, request)
        assert result is not None


# ---------------------------------------------------------------------------
# Handler permission integration
# ---------------------------------------------------------------------------


class TestHandlerPermissionIntegration:
    """Test that _validate_event_security includes permission checks."""

    @pytest.mark.asyncio
    async def test_handler_with_permission_allowed(self):
        """Handler decorated with matching permission passes validation."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"

            @permission_required("myapp.delete_item")
            @event_handler()
            def delete_item(self, item_id: int = 0, **kwargs):
                pass

        view = MyView()
        view.request = _mock_request(authenticated=True, permissions=["myapp.delete_item"])

        ws = AsyncMock()
        ws.send_error = AsyncMock()
        ws.close = AsyncMock()

        rate_limiter = MagicMock()
        rate_limiter.check_handler = MagicMock(return_value=True)

        # Patch is_safe_event_name and _check_event_security to pass
        with patch("djust.websocket_utils.is_safe_event_name", return_value=True):
            handler = await _validate_event_security(ws, "delete_item", view, rate_limiter)

        assert handler is not None
        ws.send_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_with_permission_denied(self):
        """Handler decorated with missing permission is blocked."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"

            @permission_required("myapp.delete_item")
            @event_handler()
            def delete_item(self, item_id: int = 0, **kwargs):
                pass

        view = MyView()
        view.request = _mock_request(authenticated=True, permissions=[])

        ws = AsyncMock()
        ws.send_error = AsyncMock()
        ws.close = AsyncMock()

        rate_limiter = MagicMock()
        rate_limiter.check_handler = MagicMock(return_value=True)

        with patch("djust.websocket_utils.is_safe_event_name", return_value=True):
            handler = await _validate_event_security(ws, "delete_item", view, rate_limiter)

        assert handler is None
        ws.send_error.assert_called_once_with("Permission denied")

    @pytest.mark.asyncio
    async def test_handler_without_permission_decorator_passes(self):
        """Handlers without @permission_required always pass permission check."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"

            @event_handler()
            def search(self, value: str = "", **kwargs):
                pass

        view = MyView()
        view.request = _mock_request(authenticated=True, permissions=[])

        ws = AsyncMock()
        ws.send_error = AsyncMock()
        ws.close = AsyncMock()

        rate_limiter = MagicMock()
        rate_limiter.check_handler = MagicMock(return_value=True)

        with patch("djust.websocket_utils.is_safe_event_name", return_value=True):
            handler = await _validate_event_security(ws, "search", view, rate_limiter)

        assert handler is not None
        ws.send_error.assert_not_called()
