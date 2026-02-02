"""
Tests for push_event — server-to-client event pushing.
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


class TestPushEventMixin:
    """Tests for the PushEventMixin on LiveView."""

    def _make_view(self):
        """Create a minimal view with push_event behavior (no django/channels deps)."""

        class FakeView:
            def __init__(self):
                self._pending_push_events = []

            def push_event(self, event, payload=None):
                if payload is None:
                    payload = {}
                self._pending_push_events.append((event, payload))

            def _drain_push_events(self):
                events = self._pending_push_events
                self._pending_push_events = []
                return events

        return FakeView()

    def test_push_event_queues_event(self):
        view = self._make_view()
        view.push_event("chart_update", {"data": [1, 2, 3]})
        assert len(view._pending_push_events) == 1
        assert view._pending_push_events[0] == ("chart_update", {"data": [1, 2, 3]})

    def test_push_event_default_payload(self):
        view = self._make_view()
        view.push_event("ping")
        assert view._pending_push_events[0] == ("ping", {})

    def test_push_event_multiple(self):
        view = self._make_view()
        view.push_event("a", {"x": 1})
        view.push_event("b", {"y": 2})
        view.push_event("c", {"z": 3})
        assert len(view._pending_push_events) == 3

    def test_drain_clears_queue(self):
        view = self._make_view()
        view.push_event("a", {"x": 1})
        view.push_event("b", {"y": 2})
        events = view._drain_push_events()
        assert len(events) == 2
        assert len(view._pending_push_events) == 0

    def test_drain_empty(self):
        view = self._make_view()
        events = view._drain_push_events()
        assert events == []

    def test_drain_returns_in_order(self):
        view = self._make_view()
        view.push_event("first", {})
        view.push_event("second", {})
        events = view._drain_push_events()
        assert events[0][0] == "first"
        assert events[1][0] == "second"


@pytest.mark.skipif(not HAS_CHANNELS, reason="channels not installed")
class TestPushEventToView:
    """Tests for the external push_event_to_view API."""

    def test_invalid_view_path_raises(self):
        from djust.push import push_event_to_view

        with pytest.raises(ValueError, match="Invalid view_path"):
            push_event_to_view("not valid!", "test_event")

    @patch("djust.push.get_channel_layer")
    @patch("djust.push.async_to_sync")
    def test_sends_correct_message(self, mock_async_to_sync, mock_get_channel_layer):
        from djust.push import push_event_to_view

        mock_send = MagicMock()
        mock_async_to_sync.return_value = mock_send

        push_event_to_view("myapp.views.MyView", "notification", {"msg": "hello"})

        mock_send.assert_called_once_with(
            "djust_view_myapp_views_MyView",
            {
                "type": "client_push_event",
                "event": "notification",
                "payload": {"msg": "hello"},
            },
        )

    @patch("djust.push.get_channel_layer")
    @patch("djust.push.async_to_sync")
    def test_default_payload(self, mock_async_to_sync, mock_get_channel_layer):
        from djust.push import push_event_to_view

        mock_send = MagicMock()
        mock_async_to_sync.return_value = mock_send

        push_event_to_view("myapp.views.MyView", "ping")

        call_args = mock_send.call_args[0][1]
        assert call_args["payload"] == {}


@pytest.mark.skipif(not HAS_CHANNELS, reason="channels not installed")
class TestLiveViewIntegration:
    """Test that LiveView class has push_event from the mixin."""

    def test_liveview_has_push_event(self):
        from djust.live_view import LiveView

        assert hasattr(LiveView, "push_event")

    def test_liveview_instance_can_push(self):
        from djust.live_view import LiveView

        view = LiveView()
        view.push_event("test", {"a": 1})
        assert len(view._pending_push_events) == 1


@pytest.mark.skipif(not HAS_CHANNELS, reason="channels not installed")
@pytest.mark.asyncio
class TestWebSocketFlush:
    """Test that the consumer flushes push events."""

    async def test_flush_sends_push_event_messages(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_push_events.return_value = [
            ("flash", {"message": "Saved!"}),
            ("scroll_to", {"selector": "#bottom"}),
        ]
        consumer.send_json = AsyncMock()

        await consumer._flush_push_events()

        assert consumer.send_json.call_count == 2
        consumer.send_json.assert_any_call({
            "type": "push_event",
            "event": "flash",
            "payload": {"message": "Saved!"},
        })
        consumer.send_json.assert_any_call({
            "type": "push_event",
            "event": "scroll_to",
            "payload": {"selector": "#bottom"},
        })

    async def test_flush_noop_without_view(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = None
        # Should not raise
        await consumer._flush_push_events()

    async def test_flush_noop_without_mixin(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = object()  # No _drain_push_events
        await consumer._flush_push_events()

    async def test_client_push_event_handler(self):
        """Test the channel-layer client_push_event handler."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.send_json = AsyncMock()

        await consumer.client_push_event({
            "type": "client_push_event",
            "event": "alert",
            "payload": {"level": "critical"},
        })

        consumer.send_json.assert_called_once_with({
            "type": "push_event",
            "event": "alert",
            "payload": {"level": "critical"},
        })
