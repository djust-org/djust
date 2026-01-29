"""
Server-side rate limiting for WebSocket events.

Uses a token bucket algorithm: tokens refill at a steady rate, and each event
consumes one token. Burst capacity allows short bursts of activity.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    Token bucket rate limiter.

    Args:
        rate: Tokens added per second.
        burst: Maximum tokens (bucket capacity).
    """

    __slots__ = ("rate", "burst", "tokens", "last_refill")

    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class ConnectionRateLimiter:
    """
    Per-connection rate limiter with a global bucket and per-handler buckets.

    Args:
        rate: Global tokens per second (default from config).
        burst: Global burst capacity (default from config).
        max_warnings: Warnings before disconnect (default from config).
    """

    def __init__(
        self,
        rate: float = 100,
        burst: int = 20,
        max_warnings: int = 3,
    ):
        self.global_bucket = TokenBucket(rate, burst)
        self.handler_buckets: dict[str, TokenBucket] = {}
        self.warnings = 0
        self.max_warnings = max_warnings

    def check(self, event_name: str) -> bool:
        """
        Check if an event is allowed under rate limits.

        Returns True if allowed, False if rate-limited.
        """
        if not self.global_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Rate limit exceeded for event '%s' (warning %d/%d)",
                event_name,
                self.warnings,
                self.max_warnings,
            )
            return False

        # Check per-handler bucket if one exists
        handler_bucket = self.handler_buckets.get(event_name)
        if handler_bucket and not handler_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Per-handler rate limit exceeded for '%s' (warning %d/%d)",
                event_name,
                self.warnings,
                self.max_warnings,
            )
            return False

        return True

    def should_disconnect(self) -> bool:
        """True if the connection has exceeded the max warning threshold."""
        return self.warnings >= self.max_warnings

    def register_handler_limit(self, event_name: str, rate: float, burst: int) -> None:
        """Register a per-handler rate limit (from @rate_limit decorator)."""
        self.handler_buckets[event_name] = TokenBucket(rate, burst)


def get_rate_limit_settings(handler) -> Optional[dict]:
    """
    Get rate limit settings from a handler's @rate_limit decorator metadata.

    Returns dict with 'rate' and 'burst' keys, or None if not decorated.
    """
    decorators = getattr(handler, "_djust_decorators", {})
    return decorators.get("rate_limit")
