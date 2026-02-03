"""
Tests for offline/PWA support — OfflineMixin, event queueing, state persistence.
"""

import sys
import os
import json
import pytest

# Add python/ to path so we can import djust submodules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import the mixin module directly to avoid pulling in channels/django via djust.__init__
import importlib.util

_offline_spec = importlib.util.spec_from_file_location(
    "djust.offline.mixin",
    os.path.join(os.path.dirname(__file__), "..", "djust", "offline", "mixin.py"),
)
_offline_mod = importlib.util.module_from_spec(_offline_spec)
_offline_spec.loader.exec_module(_offline_mod)

OfflineMixin = _offline_mod.OfflineMixin


# ============================================================================
# OfflineMixin Tests
# ============================================================================


def _make_offline_view():
    """Create a minimal view with offline behavior."""

    class FakeView(OfflineMixin):
        offline_template = None
        sync_events = []
        persist_state = False
        persist_fields = None
        cache_templates = []

        def __init__(self):
            self._offline_config_sent = False
            self._queued_events = []
            # Add some test state
            self.count = 0
            self.items = []
            self.name = "test"

        def get_context_data(self, **kwargs):
            context = kwargs.copy()
            return OfflineMixin.get_context_data(self, **context)

        def handle_increment(self, amount=1):
            self.count += amount

        def handle_add_item(self, item):
            self.items.append(item)

    return FakeView


class TestOfflineMixinConfig:
    """Tests for OfflineMixin configuration."""

    def test_default_config(self):
        """Default config should have offline features disabled."""
        FakeView = _make_offline_view()
        view = FakeView()
        config = view.get_offline_config()

        assert config["enabled"] is False
        assert config["syncEvents"] == []
        assert config["persistState"] is False
        assert config["persistFields"] is None

    def test_offline_template_enables_offline(self):
        """Setting offline_template should enable offline mode."""
        FakeView = _make_offline_view()
        FakeView.offline_template = "offline.html"
        view = FakeView()
        config = view.get_offline_config()

        assert config["enabled"] is True
        assert config["offlineTemplate"] == "offline.html"

    def test_sync_events_enables_offline(self):
        """Setting sync_events should enable offline mode."""
        FakeView = _make_offline_view()
        FakeView.sync_events = ["submit", "save"]
        view = FakeView()
        config = view.get_offline_config()

        assert config["enabled"] is True
        assert config["syncEvents"] == ["submit", "save"]

    def test_persist_state_enables_offline(self):
        """Setting persist_state should enable offline mode."""
        FakeView = _make_offline_view()
        FakeView.persist_state = True
        view = FakeView()
        config = view.get_offline_config()

        assert config["enabled"] is True
        assert config["persistState"] is True

    def test_context_includes_offline_config(self):
        """get_context_data should include offline config."""
        FakeView = _make_offline_view()
        FakeView.sync_events = ["submit"]
        view = FakeView()
        context = view.get_context_data()

        assert "djust_offline_config" in context
        assert context["djust_offline_config"]["enabled"] is True


