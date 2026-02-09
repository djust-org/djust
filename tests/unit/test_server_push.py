"""Tests for server-push API (#230)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from djust.push import push_to_view, apush_to_view


# ---------------------------------------------------------------------------
# push_to_view / apush_to_view
# ---------------------------------------------------------------------------


class TestPushToView:
    """Test the public push_to_view API."""

    @patch("djust.push.get_channel_layer")
    def test_push_state(self, mock_get_layer):
        layer = MagicMock()
        layer.group_send = AsyncMock()
        mock_get_layer.return_value = layer

        push_to_view("myapp.views.DashboardView", state={"count": 42})

        layer.group_send.assert_called_once_with(
            "djust_view_myapp_views_DashboardView",
            {
                "type": "server_push",
                "state": {"count": 42},
                "handler": None,
                "payload": None,
            },
        )

    @patch("djust.push.get_channel_layer")
    def test_push_handler(self, mock_get_layer):
        layer = MagicMock()
        layer.group_send = AsyncMock()
        mock_get_layer.return_value = layer

        push_to_view(
            "myapp.views.ChatView",
            handler="on_new_message",
            payload={"text": "hi"},
        )

        layer.group_send.assert_called_once()
        msg = layer.group_send.call_args[0][1]
        assert msg["handler"] == "on_new_message"
        assert msg["payload"] == {"text": "hi"}

    def test_push_invalid_view_path(self):
        with pytest.raises(ValueError, match="Invalid view_path"):
            push_to_view("", state={"x": 1})

    def test_push_invalid_view_path_no_dots(self):
        with pytest.raises(ValueError, match="Invalid view_path"):
            push_to_view("NoDots", state={"x": 1})

    @pytest.mark.asyncio
    async def test_apush_invalid_view_path(self):
        with pytest.raises(ValueError, match="Invalid view_path"):
            await apush_to_view("bad path!", state={"x": 1})

    @pytest.mark.asyncio
    @patch("djust.push.get_channel_layer")
    async def test_apush_to_view(self, mock_get_layer):
        layer = MagicMock()
        layer.group_send = AsyncMock()
        mock_get_layer.return_value = layer

        await apush_to_view("app.views.V", state={"x": 1})

        layer.group_send.assert_awaited_once_with(
            "djust_view_app_views_V",
            {
                "type": "server_push",
                "state": {"x": 1},
                "handler": None,
                "payload": None,
            },
        )


# ---------------------------------------------------------------------------
# Consumer: server_push handler
# ---------------------------------------------------------------------------


class TestServerPushHandler:
    """Test LiveViewConsumer.server_push channel handler."""

    def _make_consumer(self):
        """Create a minimal mock consumer with the server_push method."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = MagicMock()
        consumer.view_instance._skip_render = False
        consumer.view_instance._sync_state_to_rust = MagicMock()
        consumer.view_instance.render_with_diff = MagicMock(
            return_value=("<div>ok</div>", '[{"op":"replace"}]', 2)
        )
        consumer.use_binary = False
        consumer.send = AsyncMock()
        consumer._send_update = AsyncMock()
        return consumer

    @pytest.mark.asyncio
    async def test_applies_state(self):
        consumer = self._make_consumer()
        event = {"state": {"count": 10, "label": "hi"}, "handler": None, "payload": None}

        await consumer.server_push(event)

        assert consumer.view_instance.count == 10
        assert consumer.view_instance.label == "hi"
        consumer._send_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_handle_prefixed_handler(self):
        consumer = self._make_consumer()
        consumer.view_instance.handle_msg = MagicMock()
        event = {"state": None, "handler": "handle_msg", "payload": {"text": "yo"}}

        await consumer.server_push(event)

        consumer.view_instance.handle_msg.assert_called_once_with(text="yo")

    @pytest.mark.asyncio
    async def test_calls_event_handler_decorated(self):
        from djust.decorators import event_handler

        consumer = self._make_consumer()
        call_log = []

        @event_handler
        def on_msg(text):
            call_log.append(text)

        consumer.view_instance.on_msg = on_msg
        event = {"state": None, "handler": "on_msg", "payload": {"text": "yo"}}

        await consumer.server_push(event)

        assert call_log == ["yo"]
        consumer._send_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_arbitrary_handler(self):
        consumer = self._make_consumer()
        # Use a plain function (no _djust_decorators) to avoid MagicMock auto-attr
        call_log = []

        def dangerous_method():
            call_log.append("called")

        consumer.view_instance.dangerous_method = dangerous_method
        event = {"state": None, "handler": "dangerous_method", "payload": {}}

        await consumer.server_push(event)

        # Should NOT have been called — not handle_* and not @event_handler
        assert call_log == []
        # Render still happens (state may have changed)
        consumer._send_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_view_instance(self):
        consumer = self._make_consumer()
        consumer.view_instance = None

        # Should not raise
        await consumer.server_push({"state": {"x": 1}})


# ---------------------------------------------------------------------------
# Group join / leave
# ---------------------------------------------------------------------------


class TestGroupJoinLeave:
    """Test that mount joins and disconnect leaves the view group."""

    @pytest.mark.asyncio
    async def test_disconnect_leaves_group(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer._view_group = "djust_view_app_views_MyView"
        consumer._tick_task = None
        consumer._client_ip = None
        consumer.view_instance = None
        consumer.actor_handle = None
        consumer.use_actors = False
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock()

        await consumer.disconnect(1000)

        consumer.channel_layer.group_discard.assert_any_call(
            "djust_view_app_views_MyView", "test-channel"
        )

    @pytest.mark.asyncio
    async def test_disconnect_cancels_tick(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer._view_group = None
        consumer._client_ip = None
        consumer.view_instance = None
        consumer.actor_handle = None
        consumer.use_actors = False
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock()

        # Create a real cancelled future so `await self._tick_task` works
        fut = asyncio.get_event_loop().create_future()
        fut.cancel()
        consumer._tick_task = fut

        await consumer.disconnect(1000)

        assert consumer._tick_task is None


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------


class TestTick:
    """Test the _run_tick loop."""

    @pytest.mark.asyncio
    async def test_tick_calls_handle_tick(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer()
        consumer.view_instance = MagicMock()
        consumer.view_instance.handle_tick = MagicMock()
        consumer.view_instance._sync_state_to_rust = MagicMock()
        consumer.view_instance.render_with_diff = MagicMock(return_value=("<div/>", "[]", 1))
        consumer._send_update = AsyncMock()
        consumer.use_binary = False

        # Run tick once then cancel
        call_count = 0
        original_sleep = asyncio.sleep

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError()
            await original_sleep(0)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await consumer._run_tick(100)

        consumer.view_instance.handle_tick.assert_called_once()
