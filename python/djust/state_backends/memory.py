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

from .base import (
    StateBackend,
    DjustPerformanceWarning,
    DEFAULT_STATE_SIZE_WARNING_KB,
    large_state_warning_message,
    state_key_label,
)

logger = logging.getLogger(__name__)


def _coerce_ttl(value: Any) -> int:
    """``SESSION_TTL`` as an int (#3080).

    The TTL is now compared at construction and on every ``get()``, so a
    value read from the environment as a string must not fail every mount.
    Anything ``int()`` cannot take falls back to the one-hour default.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("InMemoryStateBackend: SESSION_TTL %r is not a number; using 3600 s", value)
        return 3600


class InMemoryStateBackend(StateBackend):
    """
    Thread-safe in-memory state backend for development and testing.

    Features:
    - Thread-safe access using RLock (reentrant lock)
    - State size monitoring and warnings
    - Automatic memory statistics tracking

    Expiry (#3080):
    - An entry not written for ``default_ttl`` seconds is expired: ``get()``
      treats it as a miss and drops it, and ``set()`` sweeps every expired
      entry at most once per ``min(default_ttl, 60)`` seconds. Before this,
      the TTL applied only when something called ``cleanup_expired()``, which
      nothing did at runtime, so every session's state stayed in memory for
      the life of the process.
    - ``default_ttl <= 0`` means "never expire" (no sweep, no expiry on get).

    Limitations:
    - Does not scale horizontally (single server only)
    - Data lost on server restart
    - Memory grows with the number of sessions seen within one TTL: each
      holds its view's full render state (hundreds of KB for a large page).

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
        # A serialized snapshot per key, never a live ``RustLiveView``: the
        # object a caller saves stays in use by that caller (it renders and
        # tears it down), so reading it from another thread raced it under
        # ``worker_threads``. Bytes are immutable, so readers may share them.
        self._cache: Dict[str, Tuple[bytes, float]] = {}
        self._state_sizes: Dict[str, int] = {}  # Track state sizes for monitoring
        # Keys the ``Large LiveView state`` warning already fired for, so the
        # per-render ``refresh()`` reports a view once, not once per event (#3632).
        # A key leaves the set when it is deleted, expires, or drops back under
        # the threshold, so growing past it again is reported again.
        self._warned_large: set[str] = set()
        self._default_ttl = _coerce_ttl(default_ttl)
        self._state_size_warning_kb = state_size_warning_kb
        self._lock = RLock()  # Reentrant lock for thread safety
        self._observations: Dict[str, Tuple[int, float]] = {}
        # Amortised expiry sweep from set() (#3080): at most once per
        # interval, so a busy server pays one O(entries) pass a minute.
        self._sweep_interval = min(self._default_ttl, 60) if self._default_ttl > 0 else 0
        self._next_sweep = time.monotonic() + self._sweep_interval
        logger.info(
            f"InMemoryStateBackend initialized with TTL={default_ttl}s, "
            f"state_size_warning={state_size_warning_kb}KB"
        )

    def _register_observation(self, lifetime: str) -> None:
        now = time.monotonic()
        with self._lock:
            # Registration is the bounded cleanup opportunity for abandoned
            # menus; no per-observation timer/thread is created.
            self._observations = {
                key: value
                for key, value in self._observations.items()
                if value[1] == 0 or value[1] > now
            }
            expires = now + self._default_ttl if self._default_ttl > 0 else 0
            self._observations.setdefault(lifetime, (0, expires))

    def _claim_observation(self, lifetime: str, sequence: int) -> bool:
        with self._lock:
            record = self._observations.get(lifetime)
            if record is None:
                return False
            previous, expires = record
            if expires and expires <= time.monotonic():
                self._observations.pop(lifetime, None)
                return False
            if sequence <= previous:
                return False
            self._observations[lifetime] = (sequence, expires)
            return True

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

        The cache holds the ``serialize_msgpack`` bytes taken at ``set()``,
        not the saved object itself, which its caller goes on rendering and
        tearing down. Reading that object from another thread (a second
        socket of the same session mounting the same page, with
        ``worker_threads`` on) collided with the owner's render or teardown
        as ``Already (mutably) borrowed``. Like Redis, a reader now only
        deserializes bytes; no Rust object is shared.

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
                snapshot, timestamp = cached
                if self._default_ttl > 0 and timestamp < time.time() - self._default_ttl:
                    # Expired (#3080): a miss, and the entry goes now rather
                    # than waiting for the next sweep.
                    del self._cache[key]
                    self._state_sizes.pop(key, None)
                    self._warned_large.discard(key)
                    return None

            # Deserialize outside the lock: purely CPU work on immutable
            # bytes; holding the cache lock across it would serialize all
            # gets unnecessarily.
            try:
                clone = RustLiveView.deserialize_msgpack(snapshot)
            except Exception:
                # Round-trip failed (msgpack schema drift after a hot-swap,
                # corrupt payload, etc). Returning the shared ref is
                # silently corrupting — two concurrent connections to the
                # same view would mutate the SAME `_rust_view` and leak
                # state across each other (#1410). The strictly-safer
                # alternative is to fail the cache-hit and let the caller
                # treat this as uninitialized — `mount()` will run again
                # and rebuild clean state.
                #
                # Discard the corrupt entry under the lock so the next
                # caller doesn't re-trip the same exception. Identity-
                # guard the pop: between the unlock above and the
                # re-lock here, a concurrent `set(key, new_view)` could
                # have landed a fresh, valid entry — we must not delete
                # the *new* one in place of the corrupt one we held.
                # `cached`'s reference to `snapshot` keeps the original
                # alive, so `is` is sound.
                with self._lock:
                    current = self._cache.get(key)
                    if current is not None and current[0] is snapshot:
                        self._cache.pop(key, None)
                        self._state_sizes.pop(key, None)
                        self._warned_large.discard(key)
                logger.exception(
                    "InMemoryStateBackend.get: serialize/deserialize round-trip "
                    "failed for %s; entry discarded — caller should remount",
                    state_key_label(key),
                )
                return None
            return (clone, timestamp)

    def set(
        self,
        key: str,
        view: RustLiveView,
        ttl: Optional[int] = None,
        warn_on_large_state: bool = True,
    ) -> None:
        """
        Store a snapshot of ``view`` with a timestamp (thread-safe).

        The view is serialized now, on the caller's thread, which owns it;
        the backend keeps the bytes and no reference to the object, so the
        caller may go on rendering and tearing it down while other callers
        read the key. Later changes to ``view`` are not stored, as with the
        Redis backend. Optionally tracks state size and emits warnings for
        large states.

        Args:
            key: Session key
            view: RustLiveView instance to store
            ttl: Time-to-live in seconds (unused for in-memory, kept for API compatibility)
            warn_on_large_state: Whether to emit warnings for large states
        """
        timestamp = time.time()

        try:
            snapshot = view.serialize_msgpack()
        except Exception as exc:  # noqa: BLE001 - a lost cache entry costs a fresh mount
            from djust._exposure_diagnostics import log_failure_for

            # Nothing valid to store: drop what the key held, so a stale
            # snapshot is not served in its place.
            with self._lock:
                self._cache.pop(key, None)
                self._state_sizes.pop(key, None)
                self._warned_large.discard(key)
            log_failure_for(
                logger,
                (),
                exc,
                "InMemoryStateBackend.set: could not snapshot %s",
                state_key_label(key),
            )
            return

        state_size = self._state_size(key, view, snapshot)
        over = state_size > self._state_size_warning_kb * 1024

        # Store first, warn last: a ``-W error`` filter makes ``warnings.warn``
        # raise, and the entry and its warned flag must agree when it does (a
        # flag with no entry could never be cleared by ``delete``).
        with profiler.profile(profiler.OP_STATE_SAVE):
            with self._lock:
                self._cache[key] = (snapshot, timestamp)
                if state_size > 0:
                    self._state_sizes[key] = state_size
                if over and warn_on_large_state:
                    self._warned_large.add(key)
                elif not over:
                    self._warned_large.discard(key)
        self._maybe_sweep()

        # Warn about large states
        if over and warn_on_large_state:
            self._emit_large_warning(key, state_size, snapshot)

    def _state_size(self, key: str, view: RustLiveView, snapshot: bytes) -> int:
        """The size ``set()`` measures for ``view`` (0 when it cannot be estimated)."""
        try:
            if hasattr(view, "get_state_size"):
                return int(view.get_state_size())
            return len(snapshot)
        except Exception:
            logger.debug("Failed to estimate state size for %s", state_key_label(key))
            return 0

    def _emit_large_warning(self, key: str, size: int, snapshot: bytes) -> None:
        warnings.warn(
            large_state_warning_message(key, size, self._state_size_warning_kb, snapshot),
            DjustPerformanceWarning,
            stacklevel=3,
        )

    def refresh(self, key: str, view: RustLiveView, warn_on_large_state: bool = True) -> None:
        """Re-store ``view``'s snapshot after its owner rendered it.

        The backend keeps bytes, not the owner's object, so this is how the
        cached state follows the owner's renders (the diff baseline the next
        request of the page starts from). Only an existing entry is updated,
        and its timestamp is kept: a refresh is not a new save.

        Reports a view that grew past the warning threshold during its life
        (#3632), once per key while it stays over, unless
        ``warn_on_large_state`` is False. This runs in the owner's render path,
        so a warning that a filter turns into an exception (``-W error``) is
        logged instead of raised here, after the entry is already updated.
        """
        try:
            snapshot = view.serialize_msgpack()
        except Exception as exc:  # noqa: BLE001 - a lost cache entry costs a fresh mount
            from djust._exposure_diagnostics import log_failure_for

            with self._lock:
                self._cache.pop(key, None)
                self._state_sizes.pop(key, None)
                self._warned_large.discard(key)
            log_failure_for(
                logger,
                (),
                exc,
                "InMemoryStateBackend.refresh: could not snapshot %s",
                state_key_label(key),
            )
            return
        with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                return
            self._cache[key] = (snapshot, cached[1])
            threshold = self._state_size_warning_kb * 1024
            size = len(snapshot)
            self._state_sizes[key] = size  # ``get_stats`` follows the renders too
            if size <= threshold:
                self._warned_large.discard(key)
                return
            if not warn_on_large_state or key in self._warned_large:
                return
            self._warned_large.add(key)
        # A view that GREW past the threshold during its life (#3632): ``set()``
        # saw it at creation, when it was small. The snapshot is already in hand,
        # so this costs ``len()`` and a set lookup per render, and the breakdown
        # is built only on the render that crosses the line.
        try:
            self._emit_large_warning(key, size, snapshot)
        except DjustPerformanceWarning:
            logger.warning(
                "%s",
                large_state_warning_message(key, size, self._state_size_warning_kb, snapshot),
            )

    def _maybe_sweep(self) -> None:
        """Drop expired entries, at most once per sweep interval (#3080)."""
        if self._sweep_interval <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if now < self._next_sweep:
                return
            self._next_sweep = now + self._sweep_interval
        removed = self._remove_expired(self._default_ttl)
        if removed:
            logger.debug("Expired %s in-memory sessions older than %ss", removed, self._default_ttl)

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
                self._warned_large.discard(key)
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

        removed = self._remove_expired(ttl)
        if removed:
            logger.info("Cleaned up %s expired sessions from memory", removed)
        return removed

    def _remove_expired(self, ttl: int) -> int:
        """Delete entries not written for ``ttl`` seconds; return how many."""
        cutoff = time.time() - ttl
        with self._lock:
            expired_keys = [
                key for key, (_, timestamp) in self._cache.items() if timestamp < cutoff
            ]
            for key in expired_keys:
                del self._cache[key]
                self._state_sizes.pop(key, None)
                self._warned_large.discard(key)
        return len(expired_keys)

    def delete_all(self) -> int:
        """Delete every session unconditionally (used by ``djust clear --all``)."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._state_sizes.clear()
            self._warned_large.clear()
            self._observations.clear()
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
                # Test write. The probe value's view slot is None (never read
                # as a RustLiveView — it is popped a few lines below), so the
                # cache's declared (RustLiveView, float) value type doesn't hold
                # for this transient health-check entry.
                self._cache[test_key] = (None, time.time())  # type: ignore[assignment]

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
