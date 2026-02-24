"""
Tests for state backend system (InMemory and Redis).

Tests cover:
- Serialization/deserialization
- Backend operations (get, set, delete)
- TTL expiration
- Statistics
- Error handling
"""

import os
import time
import pytest
from djust.state_backend import (
    InMemoryStateBackend,
    RedisStateBackend,
    get_backend,
    set_backend,
)
from djust._rust import RustLiveView


class TestRustSerialization:
    """Test native Rust serialization with MessagePack."""

    def test_serialize_deserialize_basic(self):
        """Test basic serialization round-trip."""
        # Create a view with state
        view = RustLiveView("<div>{{ name }}</div>")
        view.update_state({"name": "Alice"})

        # Serialize
        serialized = view.serialize_msgpack()
        assert isinstance(serialized, bytes)
        assert len(serialized) > 0

        # Deserialize
        view2 = RustLiveView.deserialize_msgpack(serialized)
        html = view2.render()
        assert html == "<div>Alice</div>"

    def test_serialize_preserves_vdom(self):
        """Test that VDOM state is preserved through serialization."""
        view = RustLiveView("<div>{{ count }}</div>")
        view.update_state({"count": 1})

        # First render to create VDOM
        html1 = view.render()
        # HTML now includes data-dj attributes for ID-based patching
        assert "<div" in html1 and ">1</div>" in html1

        # Serialize and deserialize
        serialized = view.serialize_msgpack()
        view2 = RustLiveView.deserialize_msgpack(serialized)

        # Verify state was preserved
        html2 = view2.render()
        assert "<div" in html2 and ">1</div>" in html2  # State preserved

        # Update and render again to verify VDOM works
        view2.update_state({"count": 2})
        html3, patches, version = view2.render_with_diff()
        # render_with_diff returns hydrated HTML with data-dj attributes
        assert "<div" in html3 and ">2</div>" in html3
        assert version >= 1  # Version counter was preserved

    def test_serialize_complex_state(self):
        """Test serialization with complex nested state."""
        view = RustLiveView("<div>{{ user.name }} - {{ items|length }}</div>")
        view.update_state(
            {
                "user": {"name": "Bob", "email": "bob@example.com"},
                "items": [1, 2, 3, 4, 5],
                "enabled": True,
                "count": 42,
            }
        )

        # Serialize/deserialize
        serialized = view.serialize_msgpack()
        view2 = RustLiveView.deserialize_msgpack(serialized)

        # Verify state preserved
        html = view2.render()
        assert "Bob" in html
        assert "5" in html

    def test_serialize_size_efficiency(self):
        """Test that MessagePack is compact."""
        view = RustLiveView("<div>{{ data }}</div>")
        view.update_state({"data": "x" * 1000})  # 1KB of data

        serialized = view.serialize_msgpack()

        # MessagePack should be smaller than raw data + overhead
        # (testing relative efficiency, not absolute size)
        assert len(serialized) < 1500  # Should be reasonably compact


