"""
Tests for djust.backends presence backends (memory and redis).

Redis tests use unittest.mock to avoid requiring a real Redis server.
"""

import json
import time
import unittest
from unittest.mock import MagicMock, patch, call

from djust.backends.base import PresenceBackend
from djust.backends.memory import InMemoryPresenceBackend


class TestInMemoryPresenceBackend(unittest.TestCase):
    """Tests for InMemoryPresenceBackend."""

    def setUp(self):
        self.backend = InMemoryPresenceBackend(timeout=5)

    def test_implements_interface(self):
        self.assertIsInstance(self.backend, PresenceBackend)

    def test_join_and_list(self):
        record = self.backend.join("room:1", "alice", {"name": "Alice"})
        self.assertEqual(record["id"], "alice")
        self.assertEqual(record["meta"]["name"], "Alice")
        self.assertIn("joined_at", record)

        presences = self.backend.list("room:1")
        self.assertEqual(len(presences), 1)
        self.assertEqual(presences[0]["id"], "alice")

    def test_multiple_users(self):
        self.backend.join("room:1", "alice", {"name": "Alice"})
        self.backend.join("room:1", "bob", {"name": "Bob"})
        self.assertEqual(self.backend.count("room:1"), 2)

    def test_leave(self):
        self.backend.join("room:1", "alice", {"name": "Alice"})
        record = self.backend.leave("room:1", "alice")
        self.assertIsNotNone(record)
        self.assertEqual(record["id"], "alice")
        self.assertEqual(self.backend.count("room:1"), 0)

    def test_leave_nonexistent(self):
        result = self.backend.leave("room:1", "ghost")
        self.assertIsNone(result)

    def test_heartbeat(self):
        self.backend.join("room:1", "alice", {})
        old_hb = self.backend._heartbeats[("room:1", "alice")]
        time.sleep(0.01)
        self.backend.heartbeat("room:1", "alice")
        new_hb = self.backend._heartbeats[("room:1", "alice")]
        self.assertGreater(new_hb, old_hb)

    def test_stale_cleanup(self):
        # Use very short timeout
        backend = InMemoryPresenceBackend(timeout=0.01)
        backend.join("room:1", "alice", {})
        time.sleep(0.02)
        presences = backend.list("room:1")  # triggers cleanup
        self.assertEqual(len(presences), 0)

    def test_cleanup_returns_count(self):
        backend = InMemoryPresenceBackend(timeout=0.01)
        backend.join("room:1", "alice", {})
        backend.join("room:1", "bob", {})
        time.sleep(0.02)
        removed = backend.cleanup_stale("room:1")
        self.assertEqual(removed, 2)

    def test_separate_groups(self):
        self.backend.join("room:1", "alice", {})
        self.backend.join("room:2", "bob", {})
        self.assertEqual(self.backend.count("room:1"), 1)
        self.assertEqual(self.backend.count("room:2"), 1)

    def test_health_check(self):
        self.backend.join("room:1", "alice", {})
        health = self.backend.health_check()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["backend"], "memory")
        self.assertEqual(health["total_presences"], 1)

    def test_empty_list(self):
        self.assertEqual(self.backend.list("nonexistent"), [])
        self.assertEqual(self.backend.count("nonexistent"), 0)

    def test_rejoin_updates_record(self):
        self.backend.join("room:1", "alice", {"color": "red"})
        self.backend.join("room:1", "alice", {"color": "blue"})
        presences = self.backend.list("room:1")
        self.assertEqual(len(presences), 1)
        self.assertEqual(presences[0]["meta"]["color"], "blue")