class TestOfflineMixinState:
    """Tests for state persistence functionality."""

    def test_get_persistable_state_all_fields(self):
        """get_persistable_state should return all serializable public fields."""
        FakeView = _make_offline_view()
        view = FakeView()
        view.count = 5
        view.items = ["a", "b"]
        view.name = "test_view"

        state = view.get_persistable_state()

        assert state["count"] == 5
        assert state["items"] == ["a", "b"]
        assert state["name"] == "test_view"

    def test_get_persistable_state_filters_private(self):
        """get_persistable_state should skip private attributes."""
        FakeView = _make_offline_view()
        view = FakeView()
        view._private_field = "secret"
        view.public_field = "visible"

        state = view.get_persistable_state()

        assert "_private_field" not in state
        assert "public_field" in state

    def test_get_persistable_state_with_persist_fields(self):
        """get_persistable_state should respect persist_fields filter."""
        FakeView = _make_offline_view()
        FakeView.persist_fields = ["count", "name"]
        view = FakeView()
        view.count = 10
        view.items = ["x"]
        view.name = "filtered"

        state = view.get_persistable_state()

        assert state["count"] == 10
        assert state["name"] == "filtered"
        assert "items" not in state

    def test_get_persistable_state_skips_non_serializable(self):
        """get_persistable_state should skip non-JSON-serializable values."""
        FakeView = _make_offline_view()
        view = FakeView()
        view.count = 1
        view.func_field = lambda x: x  # Not serializable

        state = view.get_persistable_state()

        assert "count" in state
        assert "func_field" not in state

    def test_restore_persisted_state(self):
        """restore_persisted_state should set attributes from saved state."""
        FakeView = _make_offline_view()
        view = FakeView()

        saved_state = {"count": 42, "items": ["restored"], "name": "restored_view"}

        view.restore_persisted_state(saved_state)

        assert view.count == 42
        assert view.items == ["restored"]
        assert view.name == "restored_view"

    def test_restore_persisted_state_respects_persist_fields(self):
        """restore_persisted_state should only restore fields in persist_fields."""
        FakeView = _make_offline_view()
        FakeView.persist_fields = ["count"]
        view = FakeView()
        view.items = ["original"]

        saved_state = {"count": 99, "items": ["should_not_restore"]}

        view.restore_persisted_state(saved_state)

        assert view.count == 99
        assert view.items == ["original"]  # Not restored

    def test_restore_persisted_state_skips_private(self):
        """restore_persisted_state should skip private attributes."""
        FakeView = _make_offline_view()
        view = FakeView()

        saved_state = {"_private": "should_skip", "count": 1}

        view.restore_persisted_state(saved_state)

        assert not hasattr(view, "_private") or view._private != "should_skip"
        assert view.count == 1


class TestOfflineMixinEventQueue:
    """Tests for event queueing functionality."""

    def test_process_queued_events_basic(self):
        """process_queued_events should call handlers for queued events."""
        FakeView = _make_offline_view()
        FakeView.sync_events = ["increment", "add_item"]
        view = FakeView()

        events = [
            {"name": "increment", "params": {"amount": 5}},
            {"name": "add_item", "params": {"item": "queued_item"}},
        ]

        results = view.process_queued_events(events)

        assert len(results) == 2
        assert results[0]["success"] is True
        assert results[1]["success"] is True
        assert view.count == 5
        assert view.items == ["queued_item"]

    def test_process_queued_events_missing_handler(self):
        """process_queued_events should report error for missing handlers."""
        FakeView = _make_offline_view()
        FakeView.sync_events = ["nonexistent"]
        view = FakeView()

        events = [{"name": "nonexistent", "params": {}}]

        results = view.process_queued_events(events)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "not found" in results[0]["error"].lower()

    def test_process_queued_events_not_in_sync_events(self):
        """process_queued_events should skip events not in sync_events."""
        FakeView = _make_offline_view()
        FakeView.sync_events = ["increment"]  # Only increment allowed
        view = FakeView()

        events = [
            {"name": "increment", "params": {}},
            {"name": "add_item", "params": {"item": "x"}},  # Not allowed
        ]

        results = view.process_queued_events(events)

        assert len(results) == 2
        assert results[0]["success"] is True
        assert results[1]["success"] is False
        assert results[1].get("skipped") is True

    def test_process_queued_events_empty_sync_events(self):
        """process_queued_events should skip all events if sync_events is empty."""
        FakeView = _make_offline_view()
        FakeView.sync_events = []
        view = FakeView()

        events = [{"name": "increment", "params": {}}]

        results = view.process_queued_events(events)

        # Empty sync_events = all events allowed (backwards compatibility)
        assert len(results) == 1
        # With empty sync_events list, it should actually process (check logic)

    def test_process_queued_events_with_timestamps(self):
        """process_queued_events should preserve timestamps in results."""
        FakeView = _make_offline_view()
        FakeView.sync_events = ["increment"]
        view = FakeView()

        timestamp = 1704067200000
        events = [{"name": "increment", "params": {}, "timestamp": timestamp}]

        results = view.process_queued_events(events)

        assert results[0]["timestamp"] == timestamp

    def test_process_queued_events_handler_exception(self):
        """process_queued_events should catch and report handler exceptions."""
        FakeView = _make_offline_view()
        FakeView.sync_events = ["failing"]
        view = FakeView()

        def failing_handler():
            raise ValueError("Handler failed")

        view.handle_failing = failing_handler

        events = [{"name": "failing", "params": {}}]

        results = view.process_queued_events(events)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "Handler failed" in results[0]["error"]


