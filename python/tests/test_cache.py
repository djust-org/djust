"""
Unit tests for SerializerCache.

Tests both filesystem and Redis backends.
"""

import pytest
import tempfile
from pathlib import Path
from djust.optimization.cache import SerializerCache


# Test fixtures


def simple_serializer(obj):
    """Simple test serializer function."""
    return {"id": obj.id, "name": obj.name}


def complex_serializer(obj):
    """Complex test serializer with nested data."""
    return {
        "id": obj.id,
        "name": obj.name,
        "nested": {"value": obj.value * 2},
    }


# Module-level helper functions (can be pickled)
def func_double(x):
    """Helper function that doubles input."""
    return x * 2


def func_triple(x):
    """Helper function that triples input."""
    return x * 3


class MockObject:
    """Mock object for testing serializers."""

    def __init__(self, id, name, value=0):
        self.id = id
        self.name = name
        self.value = value


# Filesystem backend tests


def test_filesystem_cache_init():
    """Test filesystem cache initialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        assert cache.backend == "filesystem"
        assert cache.cache_dir == Path(tmpdir)
        assert cache.cache_dir.exists()
        assert len(cache._memory_cache) == 0


def test_filesystem_cache_get_set():
    """Test filesystem cache get/set operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        # Generate cache key
        key = cache.get_cache_key("template content", "var_name")

        # Initially should return None
        assert cache.get(key) is None

        # Set the function
        cache.set(key, func_double)

        # Should now return the function
        cached_func = cache.get(key)
        assert cached_func is not None
        assert cached_func(5) == 10


def test_filesystem_cache_persistence():
    """Test that filesystem cache persists across instances."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create first cache instance
        cache1 = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        key = cache1.get_cache_key("template", "var")
        cache1.set(key, func_triple)

        # Create second cache instance (same directory)
        cache2 = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        # Should load from filesystem
        cached_func = cache2.get(key)
        assert cached_func is not None
        assert cached_func(3) == 9


def test_filesystem_cache_invalidate():
    """Test filesystem cache invalidation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        key = cache.get_cache_key("template", "var")
        cache.set(key, func_double)

        # Verify it's cached
        assert cache.get(key) is not None

        # Invalidate
        cache.invalidate(key)

        # Should now return None
        assert cache.get(key) is None


def test_filesystem_cache_clear():
    """Test filesystem cache clear operation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        # Cache multiple functions
        for i in range(5):
            key = cache.get_cache_key(f"template{i}", f"var{i}")
            # Alternate between double and triple functions
            func = func_double if i % 2 == 0 else func_triple
            cache.set(key, func)

        # Verify all are cached
        assert len(cache._memory_cache) == 5
        assert len(list(cache.cache_dir.glob("*.pkl"))) == 5

        # Clear cache
        cache.clear()

        # Should be empty
        assert len(cache._memory_cache) == 0
        assert len(list(cache.cache_dir.glob("*.pkl"))) == 0


def test_cache_key_generation():
    """Test cache key uniqueness."""
    cache = SerializerCache(backend="filesystem")

    key1 = cache.get_cache_key("template 1", "var1")
    key2 = cache.get_cache_key("template 2", "var1")
    key3 = cache.get_cache_key("template 1", "var2")
    key4 = cache.get_cache_key("template 1", "var1")  # Same as key1

    # All should be SHA256 hashes (64 hex chars)
    assert len(key1) == 64
    assert len(key2) == 64
    assert len(key3) == 64
    assert len(key4) == 64

    # Different inputs should produce different keys
    assert key1 != key2
    assert key1 != key3
    assert key2 != key3

    # Same input should produce same key
    assert key1 == key4


def test_memory_cache():
    """Test in-memory caching."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        key = cache.get_cache_key("template", "var")

        # Set in cache
        cache.set(key, func_double)

        # First get should hit memory cache
        cached_func = cache.get(key)
        assert cached_func is not None

        # Clear only the cache file (not memory)
        cache_file = cache.cache_dir / f"{key}.pkl"
        cache_file.unlink()

        # Should still get from memory cache
        cached_func = cache.get(key)
        assert cached_func is not None

        # Clear memory cache
        cache._memory_cache.clear()

        # Now should return None (no file, no memory)
        cached_func = cache.get(key)
        assert cached_func is None


