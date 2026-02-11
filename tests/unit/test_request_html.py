"""Tests for the request_html → html_recovery WebSocket protocol (#259).

When VDOM patches fail client-side (e.g., {% if %} blocks shifting DOM),
the client sends {"type": "request_html"}. The server responds with the
last rendered HTML for client-side DOM morphing.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from djust.websocket import LiveViewConsumer


def _make_consumer():
    """Create a LiveViewConsumer with mocked internals."""
    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    consumer.view_instance = None
    return consumer


class TestHandleRequestHtml:
    """Test handle_request_html method on LiveViewConsumer."""

    @pytest.mark.asyncio
    async def test_error_when_view_not_mounted(self):
        consumer = _make_consumer()
        consumer.view_instance = None

        await consumer.handle_request_html({})

        consumer.send_error.assert_called_once_with("View not mounted")
        consumer.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_when_no_recovery_html(self):
        consumer = _make_consumer()
        consumer.view_instance = MagicMock()
        # No _recovery_html set

        await consumer.handle_request_html({})

        consumer.send_error.assert_called_once_with("No recovery HTML available")
        consumer.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_html_recovery_response(self):
        consumer = _make_consumer()

        view = MagicMock()
        view._strip_comments_and_whitespace = MagicMock(return_value="<div>stripped</div>")
        view._extract_liveview_content = MagicMock(return_value="stripped")
        consumer.view_instance = view

        consumer._recovery_html = "<div>raw html</div>"
        consumer._recovery_version = 7

        await consumer.handle_request_html({})

        consumer.send_json.assert_called_once()
        response = consumer.send_json.call_args[0][0]
        assert response["type"] == "html_recovery"
        assert response["html"] == "stripped"
        assert response["version"] == 7

    @pytest.mark.asyncio
    async def test_clears_recovery_state_after_use(self):
        consumer = _make_consumer()

        view = MagicMock()
        view._strip_comments_and_whitespace = MagicMock(return_value="<div>x</div>")
        view._extract_liveview_content = MagicMock(return_value="x")
        consumer.view_instance = view

        consumer._recovery_html = "<div>html</div>"
        consumer._recovery_version = 3

        await consumer.handle_request_html({})

        # Recovery state cleared (one-time use)
        assert consumer._recovery_html is None

    @pytest.mark.asyncio
    async def test_second_request_fails_after_consumed(self):
        consumer = _make_consumer()

        view = MagicMock()
        view._strip_comments_and_whitespace = MagicMock(return_value="<div>x</div>")
        view._extract_liveview_content = MagicMock(return_value="x")
        consumer.view_instance = view

        consumer._recovery_html = "<div>html</div>"
        consumer._recovery_version = 5

        # First request succeeds
        await consumer.handle_request_html({})
        assert consumer.send_json.call_count == 1

        # Second request fails — recovery HTML was consumed
        consumer.send_json.reset_mock()
        consumer.send_error.reset_mock()
        await consumer.handle_request_html({})

        consumer.send_error.assert_called_once_with("No recovery HTML available")
        consumer.send_json.assert_not_called()
