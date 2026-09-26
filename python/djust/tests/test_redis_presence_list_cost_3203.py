"""``RedisPresenceBackend.list()`` costs a fixed number of Redis commands (#3203).

``list()`` runs on every render of a presence view (``list_presences()`` in
``get_context_data``, ``online_count``). It used to cost 2 + N commands: a
``cleanup_stale`` ``ZRANGEBYSCORE`` (plus a MULTI/EXEC write pipeline when
anything was stale), a second ``ZRANGEBYSCORE``, and one ``HGET`` per member.

Now a read is one ``ZRANGEBYSCORE`` and one ``HGETALL``, pipelined, whatever
the member count. ``cleanup_stale`` runs at most once per ``cleanup_interval``
per key per process, and a stale member is excluded from the list by its score
even when no cleanup ran.

Commands are counted where fakeredis receives them, so pipelined commands and
MULTI/EXEC are each counted, as a Redis server would see them.
"""

from __future__ import annotations

import json
from collections import Counter
from contextlib import contextmanager
from unittest.mock import patch

import pytest

fakeredis = pytest.importorskip("fakeredis")
from fakeredis._basefakesocket import BaseFakeSocket  # noqa: E402

from djust.backends.redis import RedisPresenceBackend  # noqa: E402


@pytest.fixture
def backend():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    with patch("redis.from_url", return_value=client):
        yield RedisPresenceBackend(redis_url="redis://fake:6379/0", timeout=60)


@contextmanager
def _count_commands():
    seen: Counter = Counter()
    original = BaseFakeSocket._process_command

    def _record(self, fields):
        if fields:
            name = fields[0]
            seen[(name.decode() if isinstance(name, bytes) else str(name)).upper()] += 1
        return original(self, fields)

    with patch.object(BaseFakeSocket, "_process_command", _record):
        yield seen


def _join_many(backend, key: str, n: int) -> None:
    for i in range(n):
        backend.join(key, f"u{i}", {"name": f"user {i}"})


@pytest.mark.parametrize("members", [1, 10, 50])
def test_list_costs_two_commands_whatever_the_member_count(backend, members):
    _join_many(backend, "room:a", members)
    backend.list("room:a")  # the first read of a key may run the cleanup

    with _count_commands() as seen:
        listed = backend.list("room:a")

    assert len(listed) == members
    assert dict(seen) == {"ZRANGEBYSCORE": 1, "HGETALL": 1}


def test_list_returns_the_joined_records_in_heartbeat_order(backend):
    with patch("djust.backends.redis.time.time", return_value=1000.0):
        backend.join("room:a", "alice", {"c": "red"})
    with patch("djust.backends.redis.time.time", return_value=1001.0):
        backend.join("room:a", "bob", {"c": "blue"})
    with patch("djust.backends.redis.time.time", return_value=1002.0):
        listed = backend.list("room:a")
    assert [p["id"] for p in listed] == ["alice", "bob"]
    assert listed[0]["meta"] == {"c": "red"}


def test_cleanup_runs_at_most_once_per_interval_per_key(backend):
    _join_many(backend, "room:a", 3)
    _join_many(backend, "room:b", 3)
    clock = [10_000.0]
    with patch("djust.backends.redis.time.time", side_effect=lambda: clock[0]):
        with _count_commands() as seen:
            for _ in range(20):
                backend.list("room:a")
                backend.list("room:b")
        # One cleanup probe per key, not one per read.
        assert seen["ZRANGEBYSCORE"] == 40 + 2

        clock[0] += backend._cleanup_interval + 1
        with _count_commands() as seen:
            backend.list("room:a")
            backend.list("room:a")
        assert seen["ZRANGEBYSCORE"] == 2 + 1


def test_a_stale_member_is_excluded_even_when_no_cleanup_runs(backend):
    clock = [10_000.0]
    with patch("djust.backends.redis.time.time", side_effect=lambda: clock[0]):
        backend.join("room:a", "alice", {})
        backend.join("room:a", "bob", {})

        clock[0] += 40
        backend.heartbeat("room:a", "bob")
        backend.list("room:a")  # runs this interval's cleanup; alice not stale yet
        clock[0] += 21  # alice's heartbeat is now 61 s old; bob's 21 s

        assert clock[0] - backend._last_cleanup["room:a"] < backend._cleanup_interval
        with _count_commands() as seen:
            listed = backend.list("room:a")
        assert "ZREMRANGEBYSCORE" not in seen  # no cleanup ran
        assert [p["id"] for p in listed] == ["bob"]
        assert backend.count("room:a") == 1


def test_stale_members_are_still_removed_once_the_interval_passes(backend):
    clock = [10_000.0]
    with patch("djust.backends.redis.time.time", side_effect=lambda: clock[0]):
        backend.join("room:a", "alice", {})
        backend.list("room:a")
        clock[0] += backend._cleanup_interval + 61
        backend.list("room:a")
    assert backend._client.zcard(backend._zset_key("room:a")) == 0
    assert backend._client.hlen(backend._meta_key("room:a")) == 0


def test_a_malformed_record_is_skipped(backend):
    backend.join("room:a", "alice", {})
    backend.join("room:a", "bob", {})
    backend._client.hset(backend._meta_key("room:a"), "alice", "{not json")
    assert [p["id"] for p in backend.list("room:a")] == ["bob"]


def test_a_member_without_a_record_is_skipped(backend):
    backend.join("room:a", "alice", {})
    backend._client.zadd(backend._zset_key("room:a"), {"ghost": 10_000_000_000.0})
    listed = backend.list("room:a")
    assert [p["id"] for p in listed] == ["alice"]
    assert json.loads(backend._client.hget(backend._meta_key("room:a"), "alice"))["id"] == "alice"


def test_empty_group_lists_nothing(backend):
    assert backend.list("room:none") == []
