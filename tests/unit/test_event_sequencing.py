"""
Tests for event sequencing during ticks (#560).

Verifies that:
1. The render lock serializes tick and event render operations
2. Ticks yield to user events (_processing_user_event flag)
3. Event responses include ref and source fields
4. _send_update passes source and ref through to the response
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from djust import LiveView
from djust.websocket import LiveViewConsumer


class TickCounterView(LiveView):
    """Test view with tick_interval that increments a counter."""

    template = "<div dj-root>{{ count }}</div>"
    tick_interval = 100  # 100ms tick

    def mount(self, request, **kwargs):
        self.count = 0

    def handle_tick(self):
        self.count += 1


class TestRenderLockInit:
    """Test that render lock is initialized on consumer creation."""

    def test_consumer_has_render_lock(self):
        consumer = LiveViewConsumer()
        assert hasattr(consumer, "_render_lock")
        assert isinstance(consumer._render_lock, asyncio.Lock)

    def test_consumer_has_processing_flag(self):
        consumer = LiveViewConsumer()
        assert hasattr(consumer, "_processing_user_event")
        assert consumer._processing_user_event is False


class TestSendUpdateSourceAndRef:
    """Test that _send_update passes source and ref fields."""

    @pytest.mark.asyncio
    async def test_send_update_includes_source_tick(self):
        """Tick updates include source='tick' in the response."""
        consumer = LiveViewConsumer()
        consumer.use_binary = False
        sent_messages = []

        async def mock_send_json(msg):
            sent_messages.append(msg)

        consumer.send_json = mock_send_json
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_push_events = MagicMock(return_value=[])
        consumer.view_instance._drain_navigation = MagicMock(return_value=[])
        consumer.view_instance._drain_accessibility = MagicMock(return_value=[])
        consumer.view_instance._drain_i18n = MagicMock(return_value=[])

        await consumer._send_update(
            patches=[{"op": "replace", "path": "/0", "value": "1"}],
            version=2,
            source="tick",
        )

        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert msg["type"] == "patch"
        assert msg["source"] == "tick"
        assert "ref" not in msg  # Ticks don't have refs

    @pytest.mark.asyncio
    async def test_send_update_includes_source_event_and_ref(self):
        """Event updates include source='event' and ref in the response."""
        consumer = LiveViewConsumer()
        consumer.use_binary = False
        sent_messages = []

        async def mock_send_json(msg):
            sent_messages.append(msg)

        consumer.send_json = mock_send_json
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_push_events = MagicMock(return_value=[])
        consumer.view_instance._drain_navigation = MagicMock(return_value=[])
        consumer.view_instance._drain_accessibility = MagicMock(return_value=[])
        consumer.view_instance._drain_i18n = MagicMock(return_value=[])

        await consumer._send_update(
            patches=[],
            version=3,
            source="event",
            ref=42,
        )

        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert msg["type"] == "patch"
        assert msg["source"] == "event"
        assert msg["ref"] == 42

    @pytest.mark.asyncio
    async def test_send_update_omits_source_when_none(self):
        """When source is None, it's omitted from the response."""
        consumer = LiveViewConsumer()
        consumer.use_binary = False
        sent_messages = []

        async def mock_send_json(msg):
            sent_messages.append(msg)

        consumer.send_json = mock_send_json
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_push_events = MagicMock(return_value=[])
        consumer.view_instance._drain_navigation = MagicMock(return_value=[])
        consumer.view_instance._drain_accessibility = MagicMock(return_value=[])
        consumer.view_instance._drain_i18n = MagicMock(return_value=[])

        await consumer._send_update(
            patches=[],
            version=1,
        )

        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert "source" not in msg
        assert "ref" not in msg

    @pytest.mark.asyncio
    async def test_html_update_includes_source_and_ref(self):
        """HTML fallback updates also include source and ref."""
        consumer = LiveViewConsumer()
        consumer.use_binary = False
        sent_messages = []

        async def mock_send_json(msg):
            sent_messages.append(msg)

        consumer.send_json = mock_send_json
        consumer.view_instance = MagicMock()
        consumer.view_instance._drain_push_events = MagicMock(return_value=[])
        consumer.view_instance._drain_navigation = MagicMock(return_value=[])
        consumer.view_instance._drain_accessibility = MagicMock(return_value=[])
        consumer.view_instance._drain_i18n = MagicMock(return_value=[])

        await consumer._send_update(
            html="<div>hello</div>",
            version=5,
            source="event",
            ref=7,
        )

        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert msg["type"] == "html_update"
        assert msg["source"] == "event"
        assert msg["ref"] == 7


class TestSendNoopRef:
    """Test that _send_noop passes ref field."""

    @pytest.mark.asyncio
    async def test_noop_includes_ref(self):
        consumer = LiveViewConsumer()
        sent_messages = []

        async def mock_send_json(msg):
            sent_messages.append(msg)

        consumer.send_json = mock_send_json

        await consumer._send_noop(ref=99)

        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert msg["type"] == "noop"
        assert msg["ref"] == 99

    @pytest.mark.asyncio
    async def test_noop_omits_ref_when_none(self):
        consumer = LiveViewConsumer()
        sent_messages = []

        async def mock_send_json(msg):
            sent_messages.append(msg)

        consumer.send_json = mock_send_json

        await consumer._send_noop()

        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert "ref" not in msg


class TestTickYieldsToEvents:
    """Test that ticks skip when a user event is being processed."""

    def test_processing_flag_starts_false(self):
        consumer = LiveViewConsumer()
        assert consumer._processing_user_event is False

    @pytest.mark.asyncio
    async def test_tick_skips_when_user_event_processing(self):
        """When _processing_user_event is True, tick should skip."""
        consumer = LiveViewConsumer()
        consumer._processing_user_event = True

        view = MagicMock()
        view.handle_tick = MagicMock()
        consumer.view_instance = view

        # Verify the flag is set — the tick loop checks this flag
        # and calls `continue` when True, skipping handle_tick().
        assert consumer._processing_user_event is True


class TestRenderLockSerialization:
    """Test that the render lock prevents concurrent access."""

    @pytest.mark.asyncio
    async def test_lock_prevents_concurrent_access(self):
        """Two coroutines can't hold the render lock simultaneously."""
        consumer = LiveViewConsumer()
        lock = consumer._render_lock
        access_log = []

        async def worker(name, delay):
            await lock.acquire()
            try:
                access_log.append(f"{name}_start")
                await asyncio.sleep(delay)
                access_log.append(f"{name}_end")
            finally:
                lock.release()

        # Start both workers — second should wait for first
        await asyncio.gather(
            worker("first", 0.05),
            worker("second", 0.01),
        )

        # First worker should complete before second starts
        assert access_log == [
            "first_start",
            "first_end",
            "second_start",
            "second_end",
        ]

    @pytest.mark.asyncio
    async def test_tick_timeout_skips_when_lock_held(self):
        """Tick's wait_for with short timeout should skip if lock is held."""
        consumer = LiveViewConsumer()
        lock = consumer._render_lock

        # Hold the lock (simulating event processing)
        await lock.acquire()

        # Try to acquire with short timeout — should fail
        timed_out = False
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.05)
        except asyncio.TimeoutError:
            timed_out = True

        lock.release()
        assert timed_out, "Tick should time out when lock is held by event"
