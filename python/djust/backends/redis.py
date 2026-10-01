"""
Redis-backed presence backend for multi-node production deployments.

Uses Redis sorted sets (ZSET) for efficient presence tracking, one member per
CONNECTION (#3254), aggregated per user when read:
- Score = heartbeat timestamp (enables range-based stale cleanup)
- Member = the connection's key: the bare user id for a legacy connection (no
  connection id), ``<user_id>\\x1f<connection_id>`` otherwise
- Metadata stored in a companion hash (member -> JSON record)

This avoids serializing/deserializing full Python dicts on every operation,
unlike the Django cache approach.

Requires: pip install redis (or channels_redis which includes it)
"""

import json
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    PresenceBackend,
    aggregate_by_user,
    connection_member,
    member_belongs_to,
    merge_connection_records,
)

logger = logging.getLogger(__name__)

PRESENCE_TIMEOUT = 60  # seconds

# How often ``list()`` may run ``cleanup_stale`` for one presence key, per
# process (#3203). Reads never depend on it: a stale member is filtered out by
# its heartbeat score, so the cleanup only reclaims space.
CLEANUP_INTERVAL = 30  # seconds

# The per-key throttle map is pruned of expired entries once it grows past
# this size, and after that only when it has doubled since the last prune, so
# the O(n) scan is amortised over at least n insertions.
_CLEANUP_MAP_PRUNE_AT = 10_000


def presence_cleanup_interval(config: Dict[str, Any]) -> float:
    """``DJUST_CONFIG['PRESENCE_CLEANUP_INTERVAL']`` in seconds, or the default.

    A value that is not a non-negative number is logged and ignored.
    """
    raw = config.get("PRESENCE_CLEANUP_INTERVAL", CLEANUP_INTERVAL)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = -1.0
    if not math.isfinite(value) or value < 0:
        logger.warning(
            "PRESENCE_CLEANUP_INTERVAL must be a finite, non-negative number of seconds, got %r; using %s",
            raw,
            CLEANUP_INTERVAL,
        )
        return float(CLEANUP_INTERVAL)
    return value


class CleanupThrottle:
    """At most one ``cleanup_stale`` per presence key per ``interval`` seconds (#3203).

    Per process and thread-safe. Uses ``time.monotonic()``, so a wall-clock
    step cannot suppress cleanups; presence scores stay on ``time.time()``.
    """

    def __init__(self, interval: float) -> None:
        self.interval = float(interval)
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._prune_at = _CLEANUP_MAP_PRUNE_AT

    def due(self, presence_key: str) -> bool:
        """Whether a cleanup should run now; records it when it should."""
        now = time.monotonic()
        with self._lock:
            last = self._last.get(presence_key)
            if last is not None and now - last < self.interval:
                return False
            self._last[presence_key] = now
            if len(self._last) > self._prune_at:
                horizon = now - self.interval
                for key in [k for k, t in self._last.items() if t < horizon]:
                    del self._last[key]
                self._prune_at = max(_CLEANUP_MAP_PRUNE_AT, 2 * len(self._last))
            return True


def _parse_record(raw: Any) -> Optional[Dict[str, Any]]:
    """A stored connection record, or ``None`` when it is missing or malformed."""
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(record, dict) or "id" not in record or "joined_at" not in record:
        return None
    return record


def read_presences(
    client: Any, zset_key: str, meta_key: str, cutoff: float
) -> List[Dict[str, Any]]:
    """The users whose connections have a heartbeat at or after ``cutoff`` (#3203).

    Two commands in one non-transactional round trip: ``ZRANGEBYSCORE`` and
    ``HGETALL``. A user with several live connections is one record
    (:func:`~djust.backends.base.aggregate_by_user`, #3254). Users are ordered by
    their oldest live connection's heartbeat. A member without a record, or
    with a malformed one, is skipped.
    """
    pipe = client.pipeline(transaction=False)
    pipe.zrangebyscore(zset_key, min=cutoff, max="+inf")
    pipe.hgetall(meta_key)
    members, records = pipe.execute()
    if not members:
        return []
    connections = []
    for member in members:
        record = _parse_record(records.get(member) if records else None)
        if record is None:
            logger.debug("Skipping a missing or malformed presence record in %s", meta_key)
            continue
        connections.append(record)
    return aggregate_by_user(connections)


