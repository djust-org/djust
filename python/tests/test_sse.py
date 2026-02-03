"""
Tests for SSE (Server-Sent Events) module.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from django.http import HttpRequest, StreamingHttpResponse
from django.test import RequestFactory

# Import SSE components
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "djust"))
from djust.sse import SSEView, SSEMixin, sse_event, sse_comment


# ============================================================================
# Unit Tests: sse_event and sse_comment helpers
# ============================================================================


class TestSSEEventFormatting:
    """Test SSE event string formatting."""

    def test_simple_data_event(self):
        """Test basic data-only event."""
        result = sse_event("hello")
        assert result == 'data: hello\n\n'

    def test_json_data_event(self):
        """Test JSON data encoding."""
        result = sse_event({"count": 42, "name": "test"})
        data = json.loads(result.split("data: ")[1].split("\n")[0])
        assert data == {"count": 42, "name": "test"}

    def test_event_with_type(self):
        """Test event with event type."""
        result = sse_event("test data", event="update")
        assert "event: update\n" in result
        assert "data: test data\n" in result

    def test_event_with_id(self):
        """Test event with ID for Last-Event-ID tracking."""
        result = sse_event("test", id="123")
        assert "id: 123\n" in result
        assert "data: test\n" in result

    def test_event_with_retry(self):
        """Test event with retry interval."""
        result = sse_event("test", retry=5000)
        assert "retry: 5000\n" in result

    def test_event_with_all_fields(self):
        """Test event with all fields."""
        result = sse_event(
            {"msg": "hello"},
            event="notification",
            id="evt-42",
            retry=3000
        )
        assert "id: evt-42\n" in result
        assert "event: notification\n" in result
        assert "retry: 3000\n" in result
        assert "data: " in result
        # Verify JSON data
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data == {"msg": "hello"}

    def test_multiline_data(self):
        """Test multiline data handling."""
        result = sse_event("line1\nline2\nline3")
        assert "data: line1\n" in result
        assert "data: line2\n" in result
        assert "data: line3\n" in result

    def test_event_ends_with_double_newline(self):
        """Verify events end with double newline."""
        result = sse_event("test")
        assert result.endswith("\n\n")


class TestSSEComment:
    """Test SSE comment formatting."""

    def test_simple_comment(self):
        """Test basic comment."""
        result = sse_comment("keepalive")
        assert result == ": keepalive\n\n"

    def test_multiline_comment(self):
        """Test multiline comment."""
        result = sse_comment("line1\nline2")
        assert ": line1\n" in result
        assert ": line2\n" in result


# ============================================================================
# Unit Tests: SSEMixin
# ============================================================================


class TestSSEMixin:
    """Test SSEMixin functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        class TestView(SSEMixin):
            pass
        self.view = TestView()

    def test_event_helper(self):
        """Test event() helper method."""
        result = self.view.event("update", {"value": 1})
        assert "event: update\n" in result
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data == {"value": 1}

    def test_data_event_helper(self):
        """Test data_event() helper method."""
        result = self.view.data_event({"test": True})
        assert "event:" not in result
        assert "data: " in result

    def test_keepalive_helper(self):
        """Test keepalive() helper method."""
        result = self.view.keepalive()
        assert result.startswith(":")
        assert "keepalive" in result

    def test_get_last_event_id_from_header(self):
        """Test getting Last-Event-ID from header."""
        factory = RequestFactory()
        request = factory.get("/", HTTP_LAST_EVENT_ID="evt-123")
        result = self.view.get_last_event_id(request)
        assert result == "evt-123"

    def test_get_last_event_id_from_query(self):
        """Test getting Last-Event-ID from query parameter."""
        factory = RequestFactory()
        request = factory.get("/?lastEventId=evt-456")
        result = self.view.get_last_event_id(request)
        assert result == "evt-456"

    def test_get_last_event_id_header_priority(self):
        """Test header takes priority over query parameter."""
        factory = RequestFactory()
        request = factory.get("/?lastEventId=query-id", HTTP_LAST_EVENT_ID="header-id")
        result = self.view.get_last_event_id(request)
        assert result == "header-id"

    def test_get_last_event_id_none(self):
        """Test when no Last-Event-ID is present."""
        factory = RequestFactory()
        request = factory.get("/")
        result = self.view.get_last_event_id(request)
        assert result is None


