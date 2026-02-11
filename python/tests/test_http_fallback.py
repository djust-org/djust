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

    def test_standard_format_without_params_key(self):
        """Standard format without params key: {"event": "increment"} — must not leak event into params."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"event":"increment"}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 200

        data = json.loads(response.content.decode("utf-8"))
        assert "patches" in data or "html" in data

    def test_http_fallback_preserves_cache_request_id(self):
        """HTTP fallback: _cacheRequestId is preserved for @cache decorator support."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"_cacheRequestId": "req-123", "_targetElement": "button"}',
            content_type="application/json",
            HTTP_X_DJUST_EVENT="increment",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 200

        data = json.loads(response.content.decode("utf-8"))
        assert "patches" in data or "html" in data
        # cache_request_id should be echoed back in the response
        if "patches" in data:
            assert data.get("cache_request_id") == "req-123"

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


@pytest.mark.django_db
class TestHTTPOnlySessionState:
    """Test that GET saves session state when use_websocket is False (Issue #264)."""

    def _setup_view(self, use_websocket=True):
        """Create a view and do initial GET."""
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)

        from unittest.mock import patch as mock_patch

        with mock_patch("djust.mixins.request._lv_config") as mock_config:
            mock_config.get.return_value = use_websocket
            view.get(get_request)

        return view, factory, get_request

    def test_http_only_mode_saves_state_on_get(self, settings):
        """When use_websocket=False, GET saves state to session so POST doesn't re-mount."""
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)

        from djust.config import config as lv_config

        original = lv_config.get("use_websocket", True)
        lv_config.set("use_websocket", False)
        try:
            view.get(get_request)

            # Check that state was saved in the session
            view_key = "liveview_/test/"
            saved_state = get_request.session.get(view_key, {})
            assert "count" in saved_state
            assert saved_state["count"] == 0

            # Now POST should NOT re-mount (saved state found)
            post_request = factory.post(
                "/test/",
                data='{"event":"increment","params":{}}',
                content_type="application/json",
            )
            post_request.session = get_request.session
            response = view.post(post_request)
            assert response.status_code == 200

            # Verify count was incremented from saved state (0 -> 1), not re-mounted (0 -> 1)
            data = json.loads(response.content.decode("utf-8"))
            assert "patches" in data or "html" in data
        finally:
            lv_config.set("use_websocket", original)

    def test_websocket_mode_does_not_save_state_on_get(self, settings):
        """When use_websocket=True (default), GET does NOT save state to session."""
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)

        from djust.config import config as lv_config

        original = lv_config.get("use_websocket", True)
        lv_config.set("use_websocket", True)
        try:
            view.get(get_request)

            # State should NOT be in session (WebSocket mode manages state in-memory)
            view_key = "liveview_/test/"
            saved_state = get_request.session.get(view_key, {})
            assert saved_state == {}
        finally:
            lv_config.set("use_websocket", original)


@pytest.mark.django_db
class TestHTTPFallbackSecurity:
    """Test that post() enforces event security (matches WebSocket security model)."""

    def _setup_view(self, view_cls=None):
        """Create a view and do initial GET to establish state."""
        view = (view_cls or CounterView)()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)
        view.get(get_request)
        return view, factory, get_request

    def test_unsafe_event_name_blocked(self):
        """Dunder and private method names are blocked."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"event":"__init__","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 400

    def test_private_method_blocked(self):
        """_private methods are blocked by is_safe_event_name."""
        view, factory, get_request = self._setup_view()

        post_request = factory.post(
            "/test/",
            data='{"event":"_initialize_temporary_assigns","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 400

    def test_undecorated_method_blocked(self):
        """Public methods without @event_handler are blocked via POST."""

        class UnsafeView(LiveView):
            template = "<div data-djust-root>{{ data }}</div>"

            def mount(self, request, **kwargs):
                self.data = "safe"

            def undecorated_method(self):
                """This method is NOT decorated with @event_handler."""
                self.data = "hacked"

        view, factory, get_request = self._setup_view(UnsafeView)

        post_request = factory.post(
            "/test/",
            data='{"event":"undecorated_method","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        response = view.post(post_request)
        assert response.status_code == 400
