"""
Tests for navigation — live_patch, live_redirect, URL state management.
"""

import sys
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Add python/ to path so we can import djust submodules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import the mixin module directly to avoid pulling in channels/django via djust.__init__
import importlib.util
_nav_spec = importlib.util.spec_from_file_location(
    "djust.mixins.navigation",
    os.path.join(os.path.dirname(__file__), "..", "djust", "mixins", "navigation.py"),
)
_nav_mod = importlib.util.module_from_spec(_nav_spec)
_nav_spec.loader.exec_module(_nav_mod)
NavigationMixin = _nav_mod.NavigationMixin


def _make_nav_view():
    """Create a minimal view with navigation behavior."""
    class FakeView(NavigationMixin):
        def __init__(self):
            self._init_navigation()
    return FakeView()


# ============================================================================
# NavigationMixin unit tests (no Django required)
# ============================================================================


class TestNavigationMixin:
    """Tests for the NavigationMixin on LiveView."""

    def _make_view(self):
        return _make_nav_view()

    def test_live_patch_queues_command(self):
        view = self._make_view()
        view.live_patch(params={"category": "electronics", "page": 1})
        commands = view._drain_navigation()
        assert len(commands) == 1
        assert commands[0]["type"] == "live_patch"
        assert commands[0]["params"] == {"category": "electronics", "page": 1}
        assert commands[0]["replace"] is False

    def test_live_patch_with_path(self):
        view = self._make_view()
        view.live_patch(path="/search/", params={"q": "test"})
        commands = view._drain_navigation()
        assert commands[0]["path"] == "/search/"
        assert commands[0]["params"] == {"q": "test"}

    def test_live_patch_replace(self):
        view = self._make_view()
        view.live_patch(params={"page": 2}, replace=True)
        commands = view._drain_navigation()
        assert commands[0]["replace"] is True

    def test_live_patch_no_params(self):
        """live_patch with no params should not include params key."""
        view = self._make_view()
        view.live_patch(path="/new-path/")
        commands = view._drain_navigation()
        assert "params" not in commands[0]
        assert commands[0]["path"] == "/new-path/"

    def test_live_patch_empty_params_clears(self):
        """live_patch with {} should include empty params (to clear them)."""
        view = self._make_view()
        view.live_patch(params={})
        commands = view._drain_navigation()
        assert commands[0]["params"] == {}

    def test_live_redirect_queues_command(self):
        view = self._make_view()
        view.live_redirect("/items/42/")
        commands = view._drain_navigation()
        assert len(commands) == 1
        assert commands[0]["type"] == "live_redirect"
        assert commands[0]["path"] == "/items/42/"
        assert commands[0]["replace"] is False

    def test_live_redirect_with_params(self):
        view = self._make_view()
        view.live_redirect("/search/", params={"q": "djust"})
        commands = view._drain_navigation()
        assert commands[0]["params"] == {"q": "djust"}

    def test_live_redirect_replace(self):
        view = self._make_view()
        view.live_redirect("/login/", replace=True)
        commands = view._drain_navigation()
        assert commands[0]["replace"] is True

    def test_drain_clears_commands(self):
        view = self._make_view()
        view.live_patch(params={"a": 1})
        view.live_redirect("/b/")
        assert len(view._drain_navigation()) == 2
        assert len(view._drain_navigation()) == 0

    def test_multiple_commands_ordered(self):
        view = self._make_view()
        view.live_patch(params={"step": 1})
        view.live_patch(params={"step": 2})
        view.live_redirect("/done/")
        commands = view._drain_navigation()
        assert len(commands) == 3
        assert commands[0]["params"] == {"step": 1}
        assert commands[1]["params"] == {"step": 2}
        assert commands[2]["type"] == "live_redirect"

    def test_handle_params_default_noop(self):
        """Default handle_params does nothing (no error)."""
        view = self._make_view()
        view.handle_params({"page": "2"}, "/items/?page=2")


# ============================================================================
# Routing (live_session) tests — requires Django
# ============================================================================

try:
    import django
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False

try:
    import channels
    HAS_CHANNELS = True
except ImportError:
    HAS_CHANNELS = False


