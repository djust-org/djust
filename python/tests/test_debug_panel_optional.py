"""``DJUST_CONFIG["debug_panel"]`` turns the DEBUG-only debug panel off.

On by default (the panel loads whenever ``DEBUG`` is on, as before). With
``False`` no panel entry point fires: no ``debug-panel.js`` / ``.css``, no
``window.DJUST_DEBUG_INFO``, no per-event ``_debug`` payload on WebSocket or
HTTP responses, and no ``debug-panel.js`` in the service-worker precache list.
``client-dev.js`` still loads, so hot reload keeps working.
"""

from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from djust import LiveView
from djust.config import debug_panel_enabled
from djust.decorators import event_handler

PAGE = "<html><head><title>T</title></head><body><h1>T</h1></body></html>"


class _View(LiveView):
    template_string = PAGE
    count = 0

    @event_handler
    def increment(self):
        self.count += 1


class TestDebugPanelEnabled:
    @override_settings(DEBUG=True, LIVEVIEW_CONFIG={})
    def test_on_by_default_with_debug(self):
        assert debug_panel_enabled() is True

    @override_settings(DEBUG=False, LIVEVIEW_CONFIG={"debug_panel": True})
    def test_never_without_debug(self):
        assert debug_panel_enabled() is False

    @override_settings(DEBUG=True, LIVEVIEW_CONFIG={"debug_panel": False})
    def test_off_with_false(self):
        assert debug_panel_enabled() is False

    @override_settings(DEBUG=True, LIVEVIEW_CONFIG={}, DJUST_CONFIG={"debug_panel": False})
    def test_off_via_djust_config(self):
        assert debug_panel_enabled() is False

    @pytest.mark.parametrize("value", [0, None, "false"])
    def test_only_false_turns_it_off(self, value):
        with override_settings(DEBUG=True, LIVEVIEW_CONFIG={"debug_panel": value}):
            assert debug_panel_enabled() is True


class TestClientScriptInjection:
    @override_settings(DEBUG=True, LIVEVIEW_CONFIG={})
    def test_panel_assets_injected_by_default(self):
        html = _View()._inject_client_script(PAGE)
        assert "djust/debug-panel.js" in html
        assert "djust/debug-panel.css" in html
        assert "window.DJUST_DEBUG_INFO = {" in html
        assert "djust/client-dev.js" in html

    @override_settings(DEBUG=True, LIVEVIEW_CONFIG={"debug_panel": False})
    def test_panel_assets_absent_when_off(self):
        html = _View()._inject_client_script(PAGE)
        assert "debug-panel.js" not in html
        assert "debug-panel.css" not in html
        assert "window.DJUST_DEBUG_INFO = {" not in html
        # Hot reload and the rest of the dev tooling stay.
        assert "djust/client-dev.js" in html
        assert "window.djustDebug = true" in html


class TestWebSocketDebugPayload:
    def _consumer(self):
        from djust.websocket import LiveViewConsumer

        consumer = MagicMock()
        consumer.view_instance = _View()
        consumer._debug_panel_active = True
        consumer._attach_debug_payload = LiveViewConsumer._attach_debug_payload.__get__(
            consumer, type(consumer)
        )
        return consumer

    @override_settings(DEBUG=True, LIVEVIEW_CONFIG={})
    def test_payload_attached_by_default(self):
        response = {"type": "patch", "patches": []}
        self._consumer()._attach_debug_payload(response, event_name="increment")
        assert "_debug" in response

    @override_settings(DEBUG=True, LIVEVIEW_CONFIG={"debug_panel": False})
    def test_payload_skipped_when_off(self):
        response = {"type": "patch", "patches": []}
        self._consumer()._attach_debug_payload(response, event_name="increment")
        assert "_debug" not in response


class TestServiceWorkerAssets:
    def _assets(self):
        from djust.management.commands.generate_sw import Command

        return Command()._get_djust_core_assets()

    @override_settings(DEBUG=True, STATIC_URL="/static/", LIVEVIEW_CONFIG={})
    def test_debug_panel_precached_by_default(self):
        assert "/static/djust/debug-panel.js" in self._assets()

    @override_settings(DEBUG=True, STATIC_URL="/static/", LIVEVIEW_CONFIG={"debug_panel": False})
    def test_debug_panel_not_precached_when_off(self):
        assets = self._assets()
        assert "/static/djust/debug-panel.js" not in assets
        assert "/static/djust/client.js" in assets