class TestInMemoryBackend:
    """Test InMemoryStateBackend."""

    def test_get_set_basic(self):
        """Test basic get/set operations."""
        backend = InMemoryStateBackend()
        view = RustLiveView("<div>test</div>")

        # Set
        backend.set("test_key", view)

        # Get
        result = backend.get("test_key")
        assert result is not None
        view2, timestamp = result
        assert isinstance(timestamp, float)
        html = view2.render()
        assert html == "<div>test</div>"

    def test_get_nonexistent(self):
        """Test getting nonexistent key returns None."""
        backend = InMemoryStateBackend()
        result = backend.get("nonexistent")
        assert result is None

    def test_delete(self):
        """Test deleting sessions."""
        backend = InMemoryStateBackend()
        view = RustLiveView("<div>test</div>")

        backend.set("key1", view)
        assert backend.get("key1") is not None

        # Delete
        deleted = backend.delete("key1")
        assert deleted is True
        assert backend.get("key1") is None

        # Delete nonexistent
        deleted = backend.delete("key2")
        assert deleted is False

    def test_cleanup_expired(self):
        """Test TTL-based cleanup."""
        backend = InMemoryStateBackend(default_ttl=1)
        view = RustLiveView("<div>test</div>")

        # Add session
        backend.set("key1", view)
        assert backend.get("key1") is not None

        # Wait for expiration
        time.sleep(1.1)

        # Cleanup expired sessions
        cleaned = backend.cleanup_expired(ttl=1)
        assert cleaned == 1
        assert backend.get("key1") is None

    def test_stats(self):
        """Test statistics tracking."""
        backend = InMemoryStateBackend()

        # Empty stats
        stats = backend.get_stats()
        assert stats["backend"] == "memory"
        assert stats["total_sessions"] == 0

        # Add sessions
        view1 = RustLiveView("<div>1</div>")
        view2 = RustLiveView("<div>2</div>")
        backend.set("key1", view1)
        time.sleep(0.1)
        backend.set("key2", view2)

        # Check stats
        stats = backend.get_stats()
        assert stats["total_sessions"] == 2
        assert stats["oldest_session_age"] > stats["newest_session_age"]
        assert stats["average_age"] > 0

    def test_multiple_sessions(self):
        """Test managing multiple sessions."""
        backend = InMemoryStateBackend()

        # Add multiple sessions
        for i in range(10):
            view = RustLiveView(f"<div>{i}</div>")
            backend.set(f"key{i}", view)

        # Verify all exist
        for i in range(10):
            result = backend.get(f"key{i}")
            assert result is not None

        stats = backend.get_stats()
        assert stats["total_sessions"] == 10


class TestRedisBackend:
    """Test RedisStateBackend (requires Redis server)."""

    @pytest.fixture
    def redis_backend(self, request):
        """Create Redis backend for testing (parallel-safe)."""
        try:
            # Get worker ID for parallel test isolation
            worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
            key_prefix = f"djust:test:{worker_id}:"

            backend = RedisStateBackend(
                redis_url="redis://localhost:6379/15",  # Use DB 15 for testing
                default_ttl=60,
                key_prefix=key_prefix,
            )
            yield backend
            # Cleanup: delete all test keys for this worker
            import redis

            client = redis.from_url("redis://localhost:6379/15")
            for key in client.scan_iter(match=f"{key_prefix}*"):
                client.delete(key)
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

    def test_redis_get_set(self, redis_backend):
        """Test Redis get/set operations."""
        view = RustLiveView("<div>{{ name }}</div>")
        view.update_state({"name": "Redis"})

        # Set
        redis_backend.set("test_key", view)

        # Get
        result = redis_backend.get("test_key")
        assert result is not None
        view2, timestamp = result
        html = view2.render()
        assert html == "<div>Redis</div>"

    def test_redis_serialization_performance(self, redis_backend):
        """Test that Redis uses native serialization."""
        view = RustLiveView("<div>{{ data }}</div>")
        view.update_state({"data": "x" * 1000})

        # Time serialization
        start = time.time()
        redis_backend.set("perf_key", view)
        set_time = time.time() - start

        # Time deserialization
        start = time.time()
        result = redis_backend.get("perf_key")
        get_time = time.time() - start

        # Should be fast (sub-10ms for small data)
        assert set_time < 0.01  # 10ms
        assert get_time < 0.01  # 10ms
        assert result is not None

    def test_redis_ttl_expiration(self, redis_backend):
        """Test that Redis TTL works."""
        import redis

        client = redis.from_url("redis://localhost:6379/15")

        view = RustLiveView("<div>test</div>")
        redis_backend.set("ttl_key", view, ttl=1)  # 1 second TTL

        # Key should exist immediately
        assert redis_backend.get("ttl_key") is not None

        # Check Redis TTL (use worker-specific key prefix for parallel safety)
        redis_key = f"{redis_backend.key_prefix}ttl_key"
        ttl = client.ttl(redis_key)
        assert ttl > 0 and ttl <= 1

        # Wait for expiration
        time.sleep(1.5)

        # Key should be gone
        assert redis_backend.get("ttl_key") is None

    def test_redis_delete(self, redis_backend):
        """Test Redis delete operation."""
        view = RustLiveView("<div>test</div>")
        redis_backend.set("del_key", view)

        assert redis_backend.get("del_key") is not None

        deleted = redis_backend.delete("del_key")
        assert deleted is True

        assert redis_backend.get("del_key") is None

    def test_redis_stats(self, redis_backend):
        """Test Redis statistics."""
        # Add some sessions
        for i in range(5):
            view = RustLiveView(f"<div>{i}</div>")
            redis_backend.set(f"stats_key{i}", view)

        stats = redis_backend.get_stats()
        assert stats["backend"] == "redis"
        assert stats["total_sessions"] >= 5
        assert "redis_memory" in stats

    def test_redis_persistence(self, redis_backend):
        """Test that data persists across backend instances."""
        view = RustLiveView("<div>{{ message }}</div>")
        view.update_state({"message": "persistent"})

        # Store in first backend
        redis_backend.set("persist_key", view)

        # Create new backend instance (simulating server restart)
        # Use same key_prefix as first backend for parallel-safe testing
        backend2 = RedisStateBackend(
            redis_url="redis://localhost:6379/15",
            key_prefix=redis_backend.key_prefix,
        )

        # Retrieve from new instance
        result = backend2.get("persist_key")
        assert result is not None
        view2, _ = result
        html = view2.render()
        assert html == "<div>persistent</div>"

    def test_redis_vdom_preservation(self, redis_backend):
        """Test that VDOM is preserved through Redis."""
        view = RustLiveView("<div>{{ count }}</div>")
        view.update_state({"count": 1})

        # First render to create VDOM
        html1 = view.render()
        assert html1 == "<div>1</div>"

        # Store in Redis
        redis_backend.set("vdom_key", view)

        # Retrieve from Redis
        result = redis_backend.get("vdom_key")
        assert result is not None
        view2, _ = result

        # Verify state was preserved
        html2 = view2.render()
        assert html2 == "<div>1</div>"

        # Update and verify changes work
        view2.update_state({"count": 2})
        html3 = view2.render()
        assert html3 == "<div>2</div>"

        # Do another update to verify VDOM diffing works
        view2.update_state({"count": 3})
        html4, patches, version = view2.render_with_diff()
        # VDOM adds dj-id tracking attributes
        assert html4 == '<div dj-id="0">3</div>'
        # Version should be preserved/incremented from serialization
        assert version >= 1