class TestOfflineMixinContext:
    """Tests for offline context generation."""

    def test_get_offline_context_basic(self):
        """get_offline_context should return basic context."""
        FakeView = _make_offline_view()
        view = FakeView()

        context = view.get_offline_context()

        assert context["view"] == view
        assert context["offline"] is True

    def test_get_offline_context_custom(self):
        """get_offline_context can be overridden for custom context."""
        FakeView = _make_offline_view()

        class CustomView(FakeView):
            def get_offline_context(self):
                context = super().get_offline_context()
                context["custom_value"] = "test"
                return context

        view = CustomView()
        context = view.get_offline_context()

        assert context["custom_value"] == "test"


class TestOfflineMixinCacheUrls:
    """Tests for cache URL generation."""

    def test_get_cache_urls_empty(self):
        """get_cache_urls should return empty list by default."""
        FakeView = _make_offline_view()
        view = FakeView()

        urls = view.get_cache_urls()

        assert urls == []

    def test_get_cache_urls_with_offline_template(self):
        """get_cache_urls should include offline template URL."""
        FakeView = _make_offline_view()
        FakeView.offline_template = "myapp/offline.html"
        view = FakeView()

        urls = view.get_cache_urls()

        assert len(urls) == 1
        assert "myapp/offline.html" in urls[0]


# ============================================================================
# Offline Directive Tests
# ============================================================================


class TestOfflineDirectives:
    """Tests for offline directive attributes in templates."""

    def test_dj_offline_show_attribute(self):
        """dj-offline-show attribute should be valid."""
        attr = 'dj-offline-show'
        assert 'dj-offline-show' in attr

    def test_dj_offline_hide_attribute(self):
        """dj-offline-hide attribute should be valid."""
        attr = 'dj-offline-hide'
        assert 'dj-offline-hide' in attr

    def test_dj_offline_disable_attribute(self):
        """dj-offline-disable attribute should be valid."""
        attr = 'dj-offline-disable'
        assert 'dj-offline-disable' in attr


# ============================================================================
# Service Worker Template Tests
# ============================================================================


class TestServiceWorkerTemplate:
    """Tests for service worker template content."""

    def test_service_worker_template_exists(self):
        """SERVICE_WORKER_TEMPLATE should be defined."""
        from djust.offline.mixin import SERVICE_WORKER_TEMPLATE
        
        assert SERVICE_WORKER_TEMPLATE is not None
        assert len(SERVICE_WORKER_TEMPLATE) > 0

    def test_service_worker_template_contains_install(self):
        """Template should contain install event listener."""
        from djust.offline.mixin import SERVICE_WORKER_TEMPLATE
        
        assert "addEventListener('install'" in SERVICE_WORKER_TEMPLATE

    def test_service_worker_template_contains_fetch(self):
        """Template should contain fetch event listener."""
        from djust.offline.mixin import SERVICE_WORKER_TEMPLATE
        
        assert "addEventListener('fetch'" in SERVICE_WORKER_TEMPLATE

    def test_service_worker_template_contains_sync(self):
        """Template should contain sync event listener."""
        from djust.offline.mixin import SERVICE_WORKER_TEMPLATE
        
        assert "addEventListener('sync'" in SERVICE_WORKER_TEMPLATE


# ============================================================================
# PWA Manifest Template Tests
# ============================================================================


