"""
Caching infrastructure for compiled serializer functions.

Supports both filesystem and Redis backends for production deployments.
"""

import hashlib
import pickle
from pathlib import Path
from typing import Callable, Optional, Any


class SerializerCache:
    """
    Cache for compiled serializer functions with multiple backend support.

    Supports two backends:
    - filesystem: Fast local cache using pickle (default for development)
    - redis: Distributed cache for production horizontal scaling

    Example:
        >>> cache = SerializerCache(backend='filesystem')
        >>> key = cache.get_cache_key("template content", "variable_name")
        >>> cache.set(key, my_serializer_func)
        >>> cached_func = cache.get(key)
    """

    def __init__(
        self,
        backend: str = "filesystem",
        cache_dir: str = "__pycache__/djust_serializers",
        redis_url: Optional[str] = None,
    ):
        """
        Initialize cache with specified backend.

        Args:
            backend: Cache backend ('filesystem' or 'redis')
            cache_dir: Directory for filesystem cache (default: __pycache__/djust_serializers)
            redis_url: Redis connection URL (only used if backend='redis')

        Raises:
            ValueError: If backend is 'redis' but redis package not installed
        """
        self.backend = backend
        self.cache_dir = Path(cache_dir)
        self._memory_cache: dict[str, Callable] = {}
        self._redis_client: Optional[Any] = None

        if backend == "filesystem":
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        elif backend == "redis":
            try:
                import redis

                self._redis_client = redis.from_url(redis_url or "redis://localhost:6379/0")
                # Test connection
                self._redis_client.ping()
            except ImportError as e:
                raise ValueError(
                    "Redis backend requires 'redis' package. Install with: pip install redis"
                ) from e
            except Exception as e:
                raise ValueError(f"Failed to connect to Redis: {e}") from e
        else:
            raise ValueError(f"Unknown backend: {backend}. Use 'filesystem' or 'redis'")

    def get_cache_key(self, template_content: str, variable_name: str) -> str:
        """
        Generate cache key from template content and variable name.

        Args:
            template_content: Template source code
            variable_name: Variable name in template (e.g., "expiring_soon")

        Returns:
            SHA256 hash as hex string

        Example:
            >>> cache = SerializerCache()
            >>> key1 = cache.get_cache_key("{{ user.email }}", "user")
            >>> key2 = cache.get_cache_key("{{ user.name }}", "user")
            >>> key1 != key2  # Different templates = different keys
            True
        """
        content = f"{template_content}:{variable_name}"
        return hashlib.sha256(content.encode()).hexdigest()

    def get(self, cache_key: str) -> Optional[Callable]:
        """
        Retrieve cached serializer function.

        Checks memory cache first, then backend (filesystem or Redis).

        Args:
            cache_key: Cache key from get_cache_key()

        Returns:
            Cached serializer function or None if not found

        Example:
            >>> cache = SerializerCache()
            >>> func = cache.get("a4f8b2...")
            >>> if func is None:
            ...     # Cache miss - generate new serializer
            ...     func = compile_serializer(...)
            ...     cache.set("a4f8b2...", func)
        """
        # Check memory cache first (fastest)
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        # Check backend cache
        if self.backend == "filesystem":
            return self._get_from_filesystem(cache_key)
        elif self.backend == "redis":
            return self._get_from_redis(cache_key)

        return None

    def set(self, cache_key: str, serializer_func: Callable):
        """
        Cache serializer function.

        Stores in both memory cache and backend for persistence.

        Args:
            cache_key: Cache key from get_cache_key()
            serializer_func: Compiled serializer function to cache

        Example:
            >>> cache = SerializerCache()
            >>> def my_serializer(obj):
            ...     return {"name": obj.name}
            >>> cache.set("a4f8b2...", my_serializer)
        """
        # Store in memory cache
        self._memory_cache[cache_key] = serializer_func

        # Store in backend
        if self.backend == "filesystem":
            self._set_to_filesystem(cache_key, serializer_func)
        elif self.backend == "redis":
            self._set_to_redis(cache_key, serializer_func)

    def invalidate(self, cache_key: str):
        """
        Invalidate cached serializer.

        Removes from both memory cache and backend.

        Args:
            cache_key: Cache key to invalidate

        Example:
            >>> cache = SerializerCache()
            >>> cache.invalidate("a4f8b2...")
        """
        # Remove from memory cache
        if cache_key in self._memory_cache:
            del self._memory_cache[cache_key]

        # Remove from backend
        if self.backend == "filesystem":
            self._invalidate_filesystem(cache_key)
        elif self.backend == "redis":
            self._invalidate_redis(cache_key)

    def clear(self):
        """
        Clear all cached serializers.

        Removes all entries from memory and backend cache.

        Example:
            >>> cache = SerializerCache()
            >>> cache.clear()  # Clear all cached serializers
        """
        # Clear memory cache
        self._memory_cache.clear()

        # Clear backend
        if self.backend == "filesystem":
            self._clear_filesystem()
        elif self.backend == "redis":
            self._clear_redis()

    # Filesystem backend methods

    def _get_from_filesystem(self, cache_key: str) -> Optional[Callable]:
        """Get serializer from filesystem cache."""
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        if cache_file.exists():
            try:
                with open(cache_file, "rb") as f:
                    func = pickle.load(f)
                    # Store in memory cache for faster access
                    self._memory_cache[cache_key] = func
                    return func
            except Exception:
                # Corrupt cache file - remove it
                cache_file.unlink(missing_ok=True)
                return None
        return None

    def _set_to_filesystem(self, cache_key: str, serializer_func: Callable):
        """Store serializer to filesystem cache."""
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        try:
            with open(cache_file, "wb") as f:
                pickle.dump(serializer_func, f)
        except Exception as e:
            # Log warning but don't fail - cache is optional
            import warnings

            warnings.warn(f"Failed to cache serializer to filesystem: {e}")

    def _invalidate_filesystem(self, cache_key: str):
        """Remove serializer from filesystem cache."""
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        cache_file.unlink(missing_ok=True)

    def _clear_filesystem(self):
        """Remove all cached serializers from filesystem."""
        if self.cache_dir.exists():
            for cache_file in self.cache_dir.glob("*.pkl"):
                cache_file.unlink(missing_ok=True)

    # Redis backend methods

    def _get_from_redis(self, cache_key: str) -> Optional[Callable]:
        """Get serializer from Redis cache."""
        if self._redis_client is None:
            return None

        try:
            data = self._redis_client.get(f"djust:serializer:{cache_key}")
            if data:
                func = pickle.loads(data)
                # Store in memory cache for faster access
                self._memory_cache[cache_key] = func
                return func
        except Exception:
            # Redis error - fall back to None
            return None

        return None

    def _set_to_redis(self, cache_key: str, serializer_func: Callable):
        """Store serializer to Redis cache."""
        if self._redis_client is None:
            return

        try:
            data = pickle.dumps(serializer_func)
            # Store with 24 hour TTL (auto-expire old serializers)
            self._redis_client.setex(
                f"djust:serializer:{cache_key}",
                86400,  # 24 hours in seconds
                data,
            )
        except Exception as e:
            # Log warning but don't fail - cache is optional
            import warnings

            warnings.warn(f"Failed to cache serializer to Redis: {e}")

    def _invalidate_redis(self, cache_key: str):
        """Remove serializer from Redis cache."""
        if self._redis_client is None:
            return

        try:
            self._redis_client.delete(f"djust:serializer:{cache_key}")
        except Exception:
            pass

    def _clear_redis(self):
        """Remove all cached serializers from Redis."""
        if self._redis_client is None:
            return

        try:
            # Find and delete all djust:serializer:* keys
            keys = self._redis_client.keys("djust:serializer:*")
            if keys:
                self._redis_client.delete(*keys)
        except Exception:
            pass

    def stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics including:
            - backend: Cache backend name
            - memory_cache_size: Number of entries in memory
            - total_cache_size: Total cached entries (if supported by backend)

        Example:
            >>> cache = SerializerCache()
            >>> stats = cache.stats()
            >>> print(f"Cached {stats['memory_cache_size']} serializers in memory")
        """
        stats = {
            "backend": self.backend,
            "memory_cache_size": len(self._memory_cache),
        }

        if self.backend == "filesystem":
            stats["total_cache_size"] = len(list(self.cache_dir.glob("*.pkl")))
        elif self.backend == "redis" and self._redis_client:
            try:
                keys = self._redis_client.keys("djust:serializer:*")
                stats["total_cache_size"] = len(keys)
            except Exception:
                stats["total_cache_size"] = None

        return stats
