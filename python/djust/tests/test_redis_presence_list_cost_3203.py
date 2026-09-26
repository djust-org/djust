"""Redis presence ``list()`` costs a fixed number of Redis commands (#3203).

``list()`` runs on every render of a presence view (``list_presences()`` in
``get_context_data``, ``online_count``). It used to cost 2 + N commands: a
``cleanup_stale`` ``ZRANGEBYSCORE`` (plus a MULTI/EXEC write pipeline when
anything was stale), a second ``ZRANGEBYSCORE``, and one ``HGET`` per member.

Now a read is one ``ZRANGEBYSCORE`` and one ``HGETALL``, pipelined, whatever
the member count. ``cleanup_stale`` runs at most once per ``cleanup_interval``
per key per process (on the monotonic clock), and a stale member is excluded
from the list by its score even when no cleanup ran. Both Redis backends share
this read: ``RedisPresenceBackend`` and ``djust.tenants``'
``TenantAwareRedisBackend``.

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

from djust.backends import redis as redis_backends  # noqa: E402
from djust.backends.redis import (  # noqa: E402
    CleanupThrottle,
    RedisPresenceBackend,
    presence_cleanup_interval,
)
from djust.tenants.backends import TenantAwareRedisBackend  # noqa: E402

INTERVAL = 30.0


def _make(kind: str):
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    with patch("redis.from_url", return_value=client):
        if kind == "plain":
            return RedisPresenceBackend(redis_url="redis://fake:6379/0", timeout=60)
        return TenantAwareRedisBackend(tenant_id="acme", redis_url="redis://fake:6379/0")


@pytest.fixture(params=["plain", "tenant"])
def backend(request):
    return _make(request.param)


@contextmanager
def _clock(start: float = 10_000.0):
    """One controllable clock for both the scores (time) and the throttle (monotonic)."""
    now = [start]
    with (
        patch("djust.backends.redis.time.time", side_effect=lambda: now[0]),
        patch("djust.backends.redis.time.monotonic", side_effect=lambda: now[0]),
        patch("djust.tenants.backends.time.time", side_effect=lambda: now[0]),
    ):
        yield now


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
    with _clock() as now:
        backend.join("room:a", "alice", {"c": "red"})
        now[0] += 1
        backend.join("room:a", "bob", {"c": "blue"})
        now[0] += 1
        listed = backend.list("room:a")
    assert [p["id"] for p in listed] == ["alice", "bob"]
    assert listed[0]["meta"] == {"c": "red"}


def test_cleanup_runs_at_most_once_per_interval_per_key(backend):
    _join_many(backend, "room:a", 3)
    _join_many(backend, "room:b", 3)
    with _clock() as now:
        with _count_commands() as seen:
            for _ in range(20):
                backend.list("room:a")
                backend.list("room:b")
        # One cleanup probe per key, not one per read.
        assert seen["ZRANGEBYSCORE"] == 40 + 2

        now[0] += INTERVAL + 1
        with _count_commands() as seen:
            backend.list("room:a")
            backend.list("room:a")
        assert seen["ZRANGEBYSCORE"] == 2 + 1


def test_a_stale_member_is_excluded_even_when_no_cleanup_runs(backend):
    with _clock() as now:
        backend.join("room:a", "alice", {})
        backend.join("room:a", "bob", {})

        now[0] += 40
        backend.heartbeat("room:a", "bob")
        backend.list("room:a")  # runs this interval's cleanup; alice not stale yet
        now[0] += 21  # alice's heartbeat is now 61 s old; bob's 21 s

        with _count_commands() as seen:
            listed = backend.list("room:a")
        assert "ZREMRANGEBYSCORE" not in seen  # no cleanup ran
        assert [p["id"] for p in listed] == ["bob"]
        assert backend.count("room:a") == 1


def test_stale_members_are_still_removed_once_the_interval_passes(backend):
    with _clock() as now:
        backend.join("room:a", "alice", {})
        backend.list("room:a")
        now[0] += INTERVAL + 61
        backend.list("room:a")
    assert backend._client.zcard(backend._zset_key("room:a")) == 0
    assert backend._client.hlen(backend._meta_key("room:a")) == 0


def test_a_wall_clock_step_back_does_not_suppress_cleanup():
    """The throttle runs on the monotonic clock, not the wall clock."""
    throttle = CleanupThrottle(INTERVAL)
    with (
        patch("djust.backends.redis.time.monotonic", side_effect=[100.0, 100.0 + INTERVAL + 1]),
        patch("djust.backends.redis.time.time", side_effect=AssertionError("wall clock read")),
    ):
        assert throttle.due("k") is True
        assert throttle.due("k") is True


def test_the_throttle_map_is_pruned_but_not_rescanned_on_every_insert():
    throttle = CleanupThrottle(INTERVAL)
    now = [0.0]
    scans = []
    real_items = dict.items

    class _Spy(dict):
        def items(self):
            scans.append(len(self))
            return real_items(self)

    throttle._last = _Spy()
    with (
        patch.object(redis_backends, "_CLEANUP_MAP_PRUNE_AT", 5),
        patch("djust.backends.redis.time.monotonic", side_effect=lambda: now[0]),
    ):
        throttle._prune_at = 5
        for i in range(20):  # every key is fresh, so nothing is expired
            throttle.due(f"k{i}")
    # Pruned at 6, then at 13 (> 2 * 6), not on every insert past 5.
    assert scans == [6, 13]

    now[0] += INTERVAL + 1
    with patch("djust.backends.redis.time.monotonic", side_effect=lambda: now[0]):
        for i in range(20, 27):
            throttle.due(f"k{i}")
    assert len(throttle._last) < 20  # the expired keys were dropped


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


def test_cleanup_interval_comes_from_config():
    assert presence_cleanup_interval({}) == INTERVAL
    assert presence_cleanup_interval({"PRESENCE_CLEANUP_INTERVAL": 5}) == 5.0
    assert presence_cleanup_interval({"PRESENCE_CLEANUP_INTERVAL": "nope"}) == INTERVAL
    assert presence_cleanup_interval({"PRESENCE_CLEANUP_INTERVAL": -1}) == INTERVAL

    from djust.backends.registry import _create_presence_backend

    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    with patch("redis.from_url", return_value=client):
        backend = _create_presence_backend("redis", {"PRESENCE_CLEANUP_INTERVAL": 7})
    assert backend._cleanup_throttle.interval == 7.0


def test_tenant_manager_passes_the_configured_interval():
    from djust.tenants.backends import TenantPresenceManager

    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    TenantPresenceManager.clear_cache()
    try:
        with (
            patch("redis.from_url", return_value=client),
            patch(
                "djust.config.get_djust_config",
                return_value={"PRESENCE_BACKEND": "tenant_redis", "PRESENCE_CLEANUP_INTERVAL": 9},
            ),
        ):
            backend = TenantPresenceManager.for_tenant("acme")
        assert isinstance(backend, TenantAwareRedisBackend)
        assert backend._cleanup_throttle.interval == 9.0
    finally:
        TenantPresenceManager.clear_cache()
