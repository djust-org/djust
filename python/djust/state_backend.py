"""
State backend system for djust LiveView.

Provides pluggable storage backends for LiveView state, enabling:
- In-memory caching for development
- Redis-backed storage for production horizontal scaling
- Custom backend implementations

Usage:
    # Configure in Django settings.py
    DJUST_CONFIG = {
        'STATE_BACKEND': 'redis',  # or 'memory'
        'REDIS_URL': 'redis://localhost:6379/0',
        'SESSION_TTL': 3600,  # 1 hour
    }
"""

import time
import logging
import warnings
from abc import ABC, abstractmethod
from threading import RLock
from typing import Optional, Dict, Any, Tuple
from djust._rust import RustLiveView
from djust.profiler import profiler

logger = logging.getLogger(__name__)

# Performance warning threshold (configurable via DJUST_CONFIG)
DEFAULT_STATE_SIZE_WARNING_KB = 100

# Compression settings
DEFAULT_COMPRESSION_THRESHOLD_KB = 10  # Compress states larger than this
COMPRESSION_MARKER = b'\x01'  # Prefix byte to indicate compressed data
NO_COMPRESSION_MARKER = b'\x00'  # Prefix byte for uncompressed data

# Try to import zstd for compression (optional dependency)
try:
    import zstandard as zstd
    ZSTD_AVAILABLE = True
    logger.debug("zstd compression available")
except ImportError:
    ZSTD_AVAILABLE = False
    logger.debug("zstd not available - install with: pip install zstandard")


class DjustPerformanceWarning(UserWarning):
    """Warning for potential performance issues in djust LiveViews."""
    pass


class StateBackend(ABC):
    """
    Abstract base class for LiveView state storage backends.

    Backends manage the lifecycle of RustLiveView instances, providing:
    - Persistent storage across requests
    - TTL-based session expiration
    - Statistics and monitoring
    """

    @abstractmethod
    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        """
        Retrieve a RustLiveView instance and its timestamp from storage.

        Args:
            key: Unique session key

        Returns:
            Tuple of (RustLiveView, timestamp) if found, None otherwise
        """
        pass

    @abstractmethod
    def set(self, key: str, view: RustLiveView, ttl: Optional[int] = None):
        """
        Store a RustLiveView instance with optional TTL.

        Args:
            key: Unique session key
            view: RustLiveView instance to store
            ttl: Time-to-live in seconds (None = use backend default)
        """
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """
        Remove a session from storage.

        Args:
            key: Unique session key

        Returns:
            True if session was deleted, False if not found
        """
        pass

    @abstractmethod
    def cleanup_expired(self, ttl: Optional[int] = None) -> int:
        """
        Remove expired sessions based on TTL.

        Args:
            ttl: Time-to-live threshold in seconds

        Returns:
            Number of sessions cleaned up
        """
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """
        Get backend statistics.

        Returns:
            Dictionary with metrics like total_sessions, oldest_age, etc.
        """
        pass

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """
        Check backend health and availability.

        Performs basic connectivity and operational tests to verify the backend
        is functioning correctly. Useful for monitoring and readiness probes.

        Returns:
            Dictionary with health status:
            - status (str): 'healthy' or 'unhealthy'
            - latency_ms (float): Response time in milliseconds
            - error (str, optional): Error message if unhealthy
            - details (dict, optional): Additional backend-specific info
        """
        pass

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get detailed memory usage statistics.

        Override in subclasses for backend-specific memory tracking.

        Returns:
            Dictionary with memory metrics:
            - total_state_bytes: Total bytes used for state storage
            - average_state_bytes: Average bytes per session
            - largest_sessions: List of (key, size_bytes) for largest sessions
        """
        return {
            "total_state_bytes": 0,
            "average_state_bytes": 0,
            "largest_sessions": [],
        }


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
        Retrieve from in-memory cache (thread-safe).

        Args:
            key: Session key to retrieve

        Returns:
            Tuple of (RustLiveView, timestamp) if found, None otherwise
        """
        with profiler.profile(profiler.OP_STATE_LOAD):
            with self._lock:
                return self._cache.get(key)

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
            if hasattr(view, 'get_state_size'):
                state_size = view.get_state_size()
            elif hasattr(view, 'serialize_msgpack'):
                # Fallback: serialize to get size (more expensive)
                state_size = len(view.serialize_msgpack())
        except Exception:
            pass  # Ignore errors in size estimation

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

        cutoff = time.time() - ttl

        with self._lock:
            expired_keys = [
                key for key, (_, timestamp) in self._cache.items()
                if timestamp < cutoff
            ]

            for key in expired_keys:
                del self._cache[key]
                self._state_sizes.pop(key, None)

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired sessions from memory")

        return len(expired_keys)

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
            sorted_sessions = sorted(
                self._state_sizes.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]

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
            logger.error(f"InMemory health check failed: {e}")

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