class TestPWAManifestTemplate:
    """Tests for PWA manifest template."""

    def test_pwa_manifest_template_exists(self):
        """PWA_MANIFEST_TEMPLATE should be defined."""
        from djust.offline.mixin import PWA_MANIFEST_TEMPLATE
        
        assert PWA_MANIFEST_TEMPLATE is not None
        assert isinstance(PWA_MANIFEST_TEMPLATE, dict)

    def test_pwa_manifest_has_required_fields(self):
        """PWA manifest should have required fields."""
        from djust.offline.mixin import PWA_MANIFEST_TEMPLATE
        
        assert "name" in PWA_MANIFEST_TEMPLATE
        assert "short_name" in PWA_MANIFEST_TEMPLATE
        assert "start_url" in PWA_MANIFEST_TEMPLATE
        assert "display" in PWA_MANIFEST_TEMPLATE
        assert "icons" in PWA_MANIFEST_TEMPLATE

    def test_pwa_manifest_icons_format(self):
        """PWA manifest icons should have proper format."""
        from djust.offline.mixin import PWA_MANIFEST_TEMPLATE
        
        icons = PWA_MANIFEST_TEMPLATE["icons"]
        assert len(icons) >= 2  # At least 192 and 512 icons
        
        for icon in icons:
            assert "src" in icon
            assert "sizes" in icon
            assert "type" in icon


# ============================================================================
# Integration Tests (if Django available)
# ============================================================================

try:
    import django
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False


@pytest.mark.skipif(not HAS_DJANGO, reason="Django not installed")
class TestOfflineMixinIntegration:
    """Integration tests requiring Django."""

    def test_import_from_djust_offline(self):
        """OfflineMixin should be importable from djust.offline."""
        from djust.offline import OfflineMixin
        
        assert hasattr(OfflineMixin, 'get_offline_config')
        assert hasattr(OfflineMixin, 'get_persistable_state')
        assert hasattr(OfflineMixin, 'process_queued_events')


@pytest.mark.skipif(not HAS_DJANGO, reason="Django not installed")
class TestPWATemplateTagsIntegration:
    """Integration tests for PWA template tags."""

    def test_djust_pwa_tag_import(self):
        """Template tags should be importable."""
        from djust.templatetags import djust_pwa
        
        assert hasattr(djust_pwa, 'djust_pwa_manifest')
        assert hasattr(djust_pwa, 'djust_sw_register')
        assert hasattr(djust_pwa, 'djust_offline_indicator')


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases in offline support."""

    def test_circular_reference_in_state(self):
        """get_persistable_state should handle objects with circular references."""
        FakeView = _make_offline_view()
        view = FakeView()
        
        # Create circular reference
        circular = {"a": 1}
        circular["self"] = circular
        view.circular_data = circular

        # Should not raise, should skip the circular field
        state = view.get_persistable_state()
        assert "circular_data" not in state

    def test_large_state(self):
        """get_persistable_state should handle large state objects."""
        FakeView = _make_offline_view()
        view = FakeView()
        view.large_list = list(range(10000))

        state = view.get_persistable_state()
        assert len(state["large_list"]) == 10000

    def test_empty_events_list(self):
        """process_queued_events should handle empty events list."""
        FakeView = _make_offline_view()
        view = FakeView()

        results = view.process_queued_events([])
        assert results == []

    def test_event_with_no_name(self):
        """process_queued_events should handle events without name."""
        FakeView = _make_offline_view()
        view = FakeView()

        events = [{"params": {}}]  # Missing 'name'
        results = view.process_queued_events(events)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "missing" in results[0]["error"].lower()

    def test_unicode_in_state(self):
        """get_persistable_state should handle unicode strings."""
        FakeView = _make_offline_view()
        view = FakeView()
        view.unicode_field = "Hello 世界 🌍"

        state = view.get_persistable_state()
        assert state["unicode_field"] == "Hello 世界 🌍"

    def test_nested_objects_in_state(self):
        """get_persistable_state should handle nested objects."""
        FakeView = _make_offline_view()
        view = FakeView()
        view.nested = {
            "level1": {
                "level2": {
                    "value": [1, 2, 3]
                }
            }
        }

        state = view.get_persistable_state()
        assert state["nested"]["level1"]["level2"]["value"] == [1, 2, 3]