class TestRedisPresenceBackend(unittest.TestCase):
    """Tests for RedisPresenceBackend using mocked Redis."""

    def setUp(self):
        # Create backend with mocked client, bypassing __init__
        from djust.backends.redis import RedisPresenceBackend
        self.mock_redis_client = MagicMock()
        self.mock_redis_client.ping.return_value = True
        self.backend = RedisPresenceBackend.__new__(RedisPresenceBackend)
        self.backend._client = self.mock_redis_client
        self.backend._prefix = "djust:presence"
        self.backend._timeout = 60

    def test_implements_interface(self):
        self.assertIsInstance(self.backend, PresenceBackend)

    def test_join_uses_pipeline(self):
        mock_pipe = MagicMock()
        self.mock_redis_client.pipeline.return_value = mock_pipe

        record = self.backend.join("room:1", "alice", {"name": "Alice"})

        self.assertEqual(record["id"], "alice")
        self.assertEqual(record["meta"]["name"], "Alice")
        mock_pipe.zadd.assert_called_once()
        mock_pipe.hset.assert_called_once()
        mock_pipe.execute.assert_called_once()

    def test_leave_removes_from_both(self):
        self.mock_redis_client.hget.return_value = json.dumps({
            "id": "alice", "joined_at": 1000, "meta": {"name": "Alice"}
        })
        mock_pipe = MagicMock()
        self.mock_redis_client.pipeline.return_value = mock_pipe

        record = self.backend.leave("room:1", "alice")

        self.assertEqual(record["id"], "alice")
        mock_pipe.zrem.assert_called_once()
        mock_pipe.hdel.assert_called_once()

    def test_leave_nonexistent(self):
        self.mock_redis_client.hget.return_value = None
        mock_pipe = MagicMock()
        self.mock_redis_client.pipeline.return_value = mock_pipe

        result = self.backend.leave("room:1", "ghost")
        self.assertIsNone(result)

    def test_count_uses_zcount(self):
        self.mock_redis_client.zcount.return_value = 3
        count = self.backend.count("room:1")
        self.assertEqual(count, 3)
        self.mock_redis_client.zcount.assert_called_once()

    def test_heartbeat_updates_score(self):
        mock_pipe = MagicMock()
        self.mock_redis_client.pipeline.return_value = mock_pipe

        self.backend.heartbeat("room:1", "alice")

        mock_pipe.zadd.assert_called_once()
        mock_pipe.execute.assert_called_once()

    def test_cleanup_stale(self):
        self.mock_redis_client.zrangebyscore.return_value = ["stale_user"]
        mock_pipe = MagicMock()
        self.mock_redis_client.pipeline.return_value = mock_pipe

        removed = self.backend.cleanup_stale("room:1")

        self.assertEqual(removed, 1)
        mock_pipe.zremrangebyscore.assert_called_once()
        mock_pipe.hdel.assert_called_once()

    def test_cleanup_stale_none(self):
        self.mock_redis_client.zrangebyscore.return_value = []
        removed = self.backend.cleanup_stale("room:1")
        self.assertEqual(removed, 0)

    def test_health_check_healthy(self):
        self.mock_redis_client.ping.return_value = True
        health = self.backend.health_check()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["backend"], "redis")

    def test_health_check_unhealthy(self):
        self.mock_redis_client.ping.side_effect = ConnectionError("down")
        health = self.backend.health_check()
        self.assertEqual(health["status"], "unhealthy")

    def test_list_fetches_metadata(self):
        self.mock_redis_client.zrangebyscore.side_effect = [
            [],  # cleanup_stale call
            ["alice", "bob"],  # list call
        ]
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [
            json.dumps({"id": "alice", "joined_at": 1000, "meta": {}}),
            json.dumps({"id": "bob", "joined_at": 1001, "meta": {}}),
        ]
        self.mock_redis_client.pipeline.return_value = mock_pipe

        presences = self.backend.list("room:1")
        self.assertEqual(len(presences), 2)


class TestPresenceBackendRegistry(unittest.TestCase):
    """Tests for the presence backend registry."""

    def test_default_is_memory(self):
        from djust.backends.registry import reset_presence_backend, get_presence_backend
        reset_presence_backend()

        with patch("djust.backends.registry.settings", create=True) as mock_settings:
            mock_settings.DJUST_CONFIG = {}
            backend = get_presence_backend()
            self.assertIsInstance(backend, InMemoryPresenceBackend)

        reset_presence_backend()

    def test_set_backend(self):
        from djust.backends.registry import set_presence_backend, get_presence_backend, reset_presence_backend
        mock_backend = MagicMock(spec=PresenceBackend)
        set_presence_backend(mock_backend)
        self.assertIs(get_presence_backend(), mock_backend)
        reset_presence_backend()


if __name__ == "__main__":
    unittest.main()
