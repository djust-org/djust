"""
Tests for HTTP fallback protocol.

The client JS sends events in two formats:
1. Standard (WebSocket-originated): {"event": "name", "params": {...}}
2. HTTP fallback: X-Djust-Event header + flat params in body

Both formats must be accepted by the server's post() method.
Fixes: https://github.com/djust-org/djust/issues/255
"""

import json

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler


class CounterView(LiveView):
    """Simple test view for HTTP fallback protocol testing."""

    template = """<div data-djust-root>
    <span class="count">{{ count }}</span>
    <button dj-click="increment">+</button>
    <button dj-click="set_count" data-count="10">Set 10</button>
</div>"""

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def set_count(self, count: int = 0, **kwargs):
        self.count = count


def add_session_to_request(request):
    """Helper to add session to request."""
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
class TestHTTPFallbackProtocol:
    """Test that post() handles both standard and HTTP fallback formats."""

    def _setup_view(self):
        """Create a view and do initial GET to establish state."""
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)
        view.get(get_request)
        return view, factory, get_request

    def test_standard_format_still_works(self):
        """Standard format: {"event": "name", "params": {...}} in body."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"event":"increment","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 200

        data = json.loads(response.content.decode("utf-8"))
        assert "patches" in data or "html" in data

    def test_standard_format_with_params(self):
        """Standard format with params: {"event": "set_count", "params": {"count": 42}}."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"event":"set_count","params":{"count":42}}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 200

        data = json.loads(response.content.decode("utf-8"))
        # Verify the handler was called — check patches or html contains "42"
        if "html" in data:
            assert "42" in data["html"]
        else:
            assert "patches" in data

    def test_http_fallback_event_in_header(self):
        """HTTP fallback: event name in X-Djust-Event header, empty body."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="increment",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 200

        data = json.loads(response.content.decode("utf-8"))
        assert "patches" in data or "html" in data

    def test_http_fallback_with_flat_params(self):
        """HTTP fallback: event in header, flat params in body."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"count": 99, "_targetElement": "button.set-count"}',
            content_type="application/json",
            HTTP_X_DJUST_EVENT="set_count",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 200

        data = json.loads(response.content.decode("utf-8"))
        # Verify the handler was called with count=99
        if "html" in data:
            assert "99" in data["html"]
        else:
            assert "patches" in data

    def test_http_fallback_underscore_params_filtered(self):
        """HTTP fallback: _prefixed params (like _targetElement) are filtered out."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"_targetElement": "button", "_cacheRequestId": "abc123"}',
            content_type="application/json",
            HTTP_X_DJUST_EVENT="increment",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 200

        # Should succeed — _prefixed params filtered, increment() called with no params
        data = json.loads(response.content.decode("utf-8"))
        assert "patches" in data or "html" in data

    def test_no_event_returns_400(self):
        """Missing event name in both body and header returns 400."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"some_param": "value"}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 400
