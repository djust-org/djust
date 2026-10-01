"""
Tenant-aware backends for state and presence storage.

Provides tenant isolation for:
- State storage (session state, LiveView assigns)
- Presence tracking
- Cache operations

These backends prefix all keys with tenant ID to prevent cross-tenant
data leakage.
"""

import functools
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from ..backends.base import (
    PresenceBackend,
    aggregate_by_user,
    connection_member,
    member_belongs_to,
    merge_connection_records,
)
from ..backends.redis import (
    CLEANUP_INTERVAL,
    CleanupThrottle,
    cleanup_stale_records,
    heartbeat_connection_records,
    join_connection_records,
    leave_connection_records,
    presence_cleanup_interval,
    read_presences,
)

logger = logging.getLogger(__name__)


class TenantAwareBackendMixin:
    """
    Mixin that adds tenant-scoping to backend keys.

    All keys are prefixed with tenant ID to ensure isolation.
    """

    def __init__(self, tenant_id: str, *args: Any, **kwargs: Any) -> None:
        self._tenant_id = tenant_id
        super().__init__(*args, **kwargs)

    @property
    def tenant_id(self) -> str:
        """Get the tenant ID for this backend instance."""
        return self._tenant_id

    def _tenant_key(self, key: str) -> str:
        """Prefix a key with tenant ID."""
        return f"tenant:{self._tenant_id}:{key}"


