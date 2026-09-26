"""
Redis-backed presence backend for multi-node production deployments.

Uses Redis sorted sets (ZSET) for efficient presence tracking:
- Score = heartbeat timestamp (enables range-based stale cleanup)
- Member = user_id
- Metadata stored in a companion hash

This avoids serializing/deserializing full Python dicts on every operation,
unlike the Django cache approach.

Requires: pip install redis (or channels_redis which includes it)
"""

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .base import PresenceBackend

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
    if value < 0:
        logger.warning(
            "PRESENCE_CLEANUP_INTERVAL must be a non-negative number of seconds, got %r; using %s",
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


def read_presences(
    client: Any, zset_key: str, meta_key: str, cutoff: float
) -> List[Dict[str, Any]]:
    """The records of the members whose heartbeat is at or after ``cutoff`` (#3203).

    Two commands in one non-transactional round trip: ``ZRANGEBYSCORE`` and
    ``HGETALL``. Ordered by heartbeat, oldest first. A member without a
    record, or with a malformed one, is skipped.
    """
    pipe = client.pipeline(transaction=False)
    pipe.zrangebyscore(zset_key, min=cutoff, max="+inf")
    pipe.hgetall(meta_key)
    members, records = pipe.execute()
    if not members:
        return []
    presences = []
    for uid in members:
        raw = records.get(uid) if records else None
        if not raw:
            continue
        try:
            presences.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            logger.debug("Skipping a malformed presence record in %s", meta_key)
    return presences


class RedisPresenceBackend(PresenceBackend):
    """
    Redis-backed presence store using sorted sets.

    Redis keys used per presence group:
        djust:presence:{key}:zset   — sorted set (user_id → heartbeat timestamp)
        djust:presence:{key}:meta   — hash (user_id → JSON metadata)

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

    def join(self, presence_key: str, user_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        record = {
            "id": user_id,
            "joined_at": now,
            "meta": meta,
        }
        pipe = self._client.pipeline()
        pipe.zadd(self._zset_key(presence_key), {user_id: now})
        pipe.hset(self._meta_key(presence_key), user_id, json.dumps(record))
        # Set TTL on keys to auto-expire if no activity (2x timeout as safety margin)
        ttl = self._timeout * 3
        pipe.expire(self._zset_key(presence_key), ttl)
        pipe.expire(self._meta_key(presence_key), ttl)
        pipe.execute()

        logger.debug("User %s joined presence %s (Redis)", user_id, presence_key)
        return record

    def leave(self, presence_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        # Get metadata before removing
        raw = self._client.hget(self._meta_key(presence_key), user_id)
        record = json.loads(raw) if raw else None

        pipe = self._client.pipeline()
        pipe.zrem(self._zset_key(presence_key), user_id)
        pipe.hdel(self._meta_key(presence_key), user_id)
        pipe.execute()

        if record:
            logger.debug("User %s left presence %s (Redis)", user_id, presence_key)
        return record

    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """Active presences of ``presence_key``, oldest heartbeat first.

        Runs on every render of a presence view, so it costs a fixed two
        commands in one round trip (#3203): ``ZRANGEBYSCORE`` for the members
        whose heartbeat is within ``timeout`` and ``HGETALL`` for their
        records. A stale member is excluded by its score whether or not its
        entries were deleted yet; ``cleanup_stale`` runs at most once per
        ``cleanup_interval`` per key in this process.
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
        cutoff = time.time() - self._timeout
        return int(self._client.zcount(self._zset_key(presence_key), cutoff, "+inf"))

    def heartbeat(self, presence_key: str, user_id: str) -> None:
        now = time.time()
        pipe = self._client.pipeline()
        pipe.zadd(self._zset_key(presence_key), {user_id: now})
        # Refresh TTL
        ttl = self._timeout * 3
        pipe.expire(self._zset_key(presence_key), ttl)
        pipe.expire(self._meta_key(presence_key), ttl)
        pipe.execute()

    def cleanup_stale(self, presence_key: str) -> int:
        cutoff = time.time() - self._timeout
        # Get stale members
        stale = self._client.zrangebyscore(self._zset_key(presence_key), "-inf", cutoff)
        if not stale:
            return 0

        pipe = self._client.pipeline()
        # Remove from sorted set
        pipe.zremrangebyscore(self._zset_key(presence_key), "-inf", cutoff)
        # Remove metadata
        for uid in stale:
            pipe.hdel(self._meta_key(presence_key), uid)
        pipe.execute()

        logger.debug("Cleaned %d stale presences from %s", len(stale), presence_key)
        return len(stale)

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
