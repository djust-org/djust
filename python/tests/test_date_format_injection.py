"""Tests for DATE_FORMAT / TIME_FORMAT injection into Rust context (#718)."""

import pytest
from unittest.mock import Mock

from django.test import RequestFactory, override_settings

from djust import LiveView


class DateView(LiveView):
    """Minimal view for testing DATE_FORMAT injection."""

    template = "<div dj-root>{{ some_date|date }}</div>"

    def mount(self, request, **kwargs):
        self.some_date = "2026-04-13"


@pytest.fixture
def mock_request():
    factory = RequestFactory()
    request = factory.get("/")
    request.session = Mock()
    request.session.session_key = "test-session"
    return request


class TestDateFormatInjection:
    """Verify _sync_state_to_rust injects DATE_FORMAT from Django settings."""

    @override_settings(DATE_FORMAT="Y/m/d")
    def test_date_format_injected(self, mock_request):
        """DATE_FORMAT should appear in the context sent to Rust."""
        view = DateView()
        view.mount(mock_request)
        view.request = mock_request

        # Create a mock Rust view that captures update_state calls
        mock_rust_view = Mock()
        mock_rust_view.update_state = Mock()
        mock_rust_view.mark_safe_keys = Mock()
        view._rust_view = mock_rust_view

        view._sync_state_to_rust()

        # update_state should have been called with DATE_FORMAT in context
        assert mock_rust_view.update_state.called
        ctx = mock_rust_view.update_state.call_args[0][0]
        assert "DATE_FORMAT" in ctx
        assert ctx["DATE_FORMAT"] == "Y/m/d"

    @override_settings(DATE_FORMAT="Y/m/d", TIME_FORMAT="H:i")
    def test_time_format_also_injected(self, mock_request):
        """TIME_FORMAT should also be injected."""
        view = DateView()
        view.mount(mock_request)
        view.request = mock_request

        mock_rust_view = Mock()
        mock_rust_view.update_state = Mock()
        mock_rust_view.mark_safe_keys = Mock()
        view._rust_view = mock_rust_view

        view._sync_state_to_rust()

        ctx = mock_rust_view.update_state.call_args[0][0]
        assert "TIME_FORMAT" in ctx
        assert ctx["TIME_FORMAT"] == "H:i"

    @override_settings(DATE_FORMAT="Y/m/d")
    def test_explicit_context_not_overridden(self, mock_request):
        """If get_context_data already has DATE_FORMAT, don't override it."""

        class ExplicitDateView(LiveView):
            template = "<div dj-root>{{ some_date|date }}</div>"

            def mount(self, request, **kwargs):
                self.some_date = "2026-04-13"
                self.DATE_FORMAT = "d/m/Y"

        view = ExplicitDateView()
        view.mount(mock_request)
        view.request = mock_request

        mock_rust_view = Mock()
        mock_rust_view.update_state = Mock()
        mock_rust_view.mark_safe_keys = Mock()
        view._rust_view = mock_rust_view

        view._sync_state_to_rust()

        ctx = mock_rust_view.update_state.call_args[0][0]
        assert ctx["DATE_FORMAT"] == "d/m/Y"

    def test_no_injection_when_rust_view_missing(self, mock_request):
        """If _rust_view is None, _sync_state_to_rust is a no-op."""
        view = DateView()
        view.mount(mock_request)
        view.request = mock_request
        view._rust_view = None

        # Should not raise
        view._sync_state_to_rust()