def _import_routing():
    """Import routing module directly without going through djust.__init__."""
    spec = importlib.util.spec_from_file_location(
        "djust.routing",
        os.path.join(os.path.dirname(__file__), "..", "djust", "routing.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not HAS_DJANGO, reason="Django not installed")
class TestLiveSession:
    """Tests for the live_session URL routing helper."""

    def setup_method(self):
        """Clear route maps between tests."""
        routing = _import_routing()
        if hasattr(routing.live_session, '_route_maps'):
            routing.live_session._route_maps.clear()

    def test_live_session_creates_prefixed_patterns(self):
        routing = _import_routing()
        from django.urls import path

        # Create a fake view
        class FakeView:
            @classmethod
            def as_view(cls):
                def view(request):
                    pass
                view.view_class = cls
                return view

        FakeView.__module__ = "myapp.views"
        FakeView.__qualname__ = "FakeView"

        patterns = routing.live_session("/app", [
            path("", FakeView.as_view(), name="dashboard"),
        ])

        assert len(patterns) == 1
        # The pattern should have the prefix
        assert patterns[0].name == "dashboard"

    def test_live_session_stores_route_map(self):
        routing = _import_routing()

        class FakeView:
            @classmethod
            def as_view(cls):
                def view(request):
                    pass
                view.view_class = cls
                return view

        FakeView.__module__ = "myapp.views"
        FakeView.__qualname__ = "FakeView"

        from django.urls import path
        routing.live_session("/app", [
            path("", FakeView.as_view(), name="home"),
        ], session_name="test")

        assert "test" in routing.live_session._route_maps
        entries = routing.live_session._route_maps["test"]
        assert len(entries) >= 1
        # Check that view path is stored
        assert any("FakeView" in vp for _, vp in entries)

    def test_get_route_map_script_empty(self):
        routing = _import_routing()
        routing.live_session._route_maps = {}
        result = routing.get_route_map_script()
        assert result == ""

    def test_get_route_map_script_has_content(self):
        routing = _import_routing()
        routing.live_session._route_maps = {
            "test": [("/app/", "myapp.views.DashboardView")]
        }
        result = routing.get_route_map_script()
        assert "window.djust._routeMap" in result
        assert "myapp.views.DashboardView" in result
        assert "<script>" in result


# ============================================================================
# WebSocket consumer integration tests
# ============================================================================


@pytest.mark.skipif(not HAS_CHANNELS, reason="channels not installed")
class TestNavigationWebSocket:
    """Tests for navigation message handling in the WebSocket consumer."""

    def _make_consumer_and_view(self):
        """Create a mock consumer and view for testing."""
        from djust.mixins.navigation import NavigationMixin

        class FakeView(NavigationMixin):
            def __init__(self):
                self._init_navigation()
                self._pending_push_events = []

            def _drain_push_events(self):
                events = self._pending_push_events
                self._pending_push_events = []
                return events

        consumer = MagicMock()
        consumer.send_json = AsyncMock()
        consumer.view_instance = FakeView()

        return consumer, consumer.view_instance

    @pytest.mark.asyncio
    async def test_flush_navigation_sends_commands(self):
        """_flush_navigation should send queued navigation commands."""
        from djust.websocket import LiveViewConsumer

        consumer, view = self._make_consumer_and_view()

        view.live_patch(params={"page": 2})

        # Manually call flush
        consumer_instance = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer_instance.view_instance = view
        consumer_instance.send_json = AsyncMock()

        await consumer_instance._flush_navigation()

        consumer_instance.send_json.assert_called_once()
        call_data = consumer_instance.send_json.call_args[0][0]
        assert call_data["type"] == "navigation"
        assert call_data["params"] == {"page": 2}

    @pytest.mark.asyncio
    async def test_flush_navigation_empty(self):
        """_flush_navigation with no commands should not send anything."""
        from djust.websocket import LiveViewConsumer

        consumer_instance = LiveViewConsumer.__new__(LiveViewConsumer)
        view = MagicMock()
        view._drain_navigation.return_value = []
        consumer_instance.view_instance = view
        consumer_instance.send_json = AsyncMock()

        await consumer_instance._flush_navigation()

        consumer_instance.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_navigation_no_view(self):
        """_flush_navigation with no view instance should be a no-op."""
        from djust.websocket import LiveViewConsumer

        consumer_instance = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer_instance.view_instance = None
        consumer_instance.send_json = AsyncMock()

        await consumer_instance._flush_navigation()

        consumer_instance.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_navigation_multiple_commands(self):
        """Multiple navigation commands should each be sent separately."""
        from djust.websocket import LiveViewConsumer

        consumer_instance = LiveViewConsumer.__new__(LiveViewConsumer)
        from djust.mixins.navigation import NavigationMixin

        class FakeView(NavigationMixin):
            def __init__(self):
                self._init_navigation()

        view = FakeView()
        view.live_patch(params={"a": 1})
        view.live_redirect("/b/")

        consumer_instance.view_instance = view
        consumer_instance.send_json = AsyncMock()

        await consumer_instance._flush_navigation()

        assert consumer_instance.send_json.call_count == 2
        first = consumer_instance.send_json.call_args_list[0][0][0]
        second = consumer_instance.send_json.call_args_list[1][0][0]
        assert first["type"] == "navigation"
        assert second["type"] == "navigation"
        assert second["path"] == "/b/"


# ============================================================================
# Integration: handle_params callback
# ============================================================================


class TestHandleParams:
    """Tests for handle_params being called on URL changes."""

    def test_handle_params_override(self):
        """Views can override handle_params to update state."""
        class MyView(NavigationMixin):
            def __init__(self):
                self._init_navigation()
                self.category = "all"
                self.page = 1

            def handle_params(self, params, uri):
                self.category = params.get("category", "all")
                self.page = int(params.get("page", 1))

        view = MyView()
        view.handle_params({"category": "electronics", "page": "3"}, "/items/?category=electronics&page=3")
        assert view.category == "electronics"
        assert view.page == 3

    def test_handle_params_partial_update(self):
        """handle_params with missing keys should use defaults."""
        class MyView(NavigationMixin):
            def __init__(self):
                self._init_navigation()
                self.category = "all"
                self.page = 1

            def handle_params(self, params, uri):
                self.category = params.get("category", "all")
                self.page = int(params.get("page", 1))

        view = MyView()
        view.handle_params({"category": "books"}, "/items/?category=books")
        assert view.category == "books"
        assert view.page == 1  # default