class RedisStateBackend(StateBackend):
    """
    Redis-backed state backend for production horizontal scaling.

    Benefits:
    - Horizontal scaling across multiple servers
    - Persistent state survives server restarts
    - Automatic TTL-based expiration
    - Native Rust serialization (5-10x faster, 30-40% smaller)
    - Optional zstd compression (60-80% size reduction for large states)

    Requirements:
    - Redis server running
    - redis-py package installed
    - zstandard package (optional, for compression)

    Usage:
        backend = RedisStateBackend(
            redis_url='redis://localhost:6379/0',
            default_ttl=3600,
            compression_enabled=True,  # Enable zstd compression
            compression_threshold_kb=10,  # Compress states > 10KB
        )
    """

    def __init__(
        self,
        redis_url: str,
        default_ttl: int = 3600,
        key_prefix: str = "djust:",
        compression_enabled: bool = True,
        compression_threshold_kb: int = DEFAULT_COMPRESSION_THRESHOLD_KB,
        compression_level: int = 3,
    ):
        """
        Initialize Redis backend with optional compression.

        Args:
            redis_url: Redis connection URL (e.g., 'redis://localhost:6379/0')
            default_ttl: Default session TTL in seconds (default: 1 hour)
            key_prefix: Prefix for all Redis keys (default: 'djust:')
            compression_enabled: Enable zstd compression (default: True)
            compression_threshold_kb: Compress states larger than this (default: 10KB)
            compression_level: zstd compression level 1-22 (default: 3, higher = slower but smaller)
        """
        try:
            import redis
        except ImportError:
            raise ImportError(
                "redis-py is required for RedisStateBackend. Install with: pip install redis"
            )

        self._client = redis.from_url(redis_url)
        self._default_ttl = default_ttl
        self._key_prefix = key_prefix

        # Compression settings
        self._compression_enabled = compression_enabled and ZSTD_AVAILABLE
        self._compression_threshold = compression_threshold_kb * 1024
        self._compression_level = compression_level

        # Initialize zstd compressor/decompressor if available
        if self._compression_enabled:
            self._compressor = zstd.ZstdCompressor(level=compression_level)
            self._decompressor = zstd.ZstdDecompressor()
        else:
            self._compressor = None
            self._decompressor = None

        if compression_enabled and not ZSTD_AVAILABLE:
            logger.warning(
                "Compression requested but zstandard not installed. "
                "Install with: pip install zstandard"
            )

        # Test connection
        try:
            self._client.ping()
            compression_status = "enabled" if self._compression_enabled else "disabled"
            logger.info(
                f"RedisStateBackend initialized: {redis_url} "
                f"(TTL={default_ttl}s, compression={compression_status})"
            )
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

        # Statistics tracking
        self._stats = {
            "compressed_count": 0,
            "uncompressed_count": 0,
            "total_bytes_saved": 0,
        }

    @property
    def key_prefix(self) -> str:
        """Return the Redis key prefix for this backend instance."""
        return self._key_prefix

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self._key_prefix}{key}"

    def _compress(self, data: bytes) -> bytes:
        """
        Compress data if it exceeds threshold and compression is enabled.

        Returns data with a marker byte prefix indicating compression status:
        - \\x01 + compressed_data (if compressed)
        - \\x00 + original_data (if not compressed)
        """
        if not self._compression_enabled or len(data) < self._compression_threshold:
            self._stats["uncompressed_count"] += 1
            return NO_COMPRESSION_MARKER + data

        try:
            compressed = self._compressor.compress(data)

            # Only use compression if it actually saves space
            if len(compressed) < len(data):
                bytes_saved = len(data) - len(compressed)
                self._stats["compressed_count"] += 1
                self._stats["total_bytes_saved"] += bytes_saved
                return COMPRESSION_MARKER + compressed
            else:
                self._stats["uncompressed_count"] += 1
                return NO_COMPRESSION_MARKER + data

        except Exception as e:
            logger.warning(f"Compression failed, storing uncompressed: {e}")
            self._stats["uncompressed_count"] += 1
            return NO_COMPRESSION_MARKER + data

    def _decompress(self, data: bytes) -> bytes:
        """
        Decompress data if it was compressed.

        Handles both compressed and uncompressed data based on marker byte.
        """
        if not data:
            return data

        marker = data[0:1]
        payload = data[1:]

        if marker == COMPRESSION_MARKER:
            if not self._decompressor:
                raise ValueError(
                    "Received compressed data but zstandard is not available. "
                    "Install with: pip install zstandard"
                )
            try:
                return self._decompressor.decompress(payload)
            except Exception as e:
                logger.error(f"Decompression failed: {e}")
                raise
        elif marker == NO_COMPRESSION_MARKER:
            return payload
        else:
            # Legacy data without marker - assume uncompressed
            return data

    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        """
        Retrieve from Redis using native Rust deserialization.

        Automatically handles decompression if the data was compressed.
        Returns None if key not found or deserialization fails.
        """
        redis_key = self._make_key(key)

        with profiler.profile(profiler.OP_STATE_LOAD):
            try:
                # Get serialized view
                data = self._client.get(redis_key)
                if not data:
                    return None

                # Decompress if needed
                with profiler.profile(profiler.OP_COMPRESSION):
                    data = self._decompress(data)

                # Deserialize using Rust's native MessagePack deserialization
                # Timestamp is embedded in the serialized data
                with profiler.profile(profiler.OP_SERIALIZATION):
                    view = RustLiveView.deserialize_msgpack(data)
                    timestamp = view.get_timestamp()

                return (view, timestamp)

            except Exception as e:
                logger.error(f"Failed to deserialize from Redis key '{key}': {e}")
                return None

    def set(self, key: str, view: RustLiveView, ttl: Optional[int] = None):
        """
        Store in Redis using native Rust serialization with optional compression.

        Uses MessagePack for efficient binary serialization:
        - 5-10x faster than pickle
        - 30-40% smaller payload
        - Optional zstd compression (60-80% additional reduction for large states)
        - Automatic TTL-based expiration
        - Timestamp embedded in serialized data
        """
        redis_key = self._make_key(key)
        if ttl is None:
            ttl = self._default_ttl

        with profiler.profile(profiler.OP_STATE_SAVE):
            try:
                # Serialize using Rust's native MessagePack serialization
                # Timestamp is automatically embedded in the serialized data
                with profiler.profile(profiler.OP_SERIALIZATION):
                    serialized = view.serialize_msgpack()

                # Compress if beneficial
                with profiler.profile(profiler.OP_COMPRESSION):
                    data = self._compress(serialized)

                # Store with TTL
                self._client.setex(redis_key, ttl, data)

            except Exception as e:
                logger.error(f"Failed to serialize to Redis key '{key}': {e}")
                raise

    def delete(self, key: str) -> bool:
        """Remove from Redis."""
        redis_key = self._make_key(key)

        # Delete the data (timestamp is embedded, no separate key)
        deleted = self._client.delete(redis_key)
        return deleted > 0

    def cleanup_expired(self, ttl: Optional[int] = None) -> int:
        """
        Redis handles TTL expiration automatically.

        This method returns 0 as no manual cleanup is needed.
        Redis will automatically remove expired keys based on their TTL.
        """
        # Redis handles expiration automatically via TTL
        # No manual cleanup needed
        return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get Redis backend statistics."""
        try:
            # Count keys with our prefix (limit to prevent memory issues with millions of sessions)
            pattern = f"{self._key_prefix}*"
            max_keys = 10000  # Limit to 10k keys for stats to prevent memory issues
            keys = []
            for key in self._client.scan_iter(match=pattern, count=100):
                keys.append(key)
                if len(keys) >= max_keys:
                    break

            # Get memory usage if available
            memory_usage = None
            try:
                info = self._client.info("memory")
                memory_usage = info.get("used_memory_human", "N/A")
            except Exception:
                pass

            stats = {
                "backend": "redis",
                "total_sessions": len(keys),
                "redis_memory": memory_usage,
                "stats_limited": len(keys) >= max_keys,  # True if we hit the limit
            }

            # Calculate ages by deserializing sample of views to get embedded timestamps
            if keys:
                current_time = time.time()
                ages = []
                # Sample first 100 keys for performance (deserialization has cost)
                for key in keys[:100]:
                    try:
                        data = self._client.get(key)
                        if data:
                            view = RustLiveView.deserialize_msgpack(data)
                            timestamp = view.get_timestamp()
                            if timestamp > 0:  # Valid timestamp (not initialized views)
                                ages.append(current_time - timestamp)
                    except Exception:
                        # Skip keys that fail to deserialize
                        pass

                if ages:
                    stats["oldest_session_age"] = max(ages)
                    stats["newest_session_age"] = min(ages)
                    stats["average_age"] = sum(ages) / len(ages)

            return stats

        except Exception as e:
            logger.error(f"Failed to get Redis stats: {e}")
            return {
                "backend": "redis",
                "error": str(e),
            }

    def health_check(self) -> Dict[str, Any]:
        """Check Redis backend health and connectivity."""
        start_time = time.time()

        try:
            # Test Redis connectivity with PING command
            ping_result = self._client.ping()

            if not ping_result:
                return {
                    "status": "unhealthy",
                    "backend": "redis",
                    "error": "Redis PING returned False",
                }

            # Test basic read/write operations
            test_key = self._make_key("__health_check__")

            # Test SETEX (write with TTL) - 1 second TTL since key is deleted immediately
            self._client.setex(test_key, 1, b"health_check")

            # Test GET (read)
            value = self._client.get(test_key)

            if value != b"health_check":
                return {
                    "status": "unhealthy",
                    "backend": "redis",
                    "error": "Redis read/write test failed",
                }

            # Test DELETE
            self._client.delete(test_key)

            latency_ms = (time.time() - start_time) * 1000

            # Get additional connection info
            info = {}
            try:
                server_info = self._client.info("server")
                info["redis_version"] = server_info.get("redis_version", "unknown")
                info["uptime_seconds"] = server_info.get("uptime_in_seconds", 0)

                memory_info = self._client.info("memory")
                info["used_memory_human"] = memory_info.get("used_memory_human", "N/A")
            except Exception:
                # Info is optional, continue if it fails
                pass

            return {
                "status": "healthy",
                "backend": "redis",
                "latency_ms": round(latency_ms, 2),
                "details": info,
            }

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"Redis health check failed: {e}")

            return {
                "status": "unhealthy",
                "backend": "redis",
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
            }

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get detailed memory usage statistics from Redis.

        Samples a subset of keys to estimate total memory usage
        without scanning the entire keyspace.

        Returns:
            Dictionary with memory metrics including total size estimates,
            average size, and the largest sessions.
        """
        try:
            pattern = f"{self._key_prefix}*"
            max_sample = 100  # Sample size for estimation

            # Sample keys for size estimation
            keys = []
            for key in self._client.scan_iter(match=pattern, count=100):
                keys.append(key)
                if len(keys) >= max_sample:
                    break

            if not keys:
                return {
                    "backend": "redis",
                    "total_state_bytes": 0,
                    "average_state_bytes": 0,
                    "largest_sessions": [],
                    "sessions_sampled": 0,
                    "note": "No sessions found",
                }

            # Get sizes for sampled keys
            sizes = []
            for key in keys:
                try:
                    # Use MEMORY USAGE if available (Redis 4.0+)
                    size = self._client.memory_usage(key)
                    if size:
                        sizes.append((key.decode() if isinstance(key, bytes) else key, size))
                except Exception:
                    # Fallback: get actual data size
                    try:
                        data = self._client.get(key)
                        if data:
                            sizes.append((
                                key.decode() if isinstance(key, bytes) else key,
                                len(data)
                            ))
                    except Exception:
                        pass

            if not sizes:
                return {
                    "backend": "redis",
                    "total_state_bytes": 0,
                    "average_state_bytes": 0,
                    "largest_sessions": [],
                    "sessions_sampled": 0,
                    "error": "Could not retrieve size information",
                }

            total_bytes = sum(s for _, s in sizes)
            avg_bytes = total_bytes / len(sizes) if sizes else 0

            # Sort by size, get top 10
            sorted_sizes = sorted(sizes, key=lambda x: x[1], reverse=True)[:10]

            # Get total key count for estimation
            total_keys = len(keys)
            try:
                # Try to get actual count via SCAN (limited)
                count = 0
                for _ in self._client.scan_iter(match=pattern, count=1000):
                    count += 1
                    if count >= 10000:
                        break
                total_keys = count
            except Exception:
                pass

            # Estimate total memory (extrapolate from sample)
            estimated_total = avg_bytes * total_keys

            return {
                "backend": "redis",
                "total_state_bytes_estimated": round(estimated_total),
                "total_state_kb_estimated": round(estimated_total / 1024, 2),
                "average_state_bytes": round(avg_bytes, 2),
                "average_state_kb": round(avg_bytes / 1024, 2),
                "largest_sessions": [
                    {
                        "key": k.replace(self._key_prefix, ""),
                        "size_bytes": s,
                        "size_kb": round(s / 1024, 2),
                    }
                    for k, s in sorted_sizes
                ],
                "sessions_sampled": len(sizes),
                "total_sessions_estimated": total_keys,
                "note": "Values are estimates based on sampling" if total_keys > max_sample else None,
            }

        except Exception as e:
            logger.error(f"Failed to get Redis memory stats: {e}")
            return {
                "backend": "redis",
                "error": str(e),
            }

    def get_compression_stats(self) -> Dict[str, Any]:
        """
        Get compression statistics for this backend.

        Returns:
            Dictionary with compression metrics including:
            - enabled: Whether compression is enabled
            - compressed_count: Number of states stored with compression
            - uncompressed_count: Number of states stored without compression
            - total_bytes_saved: Estimated bytes saved by compression
            - compression_rate_percent: Percentage of states that were compressed
        """
        if not self._compression_enabled:
            return {
                "enabled": False,
                "note": "zstd compression is not available or disabled",
            }

        total_ops = self._stats["compressed_count"] + self._stats["uncompressed_count"]
        compression_rate = (
            self._stats["compressed_count"] / total_ops * 100
            if total_ops > 0 else 0
        )

        return {
            "enabled": True,
            "compressed_count": self._stats["compressed_count"],
            "uncompressed_count": self._stats["uncompressed_count"],
            "total_bytes_saved": self._stats["total_bytes_saved"],
            "total_kb_saved": round(self._stats["total_bytes_saved"] / 1024, 2),
            "compression_rate_percent": round(compression_rate, 1),
            "compression_level": self._compression_level,
            "compression_threshold_kb": self._compression_threshold // 1024,
        }


