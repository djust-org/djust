"""
Server-side rate limiting for WebSocket events.

Uses a token bucket algorithm: tokens refill at a steady rate, and each event
consumes one token. Burst capacity allows short bursts of activity.
"""

import threading
import time
import logging
from typing import Dict, Optional

from .security.log_sanitizer import sanitize_for_log

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
        self.handler_buckets: Dict[str, TokenBucket] = {}
        self.warnings = 0
        self.max_warnings = max_warnings

    def check(self, event_name: str) -> bool:
        """
        Check if an event is allowed under global rate limit.

        Returns True if allowed, False if rate-limited.
        Per-handler limits are checked separately via check_handler().
        """
        if not self.global_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Rate limit exceeded for message '%s' (warning %d/%d)",
                sanitize_for_log(event_name),
                self.warnings,
                self.max_warnings,
            )
            return False

        return True

    def check_handler(self, event_name: str) -> bool:
        """
        Check per-handler rate limit bucket (if registered).

        Returns True if allowed or no per-handler limit exists.
        """
        handler_bucket = self.handler_buckets.get(event_name)
        if handler_bucket and not handler_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Per-handler rate limit exceeded for '%s' (warning %d/%d)",
                sanitize_for_log(event_name),
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


class IPConnectionTracker:
    """Process-level tracker for per-IP connection counts and reconnection cooldowns."""

    def __init__(self):
        self._connections: Dict[str, int] = {}
        self._cooldowns: Dict[str, float] = {}
        self._lock = threading.Lock()

    def connect(self, ip: str, max_per_ip: int) -> bool:
        """Try to register a connection. Returns False if limit reached or in cooldown."""
        with self._lock:
            now = time.monotonic()
            cooldown_until = self._cooldowns.get(ip, 0)
            if now < cooldown_until:
                return False
            self._cooldowns.pop(ip, None)
            count = self._connections.get(ip, 0)
            if count >= max_per_ip:
                return False
            self._connections[ip] = count + 1
            return True

    def disconnect(self, ip: str) -> None:
        with self._lock:
            count = self._connections.get(ip, 0)
            if count <= 1:
                self._connections.pop(ip, None)
            else:
                self._connections[ip] = count - 1

    def add_cooldown(self, ip: str, seconds: float) -> None:
        with self._lock:
            self._cooldowns[ip] = time.monotonic() + seconds


ip_tracker = IPConnectionTracker()


def get_rate_limit_settings(handler) -> Optional[dict]:
    """
    Get rate limit settings from a handler's @rate_limit decorator metadata.

    Returns dict with 'rate' and 'burst' keys, or None if not decorated.
    """
    decorators = getattr(handler, "_djust_decorators", {})
    return decorators.get("rate_limit")