class TenantAwareRedisBackend(TenantAwareBackendMixin, PresenceBackend):
    """
    Tenant-scoped Redis backend for presence tracking.

    Wraps RedisPresenceBackend with automatic tenant prefixing.

    Usage::

        # Get backend for a specific tenant
        backend = TenantAwareRedisBackend(
            tenant_id='acme',
            redis_url='redis://localhost:6379/0'
        )

        # All operations are now scoped to 'acme' tenant
        backend.join('document:123', 'user1', {'name': 'Alice'})
        # Stored under: djust:tenant:acme:document:123:zset

    Configuration::

        DJUST_CONFIG = {
            'PRESENCE_BACKEND': 'tenant_redis',
            'PRESENCE_REDIS_URL': 'redis://localhost:6379/0',
        }
    """

    PRESENCE_TIMEOUT = 60

    def __init__(
        self,
        tenant_id: str,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "djust",
        timeout: int = PRESENCE_TIMEOUT,
        cleanup_interval: float = CLEANUP_INTERVAL,
    ) -> None:
        super().__init__(tenant_id=tenant_id)
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError(
                "redis is required for TenantAwareRedisBackend. Install with: pip install redis"
            )

        self._tenant_id = tenant_id
        self._client = redis_lib.from_url(redis_url, decode_responses=True)
        self._base_prefix = key_prefix
        self._timeout = timeout
        self._cleanup_throttle = CleanupThrottle(cleanup_interval)

        # Verify connection
        try:
            self._client.ping()
            logger.info("TenantAwareRedisBackend connected for tenant %s", tenant_id)
        except Exception as e:
            logger.error("TenantAwareRedisBackend failed to connect: %s", e)
            raise

    def _zset_key(self, presence_key: str) -> str:
        """Get tenant-scoped zset key."""
        return f"{self._base_prefix}:tenant:{self._tenant_id}:{presence_key}:zset"

    def _meta_key(self, presence_key: str) -> str:
        """Get tenant-scoped metadata key."""
        return f"{self._base_prefix}:tenant:{self._tenant_id}:{presence_key}:meta"

    def join(
        self,
        presence_key: str,
        user_id: str,
        meta: Dict[str, Any],
        connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Join presence group, scoped to tenant."""
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
            extra={"tenant_id": self._tenant_id},
        )
        logger.debug("User %s joined tenant %s presence %s", user_id, self._tenant_id, presence_key)
        return result

    def leave(
        self, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Leave presence group (one connection, or every connection of the user)."""
        record = leave_connection_records(
            self._client,
            self._zset_key(presence_key),
            self._meta_key(presence_key),
            user_id,
            connection_id,
            cutoff=time.time() - self._timeout,
        )
        if record:
            logger.debug(
                "User %s left tenant %s presence %s", user_id, self._tenant_id, presence_key
            )
        return record

    def leave_connection(
        self, presence_key: str, user_id: str, connection_id: str
    ) -> Optional[Dict[str, Any]]:
        return self.leave(presence_key, user_id, connection_id)

    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """List all active presences in the group, one per user.

        Two Redis commands in one round trip, with ``cleanup_stale`` at most
        once per ``cleanup_interval`` per key (#3203), as
        ``RedisPresenceBackend.list``.
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
        """Count active users in the group."""
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
        """Update heartbeat timestamp (one connection, or every connection of the user)."""
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
        """Remove stale presences."""
        removed = cleanup_stale_records(
            self._client,
            self._zset_key(presence_key),
            self._meta_key(presence_key),
            time.time() - self._timeout,
        )
        if removed:
            logger.debug(
                "Cleaned %d stale presences from tenant %s:%s",
                removed,
                self._tenant_id,
                presence_key,
            )
        return removed

    def health_check(self) -> Dict[str, Any]:
        """Check backend health."""
        start = time.time()
        try:
            self._client.ping()
            latency = (time.time() - start) * 1000
            return {
                "status": "healthy",
                "backend": "tenant_redis",
                "tenant_id": self._tenant_id,
                "latency_ms": round(latency, 2),
            }
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {
                "status": "unhealthy",
                "backend": "tenant_redis",
                "tenant_id": self._tenant_id,
                "latency_ms": round(latency, 2),
                "error": str(e),
            }


_F = TypeVar("_F", bound=Callable[..., Any])

# One lock for the class-level stores below (#3074). Sessions' sync code can
# run on several threads (``LIVEVIEW_CONFIG["worker_threads"]``, or an HTTP
# request thread beside the WebSocket one), and join/leave/cleanup are
# read-modify-write sequences over shared dicts.
_TENANT_MEMORY_LOCK = threading.RLock()


def _tenant_memory_locked(fn: _F) -> _F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _TENANT_MEMORY_LOCK:
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


class TenantAwareMemoryBackend(TenantAwareBackendMixin, PresenceBackend):
    """
    Tenant-scoped in-memory backend for presence tracking.

    Useful for development and single-node deployments.
    All data is isolated per tenant via class-level dicts keyed by tenant ID.

    WARNING: Data lives in process memory and is not shared across workers.
    Use ``TenantAwareRedisBackend`` in production multi-tenant environments
    for proper isolation, persistence, and cross-process visibility.
    """

    PRESENCE_TIMEOUT = 60

    # Class-level storage for all tenants. Per tenant and presence key, one
    # record per CONNECTION keyed by ``connection_member`` (the bare user id
    # for a legacy connection), aggregated per user when read (#3254).
    _presences: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    _heartbeats: Dict[str, Dict[str, float]] = {}

    @_tenant_memory_locked
    def __init__(self, tenant_id: str, timeout: int = PRESENCE_TIMEOUT) -> None:
        super().__init__(tenant_id=tenant_id)
        self._tenant_id = tenant_id
        self._timeout = timeout

        # Initialize tenant storage if needed
        if tenant_id not in self._presences:
            self._presences[tenant_id] = {}
            self._heartbeats[tenant_id] = {}

    def _get_tenant_presences(self, presence_key: str) -> Dict[str, Dict[str, Any]]:
        """Get the connection records dict for current tenant and key."""
        tenant_data = self._presences.get(self._tenant_id, {})
        return tenant_data.get(presence_key, {})

    def _set_tenant_presences(self, presence_key: str, data: Dict[str, Dict[str, Any]]) -> None:
        """Set the connection records dict for current tenant and key."""
        if self._tenant_id not in self._presences:
            self._presences[self._tenant_id] = {}
        self._presences[self._tenant_id][presence_key] = data

    @_tenant_memory_locked
    def join(
        self,
        presence_key: str,
        user_id: str,
        meta: Dict[str, Any],
        connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Join presence group."""
        return self._join(presence_key, user_id, connection_id, meta)[0]

    @_tenant_memory_locked
    def join_connection(
        self, presence_key: str, user_id: str, connection_id: str, meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        return self._join(presence_key, user_id, connection_id, meta)

    def _join(
        self, presence_key: str, user_id: str, connection_id: Optional[str], meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        now = time.time()
        member = connection_member(user_id, connection_id)
        self.cleanup_stale(presence_key)
        presences = self._get_tenant_presences(presence_key)
        first = not any(member_belongs_to(m, user_id) for m in presences)
        existing = presences.get(member)
        presences[member] = {
            "id": user_id,
            "tenant_id": self._tenant_id,
            "connection_id": connection_id,
            # A re-join of a live connection is a refresh, not a new arrival.
            "joined_at": existing["joined_at"] if existing else now,
            "updated_at": now,
            "meta": meta,
        }
        self._set_tenant_presences(presence_key, presences)

        # Set heartbeat
        self._heartbeats.setdefault(self._tenant_id, {})[f"{presence_key}:{member}"] = now

        logger.debug(
            "User %s joined tenant %s presence %s (memory)", user_id, self._tenant_id, presence_key
        )
        record = merge_connection_records(
            r for m, r in presences.items() if member_belongs_to(m, user_id)
        )
        return record, first

    @_tenant_memory_locked
    def leave(
        self, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Leave presence group (one connection, or every connection of the user)."""
        presences = self._get_tenant_presences(presence_key)
        if connection_id is None:
            members = [m for m in presences if member_belongs_to(m, user_id)]
        else:
            members = [m for m in (connection_member(user_id, connection_id),) if m in presences]
        if not members:
            return None
        removed = [presences.pop(m) for m in members]
        self._set_tenant_presences(presence_key, presences)

        # Remove heartbeats
        tenant_heartbeats = self._heartbeats.get(self._tenant_id)
        if tenant_heartbeats is not None:
            for m in members:
                tenant_heartbeats.pop(f"{presence_key}:{m}", None)

        if any(member_belongs_to(m, user_id) for m in presences):
            return None
        logger.debug(
            "User %s left tenant %s presence %s (memory)",
            user_id,
            self._tenant_id,
            presence_key,
        )
        return merge_connection_records(removed)

    @_tenant_memory_locked
    def leave_connection(
        self, presence_key: str, user_id: str, connection_id: str
    ) -> Optional[Dict[str, Any]]:
        return self.leave(presence_key, user_id, connection_id)

    @_tenant_memory_locked
    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """List active presences, one per user."""
        self.cleanup_stale(presence_key)
        return aggregate_by_user(self._get_tenant_presences(presence_key).values())

    @_tenant_memory_locked
    def count(self, presence_key: str) -> int:
        """Count active users."""
        return len(self.list(presence_key))

    @_tenant_memory_locked
    def heartbeat(
        self, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> None:
        """Update heartbeat (one connection, or every connection of the user)."""
        if connection_id is None:
            members = [
                m for m in self._get_tenant_presences(presence_key) if member_belongs_to(m, user_id)
            ]
        else:
            members = [connection_member(user_id, connection_id)]
        now = time.time()
        for member in members:
            self._heartbeats.setdefault(self._tenant_id, {})[f"{presence_key}:{member}"] = now

    @_tenant_memory_locked
    def heartbeat_connection(self, presence_key: str, user_id: str, connection_id: str) -> None:
        self.heartbeat(presence_key, user_id, connection_id)

    @_tenant_memory_locked
    def cleanup_stale(self, presence_key: str) -> int:
        """Remove stale presences (connections with no heartbeat within the timeout)."""
        now = time.time()
        cutoff = now - self._timeout

        presences = self._get_tenant_presences(presence_key)
        tenant_heartbeats = self._heartbeats.get(self._tenant_id, {})

        stale = [
            member
            for member in list(presences.keys())
            if tenant_heartbeats.get(f"{presence_key}:{member}", 0) < cutoff
        ]

        for member in stale:
            presences.pop(member, None)
            tenant_heartbeats.pop(f"{presence_key}:{member}", None)

        self._set_tenant_presences(presence_key, presences)

        if stale:
            logger.debug(
                "Cleaned %d stale presences from tenant %s:%s (memory)",
                len(stale),
                self._tenant_id,
                presence_key,
            )
        return len(stale)

    @_tenant_memory_locked
    def health_check(self) -> Dict[str, Any]:
        """Check backend health."""
        return {
            "status": "healthy",
            "backend": "tenant_memory",
            "tenant_id": self._tenant_id,
            "presence_count": sum(
                len(aggregate_by_user(v.values()))
                for v in self._presences.get(self._tenant_id, {}).values()
            ),
        }

    @classmethod
    @_tenant_memory_locked
    def clear_tenant(cls, tenant_id: str) -> None:
        """Clear all data for a tenant (useful for testing)."""
        cls._presences.pop(tenant_id, None)
        cls._heartbeats.pop(tenant_id, None)

    @classmethod
    @_tenant_memory_locked
    def clear_all(cls) -> None:
        """Clear all tenant data (useful for testing)."""
        cls._presences.clear()
        cls._heartbeats.clear()


class TenantPresenceManager:
    """
    Factory for getting tenant-scoped presence backends.

    Usage::

        from djust.tenants import TenantPresenceManager

        # Get presence manager for a specific tenant
        manager = TenantPresenceManager.for_tenant('acme')
        manager.join('document:123', 'user1', {'name': 'Alice'})

        # Or use with current request
        class MyView(TenantMixin, LiveView):
            def mount(self, request, **kwargs):
                manager = TenantPresenceManager.for_tenant(self.tenant.id)
                self.online_users = manager.list('dashboard')
    """

    _instances: Dict[str, PresenceBackend] = {}

    @classmethod
    def for_tenant(cls, tenant_id: str) -> PresenceBackend:
        """
        Get presence backend for a specific tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Tenant-scoped PresenceBackend instance
        """
        instance = cls._instances.get(tenant_id)
        if instance is not None:
            return instance
        with _TENANT_MEMORY_LOCK:
            instance = cls._instances.get(tenant_id)
            if instance is not None:
                return instance
            return cls._create_for_tenant(tenant_id)

    @classmethod
    def _create_for_tenant(cls, tenant_id: str) -> PresenceBackend:
        """Build and cache the tenant's backend; the caller holds the lock."""
        # Get backend configuration
        from ..config import get_djust_config

        config = get_djust_config()

        backend_type = config.get("PRESENCE_BACKEND", "memory")

        backend: PresenceBackend
        if backend_type in ("redis", "tenant_redis"):
            redis_url = config.get(
                "PRESENCE_REDIS_URL", config.get("REDIS_URL", "redis://localhost:6379/0")
            )
            backend = TenantAwareRedisBackend(
                tenant_id=tenant_id,
                redis_url=redis_url,
                cleanup_interval=presence_cleanup_interval(config),
            )
        else:
            backend = TenantAwareMemoryBackend(tenant_id=tenant_id)

        cls._instances[tenant_id] = backend
        return backend

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the backend instance cache."""
        cls._instances.clear()


# Convenience function
def get_tenant_presence_backend(tenant_id: str) -> PresenceBackend:
    """
    Get presence backend for a specific tenant.

    Shorthand for TenantPresenceManager.for_tenant(tenant_id)
    """
    return TenantPresenceManager.for_tenant(tenant_id)
