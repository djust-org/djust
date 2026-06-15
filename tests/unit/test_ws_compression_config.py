"""Tests for the ``websocket_compression`` config key (v0.6.0).

Verifies:
- Default is True.
- ``DJUST_WS_COMPRESSION`` Django setting overrides it.
- The value reaches the injected client script as a
  ``window.DJUST_WS_COMPRESSION`` literal.
"""

from __future__ import annotations

import pytest
from django.test import override_settings


@pytest.fixture(autouse=True)
def _reset_djust_config():
    """Restore the shared ``config`` singleton after each test.

    These tests re-read settings into the singleton via ``config.reset()``
    inside an ``override_settings`` block; this fixture re-reads the
    *restored* settings afterwards so a ``DJUST_WS_COMPRESSION`` override
    does not leak into later tests via the singleton's ``_config``.
    """
    yield
    from djust.config import config

    config.reset()


def _fresh_config():
    """Re-read current Django settings into the shared ``config`` singleton.

    Must NOT ``importlib.reload(djust.config)`` — reloading the module
    rebinds ``djust.config.config`` to a brand-new object while every
    ``from djust.config import config`` consumer (e.g.
    ``djust.templatetags.live_tags``) keeps its reference to the OLD
    singleton. That orphaning silently breaks unrelated config-driven
    tests later in a serial run (issue #1794:
    ``auto_navigate`` <meta> went missing because ``live_tags`` read the
    stale singleton). ``config.reset()`` mutates the one shared singleton
    in place, achieving the same "re-pick-up settings" effect with no
    divergence.
    """
    from djust.config import config

    config.reset()
    return config


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
