"""
Tests for state backend configuration (#1354).

Covers:
- ``DJUST_CONFIG["STATE_BACKEND"]`` / ``["REDIS_URL"]`` select the backend.
- ``logger.warning`` fires when DEBUG=False and backend defaults to memory.

The top-level ``DJUST_STATE_BACKEND`` / ``DJUST_REDIS_URL`` aliases were removed
in 2.0; ``python/djust/tests/test_removed_settings_2_0.py`` covers the startup
error that replaced them.
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


class TestProductionFallbackWarning:
    """The in-memory fallback warns in production (#1354)."""

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
        with caplog.at_level(logging.WARNING, logger="djust.utils"):
            backend = get_backend()
        assert isinstance(backend, InMemoryStateBackend), (
            "Sanity: with no config and DEBUG=False should still default to memory"
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