# ============================================================================
# Unit Tests: SSEView
# ============================================================================


class TestSSEView:
    """Test SSEView functionality."""

    def test_auto_id_generation(self):
        """Test automatic event ID generation."""
        view = SSEView()
        view.auto_id = True

        event1 = view.event("test", {"n": 1})
        event2 = view.event("test", {"n": 2})
        event3 = view.event("test", {"n": 3})

        assert "id: 1\n" in event1
        assert "id: 2\n" in event2
        assert "id: 3\n" in event3

    def test_manual_id_overrides_auto(self):
        """Test manual ID overrides auto-generation."""
        view = SSEView()
        view.auto_id = True

        event = view.event("test", {"n": 1}, id="custom-id")
        assert "id: custom-id\n" in event

    def test_push_patch_helper(self):
        """Test push_patch() helper."""
        view = SSEView()
        patches = [{"op": "replace", "path": "#count", "value": "42"}]
        result = view.push_patch(patches)

        assert "event: patch\n" in result
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data["patches"] == patches

    def test_push_html_helper(self):
        """Test push_html() helper."""
        view = SSEView()
        result = view.push_html("#content", "<p>Hello</p>", mode="append")

        assert "event: html\n" in result
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data["selector"] == "#content"
        assert data["html"] == "<p>Hello</p>"
        assert data["mode"] == "append"

    def test_push_text_helper(self):
        """Test push_text() helper."""
        view = SSEView()
        result = view.push_text("#output", "Progress: 50%")

        assert "event: text\n" in result
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data["selector"] == "#output"
        assert data["text"] == "Progress: 50%"

    def test_push_redirect_helper(self):
        """Test push_redirect() helper."""
        view = SSEView()
        result = view.push_redirect("/dashboard/")

        assert "event: redirect\n" in result
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data["url"] == "/dashboard/"

    def test_push_reload_helper(self):
        """Test push_reload() helper."""
        view = SSEView()
        result = view.push_reload()

        assert "event: reload\n" in result


# ============================================================================
# Integration Tests: SSE Streaming
# ============================================================================


class TestSSEStreaming:
    """Test SSE streaming functionality."""

    @pytest.mark.asyncio
    async def test_basic_stream(self):
        """Test basic SSE streaming."""
        class CounterSSE(SSEView):
            async def stream(self, request):
                for i in range(3):
                    yield self.event("count", {"value": i})

        view = CounterSSE()
        factory = RequestFactory()
        request = factory.get("/")

        # Get the response
        response = await view.get(request)

        assert isinstance(response, StreamingHttpResponse)
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache, no-store, must-revalidate"

    @pytest.mark.asyncio
    async def test_mount_called(self):
        """Test that mount() is called before streaming."""
        mount_called = {"value": False, "last_id": None}

        class TestSSE(SSEView):
            async def mount(self, request, last_event_id, *args, **kwargs):
                mount_called["value"] = True
                mount_called["last_id"] = last_event_id

            async def stream(self, request):
                yield self.event("test", {})

        view = TestSSE()
        factory = RequestFactory()
        request = factory.get("/", HTTP_LAST_EVENT_ID="evt-100")

        await view.get(request)

        assert mount_called["value"] is True
        assert mount_called["last_id"] == "evt-100"

    @pytest.mark.asyncio
    async def test_stream_yields_events(self):
        """Test that stream() yields properly formatted events."""
        events_yielded = []

        class TestSSE(SSEView):
            auto_id = False  # Disable auto-ID for predictable output

            async def stream(self, request):
                yield self.event("start", {"status": "begin"})
                yield self.event("progress", {"percent": 50})
                yield self.event("complete", {"status": "done"})

        view = TestSSE()
        factory = RequestFactory()
        request = factory.get("/")

        response = await view.get(request)

        # Collect streamed content
        content = b""
        async for chunk in response.streaming_content:
            content += chunk

        content_str = content.decode("utf-8")

        # Should have connect event first
        assert "event: connect" in content_str

        # Should have our events
        assert "event: start" in content_str
        assert "event: progress" in content_str
        assert "event: complete" in content_str


