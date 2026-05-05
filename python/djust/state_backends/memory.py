"""
In-memory state backend for development and testing.
"""

import time
import logging
import warnings
from threading import RLock
from typing import Optional, Dict, Any, Tuple
from djust._rust import RustLiveView
from djust.profiler import profiler

from .base import StateBackend, DjustPerformanceWarning, DEFAULT_STATE_SIZE_WARNING_KB

logger = logging.getLogger(__name__)


class InMemoryStateBackend(StateBackend):
    """
    Thread-safe in-memory state backend for development and testing.

    Features:
    - Thread-safe access using RLock (reentrant lock)
    - State size monitoring and warnings
    - Automatic memory statistics tracking

    Limitations:
    - Does not scale horizontally (single server only)
    - Data lost on server restart
    - Potential memory growth without cleanup

    Suitable for:
    - Development environments
    - Single-server deployments with < 1000 concurrent users
    - Testing

    For production with horizontal scaling, use RedisStateBackend.
    """

    def __init__(
        self,
        default_ttl: int = 3600,
        state_size_warning_kb: int = DEFAULT_STATE_SIZE_WARNING_KB,
    ):
        """
        Initialize thread-safe in-memory backend.

        Args:
            default_ttl: Default session TTL in seconds (default: 1 hour)
            state_size_warning_kb: Emit warning when state exceeds this size in KB
        """
        self._cache: Dict[str, Tuple[RustLiveView, float]] = {}
        self._state_sizes: Dict[str, int] = {}  # Track state sizes for monitoring
        self._default_ttl = default_ttl
        self._state_size_warning_kb = state_size_warning_kb
        self._lock = RLock()  # Reentrant lock for thread safety
        logger.info(
            f"InMemoryStateBackend initialized with TTL={default_ttl}s, "
            f"state_size_warning={state_size_warning_kb}KB"
        )

    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        """
        Retrieve from in-memory cache and return an isolated copy
        (thread-safe).

        Backend contract (#1353): ``get()`` MUST return a ``RustLiveView``
        instance that the caller can safely mutate without racing with
        other callers. This mirrors how :class:`RedisStateBackend.get`
        already behaves — every Redis read is a fresh
        ``RustLiveView.deserialize_msgpack`` call, so two concurrent
        readers get two distinct Python objects.

        Previously this method returned the cached Python reference
        directly. When two HTTP requests for the same
        ``(session, view_path)`` pair landed on the in-memory backend
        concurrently, both call sites then mutated the same Rust view
        object via ``update_state`` / ``mark_safe_keys`` /
        ``set_changed_keys`` / ``set_template_dirs``, racing inside
        Rust's ``RefCell::borrow_mut`` and surfacing as
        ``RuntimeError: Already borrowed`` (NYC Claims observed 17.5%
        500-rate at concurrency 2). The race fired anywhere two
        ``&mut self`` Rust methods overlapped in time on the shared
        view — including ``render()`` which yields the GIL inside an
        active mutable borrow via the ``Context::resolve_dotted_via_getattr``
        sidecar fallback path.

        We use ``serialize_msgpack`` / ``deserialize_msgpack`` for
        cloning because (a) ``RustLiveView`` is a Rust extension type
        that doesn't expose a Python ``__copy__`` / ``__deepcopy__``,
        and (b) the round-trip already exists for Redis storage and is
        battle-tested. Note that ``template_dirs``, ``last_html``,
        ``last_render_timing``, ``node_html_cache`` (transient render
        caches) and ``raw_py_values`` (Python references) are not
        carried across the round-trip — callers re-populate the
        template dirs via ``set_template_dirs`` and the rest are
        rebuilt on the next render.

        Args:
            key: Session key to retrieve

        Returns:
            Tuple of (RustLiveView, timestamp) if found, None otherwise.
            The returned view is a fresh deserialize — mutating it does
            not affect other callers or the cached canonical state.
        """
        with profiler.profile(profiler.OP_STATE_LOAD):
            with self._lock:
                cached = self._cache.get(key)
                if cached is None:
                    return None
                view, timestamp = cached

            # Round-trip outside the lock: serialize/deserialize is
            # purely CPU work on independent bytes; holding the cache
            # lock across it would serialize all gets unnecessarily.
            try:
                serialized = view.serialize_msgpack()
                clone = RustLiveView.deserialize_msgpack(serialized)
            except Exception:
                # Defensive: if msgpack round-trip fails for any reason,
                # fall back to returning the cached ref. The race window
                # is preferable to an outright cache miss that destroys
                # the diff baseline. Logged so this surfaces in dev.
                logger.exception(
                    "InMemoryStateBackend.get: serialize/deserialize round-trip "
                    "failed for key '%s'; returning shared ref (race risk)",
                    key,
                )
                return (view, timestamp)
            return (clone, timestamp)

    def set(
        self,
        key: str,
        view: RustLiveView,
        ttl: Optional[int] = None,
        warn_on_large_state: bool = True,
    ):
        """
        Store in in-memory cache with timestamp (thread-safe).

        Optionally tracks state size and emits warnings for large states.

        Args:
            key: Session key
            view: RustLiveView instance to store
            ttl: Time-to-live in seconds (unused for in-memory, kept for API compatibility)
            warn_on_large_state: Whether to emit warnings for large states
        """
        timestamp = time.time()

        # Estimate state size if the view supports it
        state_size = 0
        try:
            if hasattr(view, "get_state_size"):
                state_size = view.get_state_size()
            elif hasattr(view, "serialize_msgpack"):
                # Fallback: serialize to get size (more expensive)
                state_size = len(view.serialize_msgpack())
        except Exception:
            logger.debug("Failed to estimate state size for key '%s'", key)

        # Warn about large states
        if warn_on_large_state and state_size > self._state_size_warning_kb * 1024:
            warnings.warn(
                f"Large LiveView state detected for '{key}': {state_size / 1024:.1f}KB "
                f"(threshold: {self._state_size_warning_kb}KB). "
                "Consider using temporary_assigns or streams to reduce memory usage. "
                "See: https://djust.org/docs/optimization/temporary-assigns",
                DjustPerformanceWarning,
                stacklevel=3,
            )

        with profiler.profile(profiler.OP_STATE_SAVE):
            with self._lock:
                self._cache[key] = (view, timestamp)
                if state_size > 0:
                    self._state_sizes[key] = state_size

    def get_and_update(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        """
        Atomically retrieve and update timestamp (thread-safe).

        This is useful for extending session TTL on access without
        separate get/set calls that could race.

        Args:
            key: Session key

        Returns:
            Tuple of (RustLiveView, new_timestamp) if found, None otherwise
        """
        with self._lock:
            cached = self._cache.get(key)
            if cached:
                view, _ = cached
                new_timestamp = time.time()
                self._cache[key] = (view, new_timestamp)
                return (view, new_timestamp)
            return None

    def delete(self, key: str) -> bool:
        """
        Remove from in-memory cache (thread-safe).

        Args:
            key: Session key to delete

        Returns:
            True if session was deleted, False if not found
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._state_sizes.pop(key, None)
                return True
            return False

    def cleanup_expired(self, ttl: Optional[int] = None) -> int:
        """
        Clean up expired sessions from memory (thread-safe).

        Args:
            ttl: Time-to-live threshold in seconds (default: backend default)

        Returns:
            Number of sessions cleaned up
        """
        if ttl is None:
            ttl = self._default_ttl

        # TTL=0 means "never expire" — skip cleanup entirely.
        # Without this guard, cutoff equals time.time() and every session
        # (whose timestamp is always in the past) gets deleted immediately,
        # which breaks all event handling because there is no state to patch.
        if ttl <= 0:
            return 0

        cutoff = time.time() - ttl

        with self._lock:
            expired_keys = [
                key for key, (_, timestamp) in self._cache.items() if timestamp < cutoff
            ]

            for key in expired_keys:
                del self._cache[key]
                self._state_sizes.pop(key, None)

        if expired_keys:
            logger.info("Cleaned up %s expired sessions from memory", len(expired_keys))

        return len(expired_keys)

    def delete_all(self) -> int:
        """Delete every session unconditionally (used by ``djust clear --all``)."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._state_sizes.clear()
        if count:
            logger.info("Deleted all %s sessions from memory", count)
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get in-memory cache statistics (thread-safe)."""
        with self._lock:
            if not self._cache:
                return {
                    "backend": "memory",
                    "total_sessions": 0,
                    "oldest_session_age": 0,
                    "newest_session_age": 0,
                    "average_age": 0,
                    "thread_safe": True,
                }

            current_time = time.time()
            ages = [current_time - timestamp for _, timestamp in self._cache.values()]

            return {
                "backend": "memory",
                "total_sessions": len(self._cache),
                "oldest_session_age": max(ages) if ages else 0,
                "newest_session_age": min(ages) if ages else 0,
                "average_age": sum(ages) / len(ages) if ages else 0,
                "thread_safe": True,
            }

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get detailed memory usage statistics (thread-safe).

        Returns:
            Dictionary with memory metrics including total size,
            average size, and the largest sessions.
        """
        with self._lock:
            if not self._state_sizes:
                return {
                    "backend": "memory",
                    "total_state_bytes": 0,
                    "average_state_bytes": 0,
                    "largest_sessions": [],
                    "sessions_tracked": 0,
                }

            total_bytes = sum(self._state_sizes.values())
            avg_bytes = total_bytes / len(self._state_sizes) if self._state_sizes else 0

            # Get top 10 largest sessions
            sorted_sessions = sorted(self._state_sizes.items(), key=lambda x: x[1], reverse=True)[
                :10
            ]

            return {
                "backend": "memory",
                "total_state_bytes": total_bytes,
                "total_state_kb": round(total_bytes / 1024, 2),
                "average_state_bytes": round(avg_bytes, 2),
                "average_state_kb": round(avg_bytes / 1024, 2),
                "largest_sessions": [
                    {"key": k, "size_bytes": s, "size_kb": round(s / 1024, 2)}
                    for k, s in sorted_sessions
                ],
                "sessions_tracked": len(self._state_sizes),
            }

    def health_check(self) -> Dict[str, Any]:
        """Check in-memory backend health (thread-safe)."""
        start_time = time.time()
        test_key = "__health_check__"

        try:
            with self._lock:
                # Test basic operations: check cache is accessible and operational
                # Test write
                self._cache[test_key] = (None, time.time())

                # Test read
                _ = self._cache.get(test_key)

                latency_ms = (time.time() - start_time) * 1000

                # Count sessions excluding test key
                total_sessions = len([k for k in self._cache.keys() if k != test_key])

                # Cleanup test key
                self._cache.pop(test_key, None)

            return {
                "status": "healthy",
                "backend": "memory",
                "latency_ms": round(latency_ms, 2),
                "total_sessions": total_sessions,
                "thread_safe": True,
            }

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error("InMemory health check failed: %s", e)

            with self._lock:
                # Count sessions excluding test key (in case it was partially written)
                total_sessions = len([k for k in self._cache.keys() if k != test_key])
                # Ensure test key is cleaned up
                self._cache.pop(test_key, None)

            return {
                "status": "unhealthy",
                "backend": "memory",
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
                "total_sessions": total_sessions,
                "thread_safe": True,
            }
