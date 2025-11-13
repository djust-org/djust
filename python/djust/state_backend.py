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
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple
from djust._rust import RustLiveView

logger = logging.getLogger(__name__)


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


class InMemoryStateBackend(StateBackend):
    """
    In-memory state backend for development and testing.

    Fast and simple, but:
    - Does not scale horizontally (single server only)
    - Data lost on server restart
    - Potential memory leaks without cleanup

    Suitable for:
    - Development environments
    - Single-server deployments
    - Testing
    """

    def __init__(self, default_ttl: int = 3600):
        """
        Initialize in-memory backend.

        Args:
            default_ttl: Default session TTL in seconds (default: 1 hour)
        """
        self._cache: Dict[str, Tuple[RustLiveView, float]] = {}
        self._default_ttl = default_ttl
        logger.info(f"InMemoryStateBackend initialized with TTL={default_ttl}s")

    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        """Retrieve from in-memory cache."""
        return self._cache.get(key)

    def set(self, key: str, view: RustLiveView, ttl: Optional[int] = None):
        """Store in in-memory cache with timestamp."""
        timestamp = time.time()
        self._cache[key] = (view, timestamp)

    def delete(self, key: str) -> bool:
        """Remove from in-memory cache."""
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def cleanup_expired(self, ttl: Optional[int] = None) -> int:
        """Clean up expired sessions from memory."""
        if ttl is None:
            ttl = self._default_ttl

        cutoff = time.time() - ttl
        expired_keys = []

        for key, (view, timestamp) in list(self._cache.items()):
            if timestamp < cutoff:
                expired_keys.append(key)

        for key in expired_keys:
            del self._cache[key]

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired sessions from memory")

        return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """Get in-memory cache statistics."""
        if not self._cache:
            return {
                "backend": "memory",
                "total_sessions": 0,
                "oldest_session_age": 0,
                "newest_session_age": 0,
                "average_age": 0,
            }

        current_time = time.time()
        ages = [current_time - timestamp for _, timestamp in self._cache.values()]

        return {
            "backend": "memory",
            "total_sessions": len(self._cache),
            "oldest_session_age": max(ages) if ages else 0,
            "newest_session_age": min(ages) if ages else 0,
            "average_age": sum(ages) / len(ages) if ages else 0,
        }


class RedisStateBackend(StateBackend):
    """
    Redis-backed state backend for production horizontal scaling.

    Benefits:
    - Horizontal scaling across multiple servers
    - Persistent state survives server restarts
    - Automatic TTL-based expiration
    - Native Rust serialization (5-10x faster, 30-40% smaller)

    Requirements:
    - Redis server running
    - redis-py package installed

    Usage:
        backend = RedisStateBackend(
            redis_url='redis://localhost:6379/0',
            default_ttl=3600
        )
    """

    def __init__(self, redis_url: str, default_ttl: int = 3600, key_prefix: str = "djust:"):
        """
        Initialize Redis backend.

        Args:
            redis_url: Redis connection URL (e.g., 'redis://localhost:6379/0')
            default_ttl: Default session TTL in seconds (default: 1 hour)
            key_prefix: Prefix for all Redis keys (default: 'djust:')
        """
        try:
            import redis
        except ImportError:
            raise ImportError(
                "redis-py is required for RedisStateBackend. " "Install with: pip install redis"
            )

        self._client = redis.from_url(redis_url)
        self._default_ttl = default_ttl
        self._key_prefix = key_prefix

        # Test connection
        try:
            self._client.ping()
            logger.info(f"RedisStateBackend initialized: {redis_url} (TTL={default_ttl}s)")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self._key_prefix}{key}"

    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        """
        Retrieve from Redis using native Rust deserialization.

        Returns None if key not found or deserialization fails.
        """
        redis_key = self._make_key(key)

        try:
            # Get serialized view
            data = self._client.get(redis_key)
            if not data:
                return None

            # Deserialize using Rust's native MessagePack deserialization
            # Timestamp is embedded in the serialized data
            view = RustLiveView.deserialize_msgpack(data)
            timestamp = view.get_timestamp()

            return (view, timestamp)

        except Exception as e:
            logger.error(f"Failed to deserialize from Redis key '{key}': {e}")
            return None

    def set(self, key: str, view: RustLiveView, ttl: Optional[int] = None):
        """
        Store in Redis using native Rust serialization.

        Uses MessagePack for efficient binary serialization:
        - 5-10x faster than pickle
        - 30-40% smaller payload
        - Automatic TTL-based expiration
        - Timestamp embedded in serialized data
        """
        redis_key = self._make_key(key)
        if ttl is None:
            ttl = self._default_ttl

        try:
            # Serialize using Rust's native MessagePack serialization
            # Timestamp is automatically embedded in the serialized data
            serialized = view.serialize_msgpack()

            # Store with TTL
            self._client.setex(redis_key, ttl, serialized)

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

    if backend_type == "redis":
        redis_url = config.get("REDIS_URL", "redis://localhost:6379/0")
        _backend = RedisStateBackend(redis_url=redis_url, default_ttl=ttl)
    else:
        _backend = InMemoryStateBackend(default_ttl=ttl)

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