# ============================================================================
# Integration Tests: Reconnection Support
# ============================================================================


class TestSSEReconnection:
    """Test SSE reconnection and Last-Event-ID support."""

    @pytest.mark.asyncio
    async def test_last_event_id_passed_to_stream(self):
        """Test that Last-Event-ID is available in stream."""
        received_request = {"obj": None}

        class TestSSE(SSEView):
            async def stream(self, request):
                received_request["obj"] = request
                last_id = self.get_last_event_id(request)
                yield self.event("resumed", {"from_id": last_id})

        view = TestSSE()
        factory = RequestFactory()
        request = factory.get("/", HTTP_LAST_EVENT_ID="evt-999")

        response = await view.get(request)

        # Consume the response
        async for _ in response.streaming_content:
            pass

        # Verify request was passed correctly
        assert received_request["obj"] is not None

    @pytest.mark.asyncio
    async def test_event_ids_for_resumption(self):
        """Test that events include IDs for resumption."""
        class TestSSE(SSEView):
            auto_id = True

            async def stream(self, request):
                yield self.event("msg", {"text": "hello"})
                yield self.event("msg", {"text": "world"})

        view = TestSSE()
        factory = RequestFactory()
        request = factory.get("/")

        response = await view.get(request)

        content = b""
        async for chunk in response.streaming_content:
            content += chunk

        content_str = content.decode("utf-8")

        # Events should have incrementing IDs
        assert "id: 1\n" in content_str or "id: 2\n" in content_str


# ============================================================================
# Edge Cases
# ============================================================================


class TestSSEEdgeCases:
    """Test edge cases and error handling."""

    def test_special_characters_in_data(self):
        """Test handling of special characters in data."""
        # Newlines in JSON
        result = sse_event({"text": "line1\nline2"})
        assert "data: " in result

        # Unicode
        result = sse_event({"emoji": "🎉", "text": "日本語"})
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data["emoji"] == "🎉"
        assert data["text"] == "日本語"

    def test_empty_data(self):
        """Test empty data handling."""
        result = sse_event("")
        assert result == "data: \n\n"

        result = sse_event({})
        assert "data: {}" in result

    def test_none_optional_fields(self):
        """Test that None optional fields are omitted."""
        result = sse_event("test", event=None, id=None, retry=None)
        assert "event:" not in result
        assert "id:" not in result
        assert "retry:" not in result

    def test_large_data(self):
        """Test handling of large data."""
        large_text = "x" * 100000
        result = sse_event({"large": large_text})
        assert len(result) > 100000

    @pytest.mark.asyncio
    async def test_cors_headers(self):
        """Test CORS headers when enabled."""
        class CORSEnabledSSE(SSEView):
            sse_cors = True

            async def stream(self, request):
                yield self.event("test", {})

        view = CORSEnabledSSE()
        factory = RequestFactory()
        request = factory.get("/")

        response = await view.get(request)

        assert response["Access-Control-Allow-Origin"] == "*"
        assert response["Access-Control-Allow-Credentials"] == "true"


# ============================================================================
# Configuration Tests
# ============================================================================


class TestSSEConfiguration:
    """Test SSE configuration options."""

    def test_default_retry_interval(self):
        """Test default retry interval."""
        view = SSEView()
        assert view.sse_retry_ms == 3000

    def test_default_keepalive_interval(self):
        """Test default keepalive interval."""
        view = SSEView()
        assert view.sse_keepalive_interval == 15

    def test_custom_retry_interval(self):
        """Test custom retry interval."""
        class CustomSSE(SSEView):
            sse_retry_ms = 10000

        view = CustomSSE()
        assert view.sse_retry_ms == 10000

    def test_auto_id_default(self):
        """Test auto_id default setting."""
        view = SSEView()
        assert view.auto_id is True

    def test_auto_id_disabled(self):
        """Test auto_id can be disabled."""
        class NoAutoIdSSE(SSEView):
            auto_id = False

        view = NoAutoIdSSE()
        event = view.event("test", {})
        assert "id:" not in event
