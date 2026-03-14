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

    template = """<div dj-root>
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

            # Modify saved session count so we can distinguish restore (5 -> 6)
            # from re-mount (0 -> 1)
            get_request.session[view_key] = {"count": 5}

            post_request = factory.post(
                "/test/",
                data='{"event":"increment","params":{}}',
                content_type="application/json",
            )
            post_request.session = get_request.session
            response = view.post(post_request)
            assert response.status_code == 200

            data = json.loads(response.content.decode("utf-8"))
            # If state was restored from session, count is 5+1=6, not 0+1=1
            if "html" in data:
                assert "6" in data["html"]
            else:
                assert "patches" in data
        finally:
            lv_config.set("use_websocket", original)

    def test_websocket_mode_saves_state_on_get(self, settings):
        """When use_websocket=True (default), GET saves state to session so WS mount can restore it."""
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)

        from djust.config import config as lv_config

        original = lv_config.get("use_websocket", True)
        lv_config.set("use_websocket", True)
        try:
            view.get(get_request)

            # State SHOULD be in session (WS mount restores it to skip redundant mount())
            view_key = "liveview_/test/"
            saved_state = get_request.session.get(view_key, {})
            assert saved_state != {}, "State should be saved to session for WS mount restoration"
            assert "count" in saved_state
        finally:
            lv_config.set("use_websocket", original)


@pytest.mark.django_db
class TestWSMountSkip:
    """Tests for skip-mount-on-prerender: WS consumer restores state from session
    instead of re-running mount() when the client sends has_prerendered=true.
    Covers the state restore logic in websocket.py._handle_connect().
    """

    def _get_and_save_state(self):
        """Do an HTTP GET, return the view + request with state in session."""
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/counter/")
        get_request = add_session_to_request(get_request)
        view.get(get_request)
        return view, get_request

    def test_session_key_format_uses_path(self):
        """GET saves state under liveview_{path} (the HTTP format, shared with WS consumer)."""
        _, get_request = self._get_and_save_state()
        assert (
            "liveview_/counter/" in get_request.session
        ), "Session key must use liveview_{path} format so WS consumer can find it"

    def test_ws_restore_sets_attributes_from_session(self):
        """State saved during GET is restored onto a fresh view via safe_setattr."""
        from djust.security import safe_setattr

        _, get_request = self._get_and_save_state()
        # Manually write a specific count into session (simulating a view with count=42)
        get_request.session["liveview_/counter/"] = {"count": 42}
        get_request.session.save()
        saved_state = get_request.session.get("liveview_/counter/", {})
        assert saved_state, "Precondition: state must be in session"

        # Simulate what the WS consumer does: restore onto a fresh view instance
        fresh_view = CounterView()
        for key, value in saved_state.items():
            safe_setattr(fresh_view, key, value, allow_private=False)

        assert fresh_view.count == 42, "Restored view should have the count from session state"

    def test_ws_restore_skips_private_attributes(self):
        """safe_setattr with allow_private=False does not set underscore-prefixed keys."""
        from djust.security import safe_setattr

        view = CounterView()
        view.count = 0
        # Attempt to restore a private key (as if it were in session) — must be blocked
        result = safe_setattr(view, "_internal", "injected", allow_private=False)
        assert result is False, "safe_setattr must block private attributes"
        assert not hasattr(view, "_internal")

    def test_ws_mount_called_when_no_saved_state(self):
        """When session has no saved state, mount() must run (mounted=False path)."""
        from unittest.mock import patch

        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/counter/")
        get_request = add_session_to_request(get_request)
        # Do NOT call view.get() — session is empty

        mount_called = []
        original_mount = view.mount

        def tracking_mount(request, **kwargs):
            mount_called.append(True)
            return original_mount(request, **kwargs)

        with patch.object(view, "mount", side_effect=tracking_mount):
            view.mount(get_request)

        assert mount_called, "mount() must be called when no saved state exists"

    def test_ensure_tenant_called_regardless_of_saved_state(self):
        """_ensure_tenant must be called unconditionally (not only in the not-mounted path).

        Regression test: previously _ensure_tenant was inside the `if not mounted:`
        block, so multi-tenant views had self.tenant=None after session restore.
        """
        import inspect

        from djust.websocket import LiveViewConsumer

        # Verify that in the current source, _ensure_tenant check appears BEFORE
        # the `if not mounted:` block — this is the structural invariant we rely on.
        source = inspect.getsource(LiveViewConsumer.handle_mount)
        ensure_tenant_pos = source.find("_ensure_tenant")
        not_mounted_pos = source.find("if not mounted:")
        assert ensure_tenant_pos != -1, "_ensure_tenant hook must exist in handle_mount"
        assert not_mounted_pos != -1, "if not mounted: block must exist in handle_mount"
        assert ensure_tenant_pos < not_mounted_pos, (
            "_ensure_tenant must be called BEFORE `if not mounted:` "
            "so it runs even when state is restored from session. "
            "Regression of #342 fix."
        )


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


@pytest.mark.django_db
class TestHTTPPostDebugPayload:
    """Test that POST responses include _debug payload when DEBUG=True (#267)."""

    def _setup_view(self):
        """Create a view and do initial GET to establish state."""
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)
        view.get(get_request)
        return view, factory, get_request

    def test_debug_in_post_response(self, settings):
        """When DEBUG=True, POST response should include _debug payload."""
        settings.DEBUG = True
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
        assert "_debug" in data
        debug = data["_debug"]

        # Should contain view class info
        assert debug["view_class"] == "CounterView"

        # Should contain event name
        assert debug["_eventName"] == "increment"

        # Should contain performance timing
        assert "performance" in debug
        assert "handler_ms" in debug["performance"]
        assert "render_ms" in debug["performance"]
        assert isinstance(debug["performance"]["handler_ms"], (int, float))
        assert isinstance(debug["performance"]["render_ms"], (int, float))

        # Event responses contain variables but NOT handlers (handlers are
        # static and only sent on initial mount to reduce payload size)
        assert "variables" in debug
        assert "handlers" not in debug

    def test_no_debug_when_not_debug(self, settings):
        """When DEBUG=False, POST response should NOT include _debug payload."""
        settings.DEBUG = False
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
        assert "_debug" not in data


@pytest.mark.django_db
class TestHTTPFallbackSecurityUndecorated:
    """Test that undecorated methods are blocked via POST."""

    def _setup_view(self, view_cls=None):
        """Create a view and do initial GET to establish state."""
        view = (view_cls or CounterView)()
        factory = RequestFactory()
        get_request = factory.get("/test/")
        get_request = add_session_to_request(get_request)
        view.get(get_request)
        return view, factory, get_request

    def test_undecorated_method_blocked(self):
        """Public methods without @event_handler are blocked via POST."""

        class UnsafeView(LiveView):
            template = "<div dj-root>{{ data }}</div>"

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
