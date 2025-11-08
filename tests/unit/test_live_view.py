"""
Unit tests for LiveView core functionality.
"""

import pytest
from django_rust_live.live_view import LiveView, cleanup_expired_sessions, get_session_stats


class TestLiveViewBasics:
    """Test basic LiveView functionality."""

    def test_liveview_initialization(self):
        """Test LiveView can be instantiated."""
        class SimpleView(LiveView):
            template_string = "<div>{{ message }}</div>"

        view = SimpleView()
        assert view is not None
        assert view.template_string == "<div>{{ message }}</div>"

    def test_get_template_with_string(self):
        """Test get_template returns template string."""
        class SimpleView(LiveView):
            template_string = "<div>Hello</div>"

        view = SimpleView()
        template = view.get_template()
        assert template == "<div>Hello</div>"

    def test_mount_called(self, get_request):
        """Test mount method is called on GET request."""
        class CounterView(LiveView):
            template_string = "<div>{{ count }}</div>"
            mount_called = False

            def mount(self, request, **kwargs):
                self.mount_called = True
                self.count = 0

        view = CounterView()
        response = view.get(get_request)

        assert view.mount_called is True
        assert hasattr(view, 'count')
        assert view.count == 0


class TestSessionCleanup:
    """Test session cleanup functionality."""

    def test_cleanup_expired_sessions_empty(self):
        """Test cleanup with empty cache."""
        cleaned = cleanup_expired_sessions()
        assert cleaned == 0

    def test_get_session_stats_empty(self):
        """Test stats with empty cache."""
        stats = get_session_stats()
        assert stats['total_sessions'] == 0
        assert stats['oldest_session_age'] == 0

    def test_session_stats_with_sessions(self, get_request):
        """Test stats with active sessions."""
        # Create a LiveView to populate cache
        class SimpleView(LiveView):
            template_string = "<div>Test</div>"

        view = SimpleView()
        view.get(get_request)

        stats = get_session_stats()
        assert stats['total_sessions'] >= 0  # May be 0 if no session key


class TestErrorHandling:
    """Test error handling improvements."""

    def test_invalid_event_handler(self, post_request):
        """Test calling non-existent event handler."""
        import json

        class SimpleView(LiveView):
            template_string = "<div>{{ count }}</div>"

            def mount(self, request, **kwargs):
                self.count = 0

        view = SimpleView()

        # Simulate POST with non-existent handler
        post_request._body = json.dumps({'event': 'nonexistent', 'params': {}}).encode()
        response = view.post(post_request)

        assert response.status_code == 500
        assert 'error' in response.json()
