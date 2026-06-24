"""
Server-side rate limiting for WebSocket events.

Uses a token bucket algorithm: tokens refill at a steady rate, and each event
consumes one token. Burst capacity allows short bursts of activity.
"""

import threading
import time
import logging
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from .security.log_sanitizer import sanitize_for_log

logger = logging.getLogger(__name__)

# Monotonic clock seam. Aliased once at module load so the token-bucket refill
# math reads through a single indirection. Production behavior is identical to
# calling ``time.monotonic()`` directly; the seam exists purely so tests can
# OWN THE CLOCK — patch this name with a controllable fake instead of the global
# ``time`` module — and assert burst-exhaustion deterministically (no wall-clock
# refill flake under CPU-saturated parallel runs). See tests/unit/test_event_security.py.
_monotonic = time.monotonic


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
        self.last_refill = _monotonic()

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = _monotonic()
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

    def __init__(self) -> None:
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


def get_rate_limit_settings(handler: Any) -> Optional[dict]:
    """
    Get rate limit settings from a handler's @rate_limit decorator metadata.

    Returns dict with 'rate' and 'burst' keys, or None if not decorated.
    """
    decorators = getattr(handler, "_djust_decorators", {})
    return decorators.get("rate_limit")


# --------------------------------------------------------------------------- #
# Shared per-caller @rate_limit store (F27 + F28, ADR-008)
# --------------------------------------------------------------------------- #
#
# The per-handler ``@rate_limit`` decorator is meant to throttle a *caller*'s
# invocation rate of a specific handler (abuse prevention for OTP/email sends,
# expensive compute, brute-forceable actions, …). Historically each transport
# enforced it against an INDEPENDENT bucket store:
#
#   * WS  — the per-connection :class:`ConnectionRateLimiter.handler_buckets`,
#           so N WebSocket connections gave N× the configured limit (F27).
#   * SSE — the per-session limiter (same per-connection class).
#   * API — a process-level dict in ``api/dispatch.py``.
#
# A caller could therefore sum allowances across both connection count and
# transport. This module-level store is the SINGLE source of truth for the
# per-handler ``@rate_limit``: one ``(caller_key, handler_name)`` bucket shared
# by all three transports, so a given caller has ONE budget per handler
# regardless of connection count or transport.
#
# NOTE: this does NOT replace the per-connection ``ConnectionRateLimiter``'s
# *global* per-message abuse-disconnect (#17 — ``check()`` / ``check_upload()``
# / ``should_disconnect()``). Connection-flood control is legitimately
# per-connection; only the per-HANDLER ``@rate_limit`` is unified here.

_HANDLER_BUCKET_CAP = 10_000  # LRU cap: evict oldest entry when full to bound memory.
_handler_buckets: "OrderedDict[Tuple[str, str], TokenBucket]" = OrderedDict()
_handler_buckets_lock = threading.Lock()


def caller_key(request: Any, client_ip: Optional[str] = None) -> str:
    """Stable per-caller identity for the shared per-handler ``@rate_limit``.

    Mirrors the SSE owner-principal identity model (Findings #24/#25) so a
    caller is keyed consistently across WS, SSE, and the HTTP API:

        * authenticated caller  -> ``user:<pk>``
        * anonymous w/ session  -> ``session:<session_key>``
        * otherwise             -> ``ip:<client_ip>``

    Args:
        request: The view's ``request`` (carries ``.user`` and ``.session``),
            or ``None`` when no request is available.
        client_ip: The already-:func:`djust._client_ip.resolve_client_ip`-resolved
            client IP (WS/SSE resolve this at connect; the HTTP API resolves it
            from the request). Used only for the anonymous-no-session fallback.

    The IP fallback uses the *resolved* client IP — never a raw ``REMOTE_ADDR``
    — so ``DJUST_TRUSTED_PROXY_COUNT`` governs the API limiter identically to
    the live transports (closes F28: no shared-proxy bucket, no XFF spoof).
    """
    user = getattr(request, "user", None) if request is not None else None
    if user is not None and getattr(user, "is_authenticated", False):
        pk = getattr(user, "pk", None)
        if pk is not None:
            return f"user:{pk}"
    session = getattr(request, "session", None) if request is not None else None
    session_key = getattr(session, "session_key", None) if session is not None else None
    if session_key:
        return f"session:{session_key}"
    return f"ip:{client_ip or 'unknown'}"


def handler_rate_check(caller: str, handler_name: str, settings: Optional[dict]) -> bool:
    """Per-caller token-bucket check for a handler's ``@rate_limit`` settings.

    The SINGLE source of truth shared by WS, SSE, and the HTTP API. Buckets are
    process-level and keyed on ``(caller, handler_name)`` so a caller's budget
    for a handler is shared across every connection and transport.

    Args:
        caller: A :func:`caller_key` result.
        handler_name: The event/handler name.
        settings: The handler's ``@rate_limit`` settings dict
            (``{"rate": …, "burst": …}``) or ``None`` when the handler is not
            decorated.

    Returns:
        True if allowed (or no per-handler limit), False if rate-limited.
    """
    if not settings:
        return True
    key = (caller, handler_name)
    with _handler_buckets_lock:
        bucket = _handler_buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(rate=settings["rate"], burst=settings["burst"])
            _handler_buckets[key] = bucket
            # LRU eviction: cap the dict so a hostile caller cycling identities
            # cannot inflate memory without bound.
            while len(_handler_buckets) > _HANDLER_BUCKET_CAP:
                _handler_buckets.popitem(last=False)
        else:
            # Touch for LRU order.
            _handler_buckets.move_to_end(key)
    return bucket.consume()


def reset_handler_buckets() -> None:
    """Clear the shared per-caller @rate_limit state — used by tests."""
    with _handler_buckets_lock:
        _handler_buckets.clear()