# Global backend instance (initialized by get_backend())
_backend: Optional[StateBackend] = None


def get_backend() -> StateBackend:
    """
    Get the configured state backend instance.

    Initializes backend on first call based on Django settings.
    Returns cached instance on subsequent calls.

    Configuration in settings.py:
        DJUST_CONFIG = {
            'STATE_BACKEND': 'redis',  # or 'memory'
            'REDIS_URL': 'redis://localhost:6379/0',
            'SESSION_TTL': 3600,
            'STATE_SIZE_WARNING_KB': 100,  # Warn when state exceeds this size
            # Compression settings (Redis only)
            'COMPRESSION_ENABLED': True,  # Enable zstd compression
            'COMPRESSION_THRESHOLD_KB': 10,  # Compress states > 10KB
            'COMPRESSION_LEVEL': 3,  # zstd level 1-22 (higher = slower but smaller)
        }

    Returns:
        StateBackend instance (InMemory or Redis)
    """
    global _backend

    if _backend is not None:
        return _backend

    # Load configuration from Django settings
    try:
        from django.conf import settings

        config = getattr(settings, "DJUST_CONFIG", {})
    except Exception:
        config = {}

    backend_type = config.get("STATE_BACKEND", "memory")
    ttl = config.get("SESSION_TTL", 3600)
    state_size_warning_kb = config.get("STATE_SIZE_WARNING_KB", DEFAULT_STATE_SIZE_WARNING_KB)

    if backend_type == "redis":
        redis_url = config.get("REDIS_URL", "redis://localhost:6379/0")
        key_prefix = config.get("REDIS_KEY_PREFIX", "djust:")
        # Compression settings
        compression_enabled = config.get("COMPRESSION_ENABLED", True)
        compression_threshold_kb = config.get("COMPRESSION_THRESHOLD_KB", DEFAULT_COMPRESSION_THRESHOLD_KB)
        compression_level = config.get("COMPRESSION_LEVEL", 3)

        _backend = RedisStateBackend(
            redis_url=redis_url,
            default_ttl=ttl,
            key_prefix=key_prefix,
            compression_enabled=compression_enabled,
            compression_threshold_kb=compression_threshold_kb,
            compression_level=compression_level,
        )
    else:
        _backend = InMemoryStateBackend(
            default_ttl=ttl,
            state_size_warning_kb=state_size_warning_kb,
        )

    logger.info(f"Initialized state backend: {backend_type}")
    return _backend


def set_backend(backend: StateBackend):
    """
    Manually set the state backend (useful for testing).

    Args:
        backend: StateBackend instance to use
    """
    global _backend
    _backend = backend
