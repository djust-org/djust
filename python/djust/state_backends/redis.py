"""
Redis-backed state backend for production horizontal scaling.
"""

import time
import logging
from typing import Optional, Dict, Any, Tuple
from djust._rust import RustLiveView
from djust.profiler import profiler

from .base import (
    StateBackend,
    DEFAULT_COMPRESSION_THRESHOLD_KB,
    COMPRESSION_MARKER,
    NO_COMPRESSION_MARKER,
    ZSTD_AVAILABLE,
)

# Import zstd if available (already checked in base)
if ZSTD_AVAILABLE:
    import zstandard as zstd

logger = logging.getLogger(__name__)


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
                            sizes.append(
                                (key.decode() if isinstance(key, bytes) else key, len(data))
                            )
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
                "note": "Values are estimates based on sampling"
                if total_keys > max_sample
                else None,
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
        compression_rate = self._stats["compressed_count"] / total_ops * 100 if total_ops > 0 else 0

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