class TestBackendConfiguration:
    """Test backend configuration and switching."""

    def test_get_backend_default(self):
        """Test that get_backend() returns InMemory by default."""
        # Reset backend
        set_backend(None)

        backend = get_backend()
        assert isinstance(backend, InMemoryStateBackend)

    def test_set_backend(self):
        """Test manually setting backend."""
        custom_backend = InMemoryStateBackend(default_ttl=7200)
        set_backend(custom_backend)

        backend = get_backend()
        assert backend is custom_backend

        # Cleanup
        set_backend(None)

    def test_backend_singleton(self):
        """Test that get_backend() returns same instance."""
        set_backend(None)

        backend1 = get_backend()
        backend2 = get_backend()

        assert backend1 is backend2


class TestIntegration:
    """Integration tests for state backend with LiveView."""

    def test_liveview_with_memory_backend(self, rf):
        """Test LiveView using InMemory backend."""
        from djust import LiveView

        set_backend(InMemoryStateBackend())

        class TestView(LiveView):
            template = "<div>{{ message }}</div>"

            def mount(self, request):
                self.message = "Hello Backend"

        view = TestView()
        request = rf.get("/")
        view.mount(request)

        html = view.render(request)
        assert "Hello Backend" in html

        # Cleanup
        set_backend(None)

    def test_session_cleanup_with_backend(self):
        """Test cleanup_expired_sessions() uses backend."""
        from djust.live_view import cleanup_expired_sessions

        backend = InMemoryStateBackend(default_ttl=1)
        set_backend(backend)

        # Add a session
        view = RustLiveView("<div>test</div>")
        backend.set("old_key", view)

        # Wait for expiration
        time.sleep(1.1)

        # Cleanup
        cleaned = cleanup_expired_sessions(ttl=1)
        assert cleaned == 1

        # Cleanup
        set_backend(None)

    def test_session_stats_with_backend(self):
        """Test get_session_stats() uses backend."""
        from djust.live_view import get_session_stats

        backend = InMemoryStateBackend()
        set_backend(backend)

        # Add sessions
        view1 = RustLiveView("<div>1</div>")
        view2 = RustLiveView("<div>2</div>")
        backend.set("key1", view1)
        backend.set("key2", view2)

        stats = get_session_stats()
        assert stats["total_sessions"] == 2
        assert "backend" in stats

        # Cleanup
        set_backend(None)


