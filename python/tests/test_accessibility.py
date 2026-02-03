"""
Tests for accessibility features — WCAG-compliant screen reader support,
focus management, and ARIA configuration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import channels  # noqa: F401
    HAS_CHANNELS = True
except ImportError:
    HAS_CHANNELS = False

try:
    import django  # noqa: F401
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False


class TestAccessibilityMixin:
    """Tests for the AccessibilityMixin on LiveView."""

    def _make_view(self):
        """Create a minimal view with accessibility behavior (no django/channels deps)."""

        class FakeView:
            # Configuration defaults
            aria_live_default = "polite"
            auto_focus_errors = True
            announce_loading = True

            def __init__(self):
                self._pending_announcements = []
                self._pending_focus = None
                self._focus_options = {}

            def announce(self, message, priority="polite"):
                if priority not in ("polite", "assertive"):
                    priority = "polite"
                self._pending_announcements.append((message, priority))

            def focus(self, selector, *, scroll=True, prevent_scroll=False, delay_ms=0):
                self._pending_focus = selector
                self._focus_options = {
                    "scroll": scroll,
                    "preventScroll": prevent_scroll,
                    "delayMs": delay_ms,
                }

            def focus_first_error(self):
                self._pending_focus = "__djust_first_error__"
                self._focus_options = {"scroll": True, "preventScroll": False, "delayMs": 0}

            def _drain_announcements(self):
                announcements = self._pending_announcements
                self._pending_announcements = []
                return announcements

            def _drain_focus(self):
                if self._pending_focus:
                    result = (self._pending_focus, self._focus_options)
                    self._pending_focus = None
                    self._focus_options = {}
                    return result
                return None

            def get_accessibility_config(self):
                return {
                    "ariaLiveDefault": self.aria_live_default,
                    "autoFocusErrors": self.auto_focus_errors,
                    "announceLoading": self.announce_loading,
                }

        return FakeView()

    # ========================================================================
    # Announcement Tests
    # ========================================================================

    def test_announce_queues_message(self):
        view = self._make_view()
        view.announce("Changes saved!")
        assert len(view._pending_announcements) == 1
        assert view._pending_announcements[0] == ("Changes saved!", "polite")

    def test_announce_with_assertive_priority(self):
        view = self._make_view()
        view.announce("Error: Invalid input", priority="assertive")
        assert view._pending_announcements[0] == ("Error: Invalid input", "assertive")

    def test_announce_invalid_priority_defaults_to_polite(self):
        view = self._make_view()
        view.announce("Message", priority="invalid")
        assert view._pending_announcements[0] == ("Message", "polite")

    def test_announce_multiple_messages(self):
        view = self._make_view()
        view.announce("First message")
        view.announce("Second message", priority="assertive")
        view.announce("Third message")
        assert len(view._pending_announcements) == 3

    def test_drain_announcements_clears_queue(self):
        view = self._make_view()
        view.announce("Message 1")
        view.announce("Message 2")
        announcements = view._drain_announcements()
        assert len(announcements) == 2
        assert len(view._pending_announcements) == 0

    def test_drain_announcements_empty(self):
        view = self._make_view()
        announcements = view._drain_announcements()
        assert announcements == []

    def test_drain_announcements_returns_in_order(self):
        view = self._make_view()
        view.announce("first")
        view.announce("second")
        announcements = view._drain_announcements()
        assert announcements[0][0] == "first"
        assert announcements[1][0] == "second"

    # ========================================================================
    # Focus Tests
    # ========================================================================

    def test_focus_sets_selector(self):
        view = self._make_view()
        view.focus("#my-element")
        assert view._pending_focus == "#my-element"

    def test_focus_default_options(self):
        view = self._make_view()
        view.focus("#my-element")
        assert view._focus_options["scroll"] is True
        assert view._focus_options["preventScroll"] is False
        assert view._focus_options["delayMs"] == 0

    def test_focus_with_options(self):
        view = self._make_view()
        view.focus("#my-element", scroll=False, prevent_scroll=True, delay_ms=100)
        assert view._focus_options["scroll"] is False
        assert view._focus_options["preventScroll"] is True
        assert view._focus_options["delayMs"] == 100

    def test_focus_last_wins(self):
        view = self._make_view()
        view.focus("#first")
        view.focus("#second")
        view.focus("#third")
        assert view._pending_focus == "#third"

    def test_focus_first_error(self):
        view = self._make_view()
        view.focus_first_error()
        assert view._pending_focus == "__djust_first_error__"
        assert view._focus_options["scroll"] is True

    def test_drain_focus_returns_tuple(self):
        view = self._make_view()
        view.focus("#element", scroll=True, delay_ms=50)
        result = view._drain_focus()
        assert result == ("#element", {"scroll": True, "preventScroll": False, "delayMs": 50})

    def test_drain_focus_clears_state(self):
        view = self._make_view()
        view.focus("#element")
        view._drain_focus()
        assert view._pending_focus is None
        assert view._focus_options == {}

    def test_drain_focus_returns_none_when_empty(self):
        view = self._make_view()
        result = view._drain_focus()
        assert result is None

    # ========================================================================
    # Configuration Tests
    # ========================================================================

    def test_get_accessibility_config(self):
        view = self._make_view()
        config = view.get_accessibility_config()
        assert config == {
            "ariaLiveDefault": "polite",
            "autoFocusErrors": True,
            "announceLoading": True,
        }

    def test_custom_config_values(self):
        view = self._make_view()
        view.aria_live_default = "assertive"
        view.auto_focus_errors = False
        view.announce_loading = False
        config = view.get_accessibility_config()
        assert config == {
            "ariaLiveDefault": "assertive",
            "autoFocusErrors": False,
            "announceLoading": False,
        }


@pytest.mark.skipif(not HAS_CHANNELS, reason="channels not installed")
class TestLiveViewAccessibilityIntegration:
    """Test that LiveView class has accessibility methods from the mixin."""

    def test_liveview_has_announce(self):
        from djust.live_view import LiveView
        assert hasattr(LiveView, "announce")

    def test_liveview_has_focus(self):
        from djust.live_view import LiveView
        assert hasattr(LiveView, "focus")

    def test_liveview_has_focus_first_error(self):
        from djust.live_view import LiveView
        assert hasattr(LiveView, "focus_first_error")

    def test_liveview_instance_can_announce(self):
        from djust.live_view import LiveView
        view = LiveView()
        view.announce("test message", priority="polite")
        assert len(view._pending_announcements) == 1

    def test_liveview_instance_can_focus(self):
        from djust.live_view import LiveView
        view = LiveView()
        view.focus("#test")
        assert view._pending_focus == "#test"


@pytest.mark.skipif(not HAS_CHANNELS, reason="channels not installed")
@pytest.mark.asyncio
class TestWebSocketAccessibilityFlush:
    """Test that the consumer flushes accessibility commands."""

    async def test_flush_sends_announcements(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_announcements.return_value = [
            ("Changes saved!", "polite"),
            ("Error occurred", "assertive"),
        ]
        consumer.view_instance._drain_focus.return_value = None
        consumer.send_json = AsyncMock()

        await consumer._flush_accessibility()

        consumer.send_json.assert_called_once_with({
            "type": "accessibility",
            "announcements": [
                ("Changes saved!", "polite"),
                ("Error occurred", "assertive"),
            ],
        })

    async def test_flush_sends_focus_command(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_announcements.return_value = []
        consumer.view_instance._drain_focus.return_value = (
            "#error-message",
            {"scroll": True, "preventScroll": False, "delayMs": 0}
        )
        consumer.send_json = AsyncMock()

        await consumer._flush_accessibility()

        consumer.send_json.assert_called_once_with({
            "type": "focus",
            "selector": "#error-message",
            "options": {"scroll": True, "preventScroll": False, "delayMs": 0},
        })

    async def test_flush_sends_both_announcements_and_focus(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_announcements.return_value = [
            ("Form submitted", "polite"),
        ]
        consumer.view_instance._drain_focus.return_value = (
            "#success-message",
            {"scroll": True, "preventScroll": False, "delayMs": 0}
        )
        consumer.send_json = AsyncMock()

        await consumer._flush_accessibility()

        assert consumer.send_json.call_count == 2
        consumer.send_json.assert_any_call({
            "type": "accessibility",
            "announcements": [("Form submitted", "polite")],
        })
        consumer.send_json.assert_any_call({
            "type": "focus",
            "selector": "#success-message",
            "options": {"scroll": True, "preventScroll": False, "delayMs": 0},
        })

    async def test_flush_noop_without_view(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = None
        # Should not raise
        await consumer._flush_accessibility()

    async def test_flush_noop_without_mixin(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = object()  # No _drain_announcements
        await consumer._flush_accessibility()

    async def test_flush_skips_empty_announcements(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_announcements.return_value = []
        consumer.view_instance._drain_focus.return_value = None
        consumer.send_json = AsyncMock()

        await consumer._flush_accessibility()

        # Should not send anything when both are empty
        consumer.send_json.assert_not_called()


class TestAccessibilityMixinEdgeCases:
    """Edge case tests for accessibility mixin."""

    def _make_view(self):
        """Create a minimal view with accessibility behavior."""

        class FakeView:
            aria_live_default = "polite"
            auto_focus_errors = True
            announce_loading = True

            def __init__(self):
                self._pending_announcements = []
                self._pending_focus = None
                self._focus_options = {}

            def announce(self, message, priority="polite"):
                if priority not in ("polite", "assertive"):
                    priority = "polite"
                self._pending_announcements.append((message, priority))

            def focus(self, selector, *, scroll=True, prevent_scroll=False, delay_ms=0):
                self._pending_focus = selector
                self._focus_options = {
                    "scroll": scroll,
                    "preventScroll": prevent_scroll,
                    "delayMs": delay_ms,
                }

            def _drain_announcements(self):
                announcements = self._pending_announcements
                self._pending_announcements = []
                return announcements

            def _drain_focus(self):
                if self._pending_focus:
                    result = (self._pending_focus, self._focus_options)
                    self._pending_focus = None
                    self._focus_options = {}
                    return result
                return None

        return FakeView()

    def test_announce_empty_message(self):
        """Empty message should still be queued."""
        view = self._make_view()
        view.announce("")
        assert len(view._pending_announcements) == 1
        assert view._pending_announcements[0] == ("", "polite")

    def test_announce_unicode_message(self):
        """Unicode messages should work."""
        view = self._make_view()
        view.announce("保存しました ✓")
        assert view._pending_announcements[0] == ("保存しました ✓", "polite")

    def test_focus_complex_selector(self):
        """Complex CSS selectors should work."""
        view = self._make_view()
        view.focus('div.container > form input[type="text"]:first-child')
        assert view._pending_focus == 'div.container > form input[type="text"]:first-child'

    def test_multiple_drains_return_different_results(self):
        """Draining should return different results each time."""
        view = self._make_view()
        view.announce("first")
        first_drain = view._drain_announcements()

        view.announce("second")
        second_drain = view._drain_announcements()

        assert first_drain == [("first", "polite")]
        assert second_drain == [("second", "polite")]
