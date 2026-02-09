"""Tests for improved error messages in websocket_utils.py (#248)."""

from unittest.mock import patch

from django.test import override_settings

from djust.decorators import event_handler
from djust.websocket_utils import (
    _check_event_security,
    _format_handler_not_found_error,
)


# ---------------------------------------------------------------------------
# Fake view classes used across tests
# ---------------------------------------------------------------------------
class FakeView:
    """View with a decorated handler and a private method."""

    @event_handler()
    def search(self, **kwargs):
        pass

    @event_handler()
    def increment(self, **kwargs):
        pass

    def undecorated(self):
        pass

    def _update_count(self):
        """Private method — should be suggested when 'update_count' is tried."""
        pass


class EmptyView:
    """View with no event handlers at all."""

    pass


# ---------------------------------------------------------------------------
# _format_handler_not_found_error
# ---------------------------------------------------------------------------
class TestFormatHandlerNotFoundError:
    """Unit tests for the helper that builds actionable 'handler not found' messages."""

    @override_settings(DEBUG=True)
    def test_typo_suggests_close_match(self):
        view = FakeView()
        msg = _format_handler_not_found_error(view, "serch")
        assert "Did you mean" in msg
        assert "search" in msg

    @override_settings(DEBUG=True)
    def test_typo_suggests_increment(self):
        view = FakeView()
        msg = _format_handler_not_found_error(view, "incremnt")
        assert "Did you mean" in msg
        assert "increment" in msg

    @override_settings(DEBUG=True)
    def test_private_method_collision(self):
        view = FakeView()
        msg = _format_handler_not_found_error(view, "update_count")
        assert "_update_count" in msg
        assert "private" in msg.lower()

    @override_settings(DEBUG=True)
    def test_lists_available_handlers(self):
        view = FakeView()
        msg = _format_handler_not_found_error(view, "nonexistent_xyz")
        assert "Available handlers on FakeView" in msg
        assert "search" in msg
        assert "increment" in msg

    @override_settings(DEBUG=True)
    def test_no_hints_returns_base_message(self):
        view = EmptyView()
        msg = _format_handler_not_found_error(view, "nonexistent_xyz")
        assert msg == "No handler found for event: nonexistent_xyz"
        # No extra hints — just the base line
        assert "\n" not in msg

    @override_settings(DEBUG=False)
    def test_production_mode_returns_base_only(self):
        view = FakeView()
        msg = _format_handler_not_found_error(view, "serch")
        assert msg == "No handler found for event: serch"
        assert "Did you mean" not in msg


# ---------------------------------------------------------------------------
# _check_event_security — improved message
# ---------------------------------------------------------------------------
class TestCheckEventSecurityMessage:
    """The strict-mode error should include class name and a code example."""

    @patch("djust.websocket_utils.djust_config")
    def test_strict_mode_includes_class_name(self, mock_config):
        mock_config.get.return_value = "strict"
        view = FakeView()
        err = _check_event_security(view.undecorated, view, "undecorated")
        assert "FakeView" in err
        assert "@event_handler" in err

    @patch("djust.websocket_utils.djust_config")
    def test_strict_mode_includes_fix_example(self, mock_config):
        mock_config.get.return_value = "strict"
        view = FakeView()
        err = _check_event_security(view.undecorated, view, "undecorated")
        assert "Fix: Add the decorator:" in err
        assert "def undecorated(self, **kwargs):" in err

    @patch("djust.websocket_utils.djust_config")
    def test_decorated_handler_passes(self, mock_config):
        mock_config.get.return_value = "strict"
        view = FakeView()
        err = _check_event_security(view.search, view, "search")
        assert err is None