def test_cache_stats():
    """Test cache statistics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        # Initially empty
        stats = cache.stats()
        assert stats["backend"] == "filesystem"
        assert stats["memory_cache_size"] == 0
        assert stats["total_cache_size"] == 0

        # Add some cached functions
        for i in range(3):
            key = cache.get_cache_key(f"template{i}", f"var{i}")
            func = func_double if i % 2 == 0 else func_triple
            cache.set(key, func)

        # Check stats
        stats = cache.stats()
        assert stats["memory_cache_size"] == 3
        assert stats["total_cache_size"] == 3


def test_corrupt_cache_file():
    """Test handling of corrupt cache files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        key = cache.get_cache_key("template", "var")
        cache_file = cache.cache_dir / f"{key}.pkl"

        # Write corrupt data
        cache_file.write_text("corrupt data")

        # Should return None and remove corrupt file
        cached_func = cache.get(key)
        assert cached_func is None
        assert not cache_file.exists()


def test_real_world_serializer():
    """Test caching a real serializer function."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        # Simulate a real serializer
        key = cache.get_cache_key(
            "{{ lease.property.name }} {{ lease.tenant.user.email }}", "lease"
        )

        # Cache the serializer
        cache.set(key, simple_serializer)

        # Retrieve and use it
        cached_serializer = cache.get(key)
        assert cached_serializer is not None

        # Test serialization
        obj = MockObject(id=1, name="Test Lease")
        result = cached_serializer(obj)
        assert result == {"id": 1, "name": "Test Lease"}


# Redis backend tests (skip if redis not available)


@pytest.mark.skipif(True, reason="Redis tests require running Redis server and redis package")
def test_redis_cache_init():
    """Test Redis cache initialization."""
    try:
        cache = SerializerCache(backend="redis", redis_url="redis://localhost:6379/15")
        assert cache.backend == "redis"
        assert cache._redis_client is not None
    except ValueError as e:
        pytest.skip(f"Redis not available: {e}")


@pytest.mark.skipif(True, reason="Redis tests require running Redis server and redis package")
def test_redis_cache_get_set():
    """Test Redis cache get/set operations."""
    try:
        cache = SerializerCache(backend="redis", redis_url="redis://localhost:6379/15")

        key = cache.get_cache_key("template", "var")

        # Initially should return None
        assert cache.get(key) is None

        # Set the function
        cache.set(key, func_double)

        # Should now return the function
        cached_func = cache.get(key)
        assert cached_func is not None
        assert cached_func(5) == 10

        # Cleanup
        cache.clear()
    except ValueError as e:
        pytest.skip(f"Redis not available: {e}")


@pytest.mark.skipif(True, reason="Redis tests require running Redis server and redis package")
def test_redis_cache_persistence():
    """Test that Redis cache persists across instances."""
    try:
        # Create first cache instance
        cache1 = SerializerCache(backend="redis", redis_url="redis://localhost:6379/15")

        key = cache1.get_cache_key("template", "var")
        cache1.set(key, func_triple)

        # Create second cache instance (same Redis)
        cache2 = SerializerCache(backend="redis", redis_url="redis://localhost:6379/15")

        # Should load from Redis
        cached_func = cache2.get(key)
        assert cached_func is not None
        assert cached_func(3) == 9

        # Cleanup
        cache2.clear()
    except ValueError as e:
        pytest.skip(f"Redis not available: {e}")


def test_invalid_backend():
    """Test that invalid backend raises ValueError."""
    with pytest.raises(ValueError, match="Unknown backend"):
        SerializerCache(backend="invalid")


def test_redis_without_package():
    """Test that Redis backend without redis package raises ValueError."""
    # This test assumes redis is not installed
    # If redis is installed, it will fail (which is expected)
    try:
        import redis  # noqa: F401

        pytest.skip("redis package is installed")
    except ImportError:
        pass

    with pytest.raises(ValueError, match="Redis backend requires 'redis' package"):
        SerializerCache(backend="redis")


# Performance tests


def test_cache_hit_performance():
    """Test that cache hits are fast."""
    import time

    with tempfile.TemporaryDirectory() as tmpdir:
        cache = SerializerCache(backend="filesystem", cache_dir=tmpdir)

        key = cache.get_cache_key("template", "var")
        cache.set(key, func_double)

        # Warmup
        for _ in range(10):
            cache.get(key)

        # Measure
        start = time.time()
        for _ in range(1000):
            cache.get(key)
        elapsed = (time.time() - start) * 1000  # Convert to ms

        # Cache hits should be < 1ms on average
        avg_time = elapsed / 1000
        assert avg_time < 1.0, f"Cache hit took {avg_time}ms, expected < 1ms"
