"""
Tests for the rate limiting system.

Covers:
- TokenBucket unit tests
- ConnectionRateLimiter (global + per-handler)
- IPConnectionTracker
- Configurable limits
- Per-event-type overrides via @rate_limit decorator
"""

import time
from unittest.mock import patch

import pytest

from djust.rate_limit import (
    ConnectionRateLimiter,
    IPConnectionTracker,
    TokenBucket,
    get_rate_limit_settings,
)


# ============================================================================
# TokenBucket unit tests
# ============================================================================


class TestTokenBucket:
    def test_initial_burst_capacity(self):
        """Bucket starts full — burst tokens available immediately."""
        bucket = TokenBucket(rate=10, burst=5)
        for _ in range(5):
            assert bucket.consume() is True
        assert bucket.consume() is False

    def test_refill_over_time(self):
        """Tokens refill based on elapsed time and rate."""
        bucket = TokenBucket(rate=100, burst=10)
        # Drain all tokens
        for _ in range(10):
            bucket.consume()
        assert bucket.consume() is False

        # Simulate time passing (0.05s at rate=100 => 5 tokens)
        bucket.last_refill = time.monotonic() - 0.05
        for _ in range(5):
            assert bucket.consume() is True
        assert bucket.consume() is False

    def test_burst_caps_tokens(self):
        """Tokens never exceed burst capacity even after long idle."""
        bucket = TokenBucket(rate=100, burst=3)
        bucket.last_refill = time.monotonic() - 100  # Long idle
        # Should only have 3 (burst cap), not 10000
        for _ in range(3):
            assert bucket.consume() is True
        assert bucket.consume() is False

    def test_zero_rate(self):
        """With rate=0, tokens never refill after burst is exhausted."""
        bucket = TokenBucket(rate=0, burst=2)
        assert bucket.consume() is True
        assert bucket.consume() is True
        bucket.last_refill = time.monotonic() - 10
        assert bucket.consume() is False


# ============================================================================
# ConnectionRateLimiter tests
# ============================================================================


class TestConnectionRateLimiter:
    def test_global_limit(self):
        """Global bucket rejects events after burst is exhausted."""
        limiter = ConnectionRateLimiter(rate=0, burst=3, max_warnings=10)
        assert limiter.check("click") is True
        assert limiter.check("click") is True
        assert limiter.check("click") is True
        assert limiter.check("click") is False

    def test_warnings_count_up(self):
        limiter = ConnectionRateLimiter(rate=0, burst=1, max_warnings=3)
        limiter.check("a")  # consumes the one token
        limiter.check("a")  # rejected => warning 1
        limiter.check("a")  # warning 2
        assert limiter.warnings == 2
        assert limiter.should_disconnect() is False
        limiter.check("a")  # warning 3
        assert limiter.should_disconnect() is True

    def test_per_handler_limit(self):
        """Per-handler bucket is independent from global bucket."""
        limiter = ConnectionRateLimiter(rate=1000, burst=100, max_warnings=10)
        limiter.register_handler_limit("keydown", rate=0, burst=2)

        assert limiter.check_handler("keydown") is True
        assert limiter.check_handler("keydown") is True
        assert limiter.check_handler("keydown") is False

        # Global bucket still fine
        assert limiter.check("keydown") is True

    def test_unregistered_handler_always_passes(self):
        limiter = ConnectionRateLimiter(rate=1000, burst=100)
        assert limiter.check_handler("unknown_event") is True

    def test_different_event_types_independent(self):
        """Different handlers get separate buckets."""
        limiter = ConnectionRateLimiter(rate=1000, burst=100)
        limiter.register_handler_limit("click", rate=0, burst=1)
        limiter.register_handler_limit("keydown", rate=0, burst=1)

        assert limiter.check_handler("click") is True
        assert limiter.check_handler("click") is False
        # keydown still has its token
        assert limiter.check_handler("keydown") is True


# ============================================================================
# IPConnectionTracker tests
# ============================================================================


class TestIPConnectionTracker:
    def test_connection_limit(self):
        tracker = IPConnectionTracker()
        assert tracker.connect("1.2.3.4", max_per_ip=2) is True
        assert tracker.connect("1.2.3.4", max_per_ip=2) is True
        assert tracker.connect("1.2.3.4", max_per_ip=2) is False

    def test_disconnect_frees_slot(self):
        tracker = IPConnectionTracker()
        tracker.connect("1.2.3.4", max_per_ip=1)
        assert tracker.connect("1.2.3.4", max_per_ip=1) is False
        tracker.disconnect("1.2.3.4")
        assert tracker.connect("1.2.3.4", max_per_ip=1) is True

    def test_cooldown_blocks_reconnect(self):
        tracker = IPConnectionTracker()
        tracker.add_cooldown("1.2.3.4", seconds=10)
        assert tracker.connect("1.2.3.4", max_per_ip=100) is False

    def test_different_ips_independent(self):
        tracker = IPConnectionTracker()
        tracker.connect("1.1.1.1", max_per_ip=1)
        assert tracker.connect("2.2.2.2", max_per_ip=1) is True

    def test_disconnect_below_zero(self):
        """Disconnecting when count is 0 shouldn't crash."""
        tracker = IPConnectionTracker()
        tracker.disconnect("9.9.9.9")  # no-op, shouldn't raise


# ============================================================================
# get_rate_limit_settings helper
# ============================================================================


class TestGetRateLimitSettings:
    def test_returns_none_for_undecorated(self):
        def handler():
            pass
        assert get_rate_limit_settings(handler) is None

    def test_returns_settings_from_decorator_metadata(self):
        def handler():
            pass
        handler._djust_decorators = {"rate_limit": {"rate": 5, "burst": 3}}
        result = get_rate_limit_settings(handler)
        assert result == {"rate": 5, "burst": 3}


# ============================================================================
# Integration-style tests (simulated consumer behavior)
# ============================================================================


class TestRateLimitIntegration:
    """Tests that mirror how the WebSocket consumer uses the rate limiter."""

    def test_global_rate_limit_then_disconnect(self):
        """After max_warnings, should_disconnect returns True."""
        limiter = ConnectionRateLimiter(rate=0, burst=0, max_warnings=2)
        # Every check fails (0 burst), each failure increments warnings
        limiter.check("ev1")  # warning 1
        limiter.check("ev2")  # warning 2
        assert limiter.should_disconnect() is True

    def test_handler_rate_limit_contributes_to_warnings(self):
        """Per-handler rejection also increments warning count."""
        limiter = ConnectionRateLimiter(rate=1000, burst=100, max_warnings=2)
        limiter.register_handler_limit("fast_event", rate=0, burst=0)
        limiter.check_handler("fast_event")  # warning 1
        limiter.check_handler("fast_event")  # warning 2
        assert limiter.should_disconnect() is True

    def test_mixed_global_and_handler_warnings(self):
        """Global and handler warnings share the same counter."""
        limiter = ConnectionRateLimiter(rate=0, burst=0, max_warnings=3)
        limiter.check("a")  # global warning 1
        limiter.register_handler_limit("b", rate=0, burst=0)
        limiter.check_handler("b")  # handler warning 2
        assert limiter.should_disconnect() is False
        limiter.check("c")  # global warning 3
        assert limiter.should_disconnect() is True

    def test_configurable_defaults(self):
        """Rate/burst/max_warnings can be configured per connection."""
        limiter = ConnectionRateLimiter(rate=50, burst=10, max_warnings=5)
        assert limiter.global_bucket.rate == 50
        assert limiter.global_bucket.burst == 10
        assert limiter.max_warnings == 5