class TestHealthCheck:
    """Test health check functionality for state backends."""

    def test_inmemory_health_check_healthy(self):
        """Test InMemory backend reports healthy status."""
        backend = InMemoryStateBackend()

        result = backend.health_check()

        assert result["status"] == "healthy"
        assert result["backend"] == "memory"
        assert "latency_ms" in result
        assert result["latency_ms"] >= 0
        assert "total_sessions" in result

    def test_inmemory_health_check_with_sessions(self):
        """Test InMemory health check includes session count."""
        backend = InMemoryStateBackend()

        # Add some sessions
        view1 = RustLiveView("<div>1</div>")
        view2 = RustLiveView("<div>2</div>")
        backend.set("key1", view1)
        backend.set("key2", view2)

        result = backend.health_check()

        assert result["status"] == "healthy"
        assert result["total_sessions"] == 2

    def test_inmemory_health_check_handles_failure(self):
        """Test InMemory health check reports unhealthy on cache failure."""
        backend = InMemoryStateBackend()

        # Create a mock cache that raises an exception on write
        class FailingCache(dict):
            def __setitem__(self, key, value):
                if key == "__health_check__":
                    raise RuntimeError("Cache write failed")
                super().__setitem__(key, value)

        # Replace the cache with the failing mock
        backend._cache = FailingCache()

        result = backend.health_check()

        # Should report unhealthy with error message
        assert result["status"] == "unhealthy"
        assert result["backend"] == "memory"
        assert "error" in result
        assert "Cache write failed" in result["error"]
        assert "latency_ms" in result
        assert result["latency_ms"] >= 0

        # Verify test key was cleaned up (should not exist in cache)
        assert "__health_check__" not in backend._cache

    @pytest.mark.skipif(os.environ.get("REDIS_URL") is None, reason="Redis not available")
    def test_redis_health_check_healthy(self):
        """Test Redis backend reports healthy status."""
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/15")

        try:
            backend = RedisStateBackend(redis_url=redis_url, default_ttl=3600)
        except Exception:
            pytest.skip("Redis not available")

        result = backend.health_check()

        assert result["status"] == "healthy"
        assert result["backend"] == "redis"
        assert "latency_ms" in result
        assert result["latency_ms"] >= 0
        assert "details" in result

        # Verify Redis info is included
        details = result["details"]
        assert "redis_version" in details or len(details) == 0

    @pytest.mark.skipif(os.environ.get("REDIS_URL") is None, reason="Redis not available")
    def test_redis_health_check_measures_latency(self):
        """Test Redis health check measures latency."""
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/15")

        try:
            backend = RedisStateBackend(redis_url=redis_url, default_ttl=3600)
        except Exception:
            pytest.skip("Redis not available")

        result = backend.health_check()

        # Latency should be measurable (not zero, but reasonable)
        assert result["latency_ms"] > 0
        assert result["latency_ms"] < 1000  # Should be under 1 second

    def test_redis_health_check_connection_error(self):
        """Test Redis health check reports unhealthy on connection error."""
        try:
            # Try to connect to non-existent Redis server
            # This will fail in __init__'s ping() call
            backend = RedisStateBackend(redis_url="redis://localhost:9999/0", default_ttl=3600)
            pytest.fail("Expected ConnectionError but initialization succeeded")
        except Exception:
            # Expected - can't create backend without valid connection
            pass

        # Instead, create a backend with a valid connection, then override the client
        try:
            backend = RedisStateBackend(redis_url="redis://localhost:6379/15", default_ttl=3600)
        except Exception:
            pytest.skip("Redis not available for testing")

        # Override the client to simulate connection failure
        class FailingRedisClient:
            def ping(self):
                raise ConnectionError("Connection refused")

        backend._client = FailingRedisClient()

        result = backend.health_check()

        assert result["status"] == "unhealthy"
        assert result["backend"] == "redis"
        assert "error" in result
        assert "Connection refused" in result["error"]
