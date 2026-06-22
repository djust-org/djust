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
    Per-connection rate limiter with a global bucket, per-handler buckets, and
    a dedicated higher-ceiling bucket for binary upload frames.

    Args:
        rate: Global tokens per second (default from config).
        burst: Global burst capacity (default from config).
        max_warnings: Warnings before disconnect (default from config).
        upload_rate: Upload-frame tokens per second. Legitimate uploads are
            high-volume (a 10 MB file is ~157 64 KB chunk frames), so this bucket
            is intentionally larger than the general per-message limit — but it
            MUST exist so a binary-frame flood still trips ``should_disconnect()``
            (#F17). Defaults to a higher ceiling than the global limit.
        upload_burst: Upload-frame burst capacity. Sized to let a full
            single-file upload land as one burst without throttling legit
            throughput, while a sustained flood depletes the bucket and warns.
    """

    def __init__(
        self,
        rate: float = 100,
        burst: int = 20,
        max_warnings: int = 3,
        upload_rate: float = 200,
        upload_burst: int = 400,
    ):
        self.global_bucket = TokenBucket(rate, burst)
        self.handler_buckets: Dict[str, TokenBucket] = {}
        # Dedicated bucket for binary upload frames (#F17). Without it, upload
        # frames early-return before the global gate and so are never throttled
        # nor counted toward the abuse-disconnect — the exact frame class an
        # attacker would flood with. Higher ceiling than the global bucket so
        # legitimate high-volume uploads are not throttled to nothing.
        self.upload_bucket = TokenBucket(upload_rate, upload_burst)
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

    def check_upload(self) -> bool:
        """
        Check a binary upload frame against the dedicated upload bucket (#F17).

        Returns True if allowed, False if rate-limited. On failure this
        increments the shared warning counter exactly like ``check()`` /
        ``check_handler()``, so a sustained upload-frame flood trips
        ``should_disconnect()`` → ``close(4429)`` + cooldown — closing the
        bypass where binary upload frames were dispatched before the global
        rate gate and so were never counted toward the abuse-disconnect.
        """
        if not self.upload_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Upload-frame rate limit exceeded (warning %d/%d)",
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
