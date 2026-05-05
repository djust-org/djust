"""
Tests for state backend configuration via top-level Django settings (#1354).

Covers:
- Top-level ``DJUST_STATE_BACKEND="redis://..."`` (URL form) is honoured.
- Top-level ``DJUST_STATE_BACKEND="redis"`` + ``DJUST_REDIS_URL=...`` is honoured.
- ``DJUST_CONFIG["STATE_BACKEND"]`` regression guard (existing behaviour).
- ``logger.warning`` fires when DEBUG=False and backend defaults to memory.
"""

import logging
import sys
from unittest import mock

import pytest
from django.test import override_settings

from djust.state_backends.registry import _registry as _state_registry
from djust.state_backends import (
    InMemoryStateBackend,
    RedisStateBackend,
    get_backend,
)


@pytest.fixture(autouse=True)
def _reset_state_registry():
    """Reset the singleton between tests so each one re-reads settings."""
    _state_registry.reset()
    yield
    _state_registry.reset()


def _stub_redis_module():
    """Patch ``redis.from_url`` so RedisStateBackend can be constructed
    without a live Redis server. Returns the mock client."""
    fake_client = mock.MagicMock()
    fake_redis = mock.MagicMock()
    fake_redis.from_url = mock.MagicMock(return_value=fake_client)
    return mock.patch.dict(sys.modules, {"redis": fake_redis})


class TestTopLevelStateBackendSetting:
    """Top-level ``DJUST_STATE_BACKEND`` should be honoured (#1354)."""

    @override_settings(DJUST_STATE_BACKEND="redis://localhost:6379/0")
    def test_url_shaped_top_level_resolves_to_redis_backend(self):
        """``DJUST_STATE_BACKEND="redis://..."`` should produce RedisStateBackend.

        URL-shaped values are auto-split into ``backend_type="redis"`` plus
        ``REDIS_URL=<value>``.
        """
        with _stub_redis_module():
            # Strip DJUST_CONFIG so only the top-level setting is in play.
            with override_settings(DJUST_CONFIG={}):
                backend = get_backend()
        assert isinstance(backend, RedisStateBackend), (
            f"Expected RedisStateBackend from top-level DJUST_STATE_BACKEND URL, "
            f"got {type(backend).__name__}"
        )

    @override_settings(
        DJUST_STATE_BACKEND="redis",
        DJUST_REDIS_URL="redis://localhost:6379/0",
        DJUST_CONFIG={},
    )
    def test_top_level_pair_resolves_to_redis_backend(self):
        """``DJUST_STATE_BACKEND="redis"`` + ``DJUST_REDIS_URL`` work together.

        Two-setting form (no URL embedded in DJUST_STATE_BACKEND).
        """
        with _stub_redis_module():
            backend = get_backend()
        assert isinstance(backend, RedisStateBackend), (
            f"Expected RedisStateBackend from DJUST_STATE_BACKEND+DJUST_REDIS_URL, "
            f"got {type(backend).__name__}"
        )

    @override_settings(
        DEBUG=False,
        DJUST_CONFIG={},
    )
    def test_production_fallback_emits_warning(self, caplog):
        """When DEBUG=False and no backend config, emit a warning (#1354).

        Reasonable production deploy: forgets to set the state backend,
        falls back to in-memory, multi-replica deployments lose state.
        Logger should fire so this gets caught in startup logs.
        """
        # Make sure no top-level alias is set
        with override_settings(spec=[]):
            with caplog.at_level(logging.WARNING, logger="djust.utils"):
                backend = get_backend()
        assert isinstance(backend, InMemoryStateBackend), (
            "Sanity: with no config and no DEBUG=True should still default to memory"
        )
        # The warning should mention "in-memory" and "production"
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        warning_msgs = [r.getMessage() for r in warning_records]
        assert any("in-memory" in msg and "production" in msg.lower() for msg in warning_msgs), (
            "Expected a warning about in-memory + production fallback, "
            f"got messages: {warning_msgs}"
        )

    @override_settings(
        DEBUG=True,
        DJUST_CONFIG={},
    )
    def test_debug_true_no_warning(self, caplog):
        """DEBUG=True should NOT emit the production-fallback warning."""
        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            backend = get_backend()
        assert isinstance(backend, InMemoryStateBackend)
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        # Must NOT contain the production fallback warning
        assert not any(
            "in-memory" in msg and "production" in msg.lower() for msg in warning_msgs
        ), f"Should not warn under DEBUG=True; got: {warning_msgs}"


class TestDjustConfigRegression:
    """Existing ``DJUST_CONFIG`` form must keep working (regression guard)."""

    @override_settings(DJUST_CONFIG={"STATE_BACKEND": "memory"})
    def test_djust_config_memory_backend_works(self):
        """``DJUST_CONFIG["STATE_BACKEND"]="memory"`` returns InMemoryStateBackend."""
        backend = get_backend()
        assert isinstance(backend, InMemoryStateBackend)

    @override_settings(
        DJUST_CONFIG={
            "STATE_BACKEND": "redis",
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )
    def test_djust_config_redis_backend_works(self):
        """``DJUST_CONFIG["STATE_BACKEND"]="redis"`` returns RedisStateBackend."""
        with _stub_redis_module():
            backend = get_backend()
        assert isinstance(backend, RedisStateBackend)

    @override_settings(
        DJUST_CONFIG={"STATE_BACKEND": "redis", "REDIS_URL": "redis://djust-config:6379/0"},
        DJUST_STATE_BACKEND="redis://top-level-loses:6379/0",
    )
    def test_djust_config_wins_over_top_level(self):
        """When BOTH are set, ``DJUST_CONFIG`` wins (backwards-compatible)."""
        captured_url = {}

        def _capturing_from_url(url, *args, **kwargs):
            captured_url["url"] = url
            return mock.MagicMock()

        fake_redis = mock.MagicMock()
        fake_redis.from_url = _capturing_from_url
        with mock.patch.dict(sys.modules, {"redis": fake_redis}):
            backend = get_backend()
        assert isinstance(backend, RedisStateBackend)
        assert captured_url["url"] == "redis://djust-config:6379/0", (
            f"Expected DJUST_CONFIG REDIS_URL to win, got {captured_url}"
        )