def join_connection_records(
    client: Any,
    zset_key: str,
    meta_key: str,
    user_id: str,
    connection_id: Optional[str],
    meta: Dict[str, Any],
    *,
    now: float,
    cutoff: float,
    ttl: float,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Add or refresh one connection; returns ``(user record, first)`` (#3254).

    ``first`` is read inside the ``MULTI`` that writes the connection, so two
    nodes joining a user's first two connections at once cannot both see an
    empty group.
    """
    member = connection_member(user_id, connection_id)
    previous = _parse_record(client.hget(meta_key, member))
    record = {
        "id": user_id,
        **(extra or {}),
        "connection_id": connection_id,
        "joined_at": previous["joined_at"] if previous else now,
        "updated_at": now,
        "meta": meta,
    }
    pipe = client.pipeline(transaction=True)
    pipe.zrangebyscore(zset_key, min=cutoff, max="+inf")
    pipe.zadd(zset_key, {member: now})
    pipe.hset(meta_key, member, json.dumps(record))
    # Set TTL on keys to auto-expire if no activity (3x timeout as safety margin)
    pipe.expire(zset_key, ttl)
    pipe.expire(meta_key, ttl)
    alive_before = pipe.execute()[0]
    first = not any(member_belongs_to(m, user_id) for m in alive_before)
    siblings = [m for m in alive_before if m != member and member_belongs_to(m, user_id)]
    others = [parsed for _, parsed in _read_records(client, meta_key, siblings)]
    return merge_connection_records([*others, record]), first


def _read_records(
    client: Any, meta_key: str, members: List[str]
) -> List[Tuple[str, Dict[str, Any]]]:
    """The parsed records of ``members`` (those missing or malformed are left out)."""
    if not members:
        return []
    out = []
    for member, raw in zip(members, client.hmget(meta_key, members)):
        parsed = _parse_record(raw)
        if parsed is not None:
            out.append((member, parsed))
    return out


def leave_connection_records(
    client: Any,
    zset_key: str,
    meta_key: str,
    user_id: str,
    connection_id: Optional[str],
    *,
    cutoff: float,
) -> Optional[Dict[str, Any]]:
    """Remove one connection of ``user_id``, or all of them when ``connection_id`` is None.

    Returns the user's record when none of their connections is left alive, and
    ``None`` when another live connection keeps them present or there was
    nothing to remove (#3254). The read and the removal share one ``MULTI``.
    """
    if connection_id is None:
        members = [
            m
            for m, raw in client.hgetall(meta_key).items()
            if (_parse_record(raw) or {}).get("id") == user_id
        ]
    else:
        members = [connection_member(user_id, connection_id)]
    if not members:
        return None
    pipe = client.pipeline(transaction=True)
    pipe.zrangebyscore(zset_key, min=cutoff, max="+inf")
    pipe.hmget(meta_key, members)
    pipe.zrem(zset_key, *members)
    pipe.hdel(meta_key, *members)
    alive_before, raw_records, _, _ = pipe.execute()
    removed = [r for r in map(_parse_record, raw_records) if r is not None]
    if not removed:
        return None
    gone = set(members)
    if any(member_belongs_to(m, user_id) and m not in gone for m in alive_before):
        return None
    return merge_connection_records(removed)


def heartbeat_connection_records(
    client: Any,
    zset_key: str,
    meta_key: str,
    user_id: str,
    connection_id: Optional[str],
    *,
    now: float,
    ttl: float,
) -> None:
    """Refresh one connection of ``user_id``, or all of theirs when ``connection_id`` is None.

    Only an existing connection is refreshed (``ZADD XX``): a heartbeat that
    arrives after the connection expired does not bring back a member with no
    record (#3254).
    """
    if connection_id is None:
        members = [m for m in client.zrange(zset_key, 0, -1) if member_belongs_to(m, user_id)]
    else:
        members = [connection_member(user_id, connection_id)]
    if not members:
        return
    pipe = client.pipeline()
    for member in members:
        pipe.zadd(zset_key, {member: now}, xx=True)
    pipe.expire(zset_key, ttl)
    pipe.expire(meta_key, ttl)
    pipe.execute()


def cleanup_stale_records(client: Any, zset_key: str, meta_key: str, cutoff: float) -> int:
    """Remove the connections with no heartbeat since ``cutoff``; returns how many."""
    stale = client.zrangebyscore(zset_key, "-inf", cutoff)
    if not stale:
        return 0
    pipe = client.pipeline()
    # Remove from sorted set
    pipe.zremrangebyscore(zset_key, "-inf", cutoff)
    # Remove metadata
    pipe.hdel(meta_key, *stale)
    pipe.execute()
    return len(stale)


class RedisPresenceBackend(PresenceBackend):
    """
    Redis-backed presence store using sorted sets.

    Redis keys used per presence group (one entry per CONNECTION, #3254; a
    legacy connection with no connection id is keyed by the bare user id, so
    records written before #3254 read as that user's one connection):
        djust:presence:{key}:zset   — sorted set (connection → heartbeat timestamp)
        djust:presence:{key}:meta   — hash (connection → JSON record)

    Benefits over the Django-cache approach:
        - Atomic operations (no read-modify-write races)
        - Efficient range queries for stale cleanup (ZRANGEBYSCORE)
        - Works across all nodes sharing the same Redis
        - No Python-level locking needed
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "djust:presence",
        timeout: int = PRESENCE_TIMEOUT,
        cleanup_interval: float = CLEANUP_INTERVAL,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError(
                "redis is required for RedisPresenceBackend. Install with: pip install redis"
            )

        self._client = redis_lib.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix
        self._timeout = timeout
        self._cleanup_throttle = CleanupThrottle(cleanup_interval)

        # Verify connection
        try:
            self._client.ping()
            logger.info("RedisPresenceBackend connected to %s", redis_url)
        except Exception as e:
            logger.error("RedisPresenceBackend failed to connect: %s", e)
            raise

    def _zset_key(self, presence_key: str) -> str:
        return f"{self._prefix}:{presence_key}:zset"

    def _meta_key(self, presence_key: str) -> str:
        return f"{self._prefix}:{presence_key}:meta"

    def join(
        self,
        presence_key: str,
        user_id: str,
        meta: Dict[str, Any],
        connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._join(presence_key, user_id, connection_id, meta)[0]

    def join_connection(
        self, presence_key: str, user_id: str, connection_id: str, meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        return self._join(presence_key, user_id, connection_id, meta)

    def _join(
        self, presence_key: str, user_id: str, connection_id: Optional[str], meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        now = time.time()
        result = join_connection_records(
            self._client,
            self._zset_key(presence_key),
            self._meta_key(presence_key),
            user_id,
            connection_id,
            meta,
            now=now,
            cutoff=now - self._timeout,
            ttl=self._timeout * 3,
        )
        logger.debug("User %s joined presence %s (Redis)", user_id, presence_key)
        return result

    def leave(
        self, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        record = leave_connection_records(
            self._client,
            self._zset_key(presence_key),
            self._meta_key(presence_key),
            user_id,
            connection_id,
            cutoff=time.time() - self._timeout,
        )
        if record:
            logger.debug("User %s left presence %s (Redis)", user_id, presence_key)
        return record

    def leave_connection(
        self, presence_key: str, user_id: str, connection_id: str
    ) -> Optional[Dict[str, Any]]:
        return self.leave(presence_key, user_id, connection_id)

    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """Active presences of ``presence_key``, one per user, oldest heartbeat first.

        Runs on every render of a presence view, so it costs a fixed two
        commands in one round trip (#3203): ``ZRANGEBYSCORE`` for the connections
        whose heartbeat is within ``timeout`` and ``HGETALL`` for their
        records, which are then aggregated per user (#3254). A stale connection
        is excluded by its score whether or not its entries were deleted yet;
        ``cleanup_stale`` runs at most once per ``cleanup_interval`` per key in
        this process.
        """
        if self._cleanup_throttle.due(presence_key):
            self.cleanup_stale(presence_key)
        return read_presences(
            self._client,
            self._zset_key(presence_key),
            self._meta_key(presence_key),
            time.time() - self._timeout,
        )

    def count(self, presence_key: str) -> int:
        return len(
            read_presences(
                self._client,
                self._zset_key(presence_key),
                self._meta_key(presence_key),
                time.time() - self._timeout,
            )
        )

    def heartbeat(
        self, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> None:
        heartbeat_connection_records(
            self._client,
            self._zset_key(presence_key),
            self._meta_key(presence_key),
            user_id,
            connection_id,
            now=time.time(),
            ttl=self._timeout * 3,
        )

    def heartbeat_connection(self, presence_key: str, user_id: str, connection_id: str) -> None:
        self.heartbeat(presence_key, user_id, connection_id)

    def cleanup_stale(self, presence_key: str) -> int:
        removed = cleanup_stale_records(
            self._client,
            self._zset_key(presence_key),
            self._meta_key(presence_key),
            time.time() - self._timeout,
        )
        if removed:
            logger.debug("Cleaned %d stale presences from %s", removed, presence_key)
        return removed

    def health_check(self) -> Dict[str, Any]:
        start = time.time()
        try:
            self._client.ping()
            latency = (time.time() - start) * 1000
            return {
                "status": "healthy",
                "backend": "redis",
                "latency_ms": round(latency, 2),
            }
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {
                "status": "unhealthy",
                "backend": "redis",
                "latency_ms": round(latency, 2),
                "error": str(e),
            }
