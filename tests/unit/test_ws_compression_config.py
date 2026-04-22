"""Tests for the ``websocket_compression`` config key (v0.6.0).

Verifies:
- Default is True.
- ``DJUST_WS_COMPRESSION`` Django setting overrides it.
- The value reaches the injected client script as a
  ``window.DJUST_WS_COMPRESSION`` literal.
"""

from __future__ import annotations

import importlib

from django.test import override_settings


def _fresh_config():
    """Re-import ``djust.config`` so the DefaultConfig picks up current settings."""
    import djust.config

    return importlib.reload(djust.config).config


def test_default_is_true():
    config = _fresh_config()
    assert config.get("websocket_compression") is True


@override_settings(DJUST_WS_COMPRESSION=False)
def test_setting_overrides_to_false():
    config = _fresh_config()
    assert config.get("websocket_compression") is False


@override_settings(DJUST_WS_COMPRESSION=True)
def test_explicit_true_setting_is_preserved():
    config = _fresh_config()
    assert config.get("websocket_compression") is True


@override_settings(DJUST_WS_COMPRESSION=1)
def test_truthy_non_bool_coerced():
    """Accepts truthy non-bool (e.g. int 1) and coerces to bool."""
    config = _fresh_config()
    assert config.get("websocket_compression") is True


@override_settings(DJUST_WS_COMPRESSION=0)
def test_falsy_non_bool_coerced():
    config = _fresh_config()
    assert config.get("websocket_compression") is False


def test_injected_script_emits_ws_compression_literal():
    """The injected client bootstrap exposes window.DJUST_WS_COMPRESSION."""
    from djust.mixins.post_processing import PostProcessingMixin

    class _FakeView(PostProcessingMixin):
        def get_debug_info(self):
            return {}

    view = _FakeView()
    injected = view._inject_client_script("<html><body></body></html>")
    assert "window.DJUST_WS_COMPRESSION" in injected
