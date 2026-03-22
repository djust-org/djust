"""
Tests for enhanced error messages in WebSocket consumer.

Verifies that ``send_error`` includes debug context (traceback, hint,
debug_detail) when ``settings.DEBUG=True`` and omits it in production.
Also tests the did-you-mean handler suggestions in ``_format_handler_not_found_error``.
"""

from unittest.mock import MagicMock
from django.test import SimpleTestCase, override_settings

from djust.websocket_utils import _format_handler_not_found_error, _safe_error


class TestSafeError(SimpleTestCase):
    """Verify _safe_error returns detail in DEBUG, generic otherwise."""

    @override_settings(DEBUG=True)
    def test_debug_returns_detail(self):
        result = _safe_error("detailed info", "generic")
        self.assertEqual(result, "detailed info")

    @override_settings(DEBUG=False)
    def test_production_returns_generic(self):
        result = _safe_error("detailed info", "generic")
        self.assertEqual(result, "generic")


class TestFormatHandlerNotFoundError(SimpleTestCase):
    """Test the did-you-mean suggestions for handler lookup failures."""

    def _make_view(self, handler_names):
        """Create a mock view instance with given handler names."""
        from djust.decorators import event_handler

        attrs = {}
        for name in handler_names:

            @event_handler()
            def _handler(self, **kwargs):
                pass

            _handler.__name__ = name
            attrs[name] = _handler

        ViewClass = type("TestView", (), attrs)
        return ViewClass()

    @override_settings(DEBUG=True)
    def test_typo_suggestion(self):
        view = self._make_view(["increment", "decrement", "reset"])
        msg = _format_handler_not_found_error(view, "incremnt")
        self.assertIn("Did you mean", msg)
        self.assertIn("increment", msg)

    @override_settings(DEBUG=True)
    def test_lists_available_handlers(self):
        view = self._make_view(["search", "filter_results", "sort"])
        msg = _format_handler_not_found_error(view, "nonexistent_xyz")
        self.assertIn("Available handlers", msg)
        self.assertIn("search", msg)

    @override_settings(DEBUG=False)
    def test_production_no_hints(self):
        view = self._make_view(["increment"])
        msg = _format_handler_not_found_error(view, "incremnt")
        self.assertEqual(msg, "No handler found for event: incremnt")


class TestSendErrorDebugContext(SimpleTestCase):
    """Test that send_error includes debug fields when DEBUG=True."""

    async def _call_send_error(self, error, is_debug, **context):
        """Create a mock consumer and call send_error."""
        from unittest.mock import AsyncMock

        from djust.websocket import LiveViewConsumer

        consumer = MagicMock(spec=LiveViewConsumer)
        consumer.send_json = AsyncMock()
        consumer.view_instance = None

        # Call the real send_error with appropriate settings
        with self.settings(DEBUG=is_debug):
            await LiveViewConsumer.send_error(consumer, error, **context)

        # Return the response dict sent via send_json
        return consumer.send_json.call_args[0][0]

    async def test_debug_includes_hint(self):
        response = await self._call_send_error(
            "Something went wrong", is_debug=True, hint="Check your template"
        )
        self.assertEqual(response["error"], "Something went wrong")
        self.assertEqual(response["hint"], "Check your template")

    async def test_production_omits_hint(self):
        response = await self._call_send_error(
            "Something went wrong", is_debug=False, hint="Check your template"
        )
        self.assertEqual(response["error"], "Something went wrong")
        self.assertNotIn("hint", response)

    async def test_debug_includes_traceback_on_exception(self):
        """When called inside an exception handler, traceback is included."""
        import sys

        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
            response = await self._call_send_error(
                "Handler failed", is_debug=True, _exc_info=exc_info
            )

        self.assertIn("traceback", response)
        self.assertIn("ValueError", response["traceback"])

    async def test_production_omits_traceback(self):
        """In production, no traceback is included."""
        import sys

        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
            response = await self._call_send_error(
                "Handler failed", is_debug=False, _exc_info=exc_info
            )

        self.assertNotIn("traceback", response)

    async def test_debug_detail_included(self):
        response = await self._call_send_error(
            "Event rejected",
            is_debug=True,
            debug_detail="Full error: missing field 'name' in handler params",
        )
        self.assertEqual(
            response.get("debug_detail"),
            "Full error: missing field 'name' in handler params",
        )

    async def test_production_strips_debug_detail(self):
        response = await self._call_send_error(
            "Event rejected",
            is_debug=False,
            debug_detail="Full error: missing field 'name'",
        )
        self.assertNotIn("debug_detail", response)
