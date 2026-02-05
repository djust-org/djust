"""
Tenant-aware backends for state and presence storage.

Provides tenant isolation for:
- State storage (session state, LiveView assigns)
- Presence tracking
- Cache operations

These backends prefix all keys with tenant ID to prevent cross-tenant
data leakage.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from ..backends.base import PresenceBackend

logger = logging.getLogger(__name__)


class TenantAwareBackendMixin:
    """
    Mixin that adds tenant-scoping to backend keys.

    All keys are prefixed with tenant ID to ensure isolation.
    """

    def __init__(self, tenant_id: str, *args, **kwargs):
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
    ):
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError(
                "redis is required for TenantAwareRedisBackend. " "Install with: pip install redis"
            )

        self._tenant_id = tenant_id
        self._client = redis_lib.from_url(redis_url, decode_responses=True)
        self._base_prefix = key_prefix
        self._timeout = timeout

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

    def join(self, presence_key: str, user_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Join presence group, scoped to tenant."""
        now = time.time()
        record = {
            "id": user_id,
            "tenant_id": self._tenant_id,
            "joined_at": now,
            "meta": meta,
        }

        pipe = self._client.pipeline()
        pipe.zadd(self._zset_key(presence_key), {user_id: now})
        pipe.hset(self._meta_key(presence_key), user_id, json.dumps(record))
        ttl = self._timeout * 3
        pipe.expire(self._zset_key(presence_key), ttl)
        pipe.expire(self._meta_key(presence_key), ttl)
        pipe.execute()

        logger.debug("User %s joined tenant %s presence %s", user_id, self._tenant_id, presence_key)
        return record

    def leave(self, presence_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Leave presence group."""
        raw = self._client.hget(self._meta_key(presence_key), user_id)
        record = json.loads(raw) if raw else None

        pipe = self._client.pipeline()
        pipe.zrem(self._zset_key(presence_key), user_id)
        pipe.hdel(self._meta_key(presence_key), user_id)
        pipe.execute()

        if record:
            logger.debug(
                "User %s left tenant %s presence %s", user_id, self._tenant_id, presence_key
            )
        return record

    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """List all active presences in the group."""
        self.cleanup_stale(presence_key)

        members = self._client.zrangebyscore(
            self._zset_key(presence_key),
            min=time.time() - self._timeout,
            max="+inf",
        )

        if not members:
            return []

        pipe = self._client.pipeline()
        for uid in members:
            pipe.hget(self._meta_key(presence_key), uid)
        results = pipe.execute()

        presences = []
        for raw in results:
            if raw:
                try:
                    presences.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    logger.debug("Skipping malformed presence record in %s", presence_key)
        return presences

    def count(self, presence_key: str) -> int:
        """Count active users in the group."""
        cutoff = time.time() - self._timeout
        return self._client.zcount(self._zset_key(presence_key), cutoff, "+inf")

    def heartbeat(self, presence_key: str, user_id: str) -> None:
        """Update heartbeat timestamp."""
        now = time.time()
        pipe = self._client.pipeline()
        pipe.zadd(self._zset_key(presence_key), {user_id: now})
        ttl = self._timeout * 3
        pipe.expire(self._zset_key(presence_key), ttl)
        pipe.expire(self._meta_key(presence_key), ttl)
        pipe.execute()

    def cleanup_stale(self, presence_key: str) -> int:
        """Remove stale presences."""
        cutoff = time.time() - self._timeout
        stale = self._client.zrangebyscore(self._zset_key(presence_key), "-inf", cutoff)

        if not stale:
            return 0

        pipe = self._client.pipeline()
        pipe.zremrangebyscore(self._zset_key(presence_key), "-inf", cutoff)
        for uid in stale:
            pipe.hdel(self._meta_key(presence_key), uid)
        pipe.execute()

        logger.debug(
            "Cleaned %d stale presences from tenant %s:%s",
            len(stale),
            self._tenant_id,
            presence_key,
        )
        return len(stale)

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


class TenantAwareMemoryBackend(TenantAwareBackendMixin, PresenceBackend):
    """
    Tenant-scoped in-memory backend for presence tracking.

    Useful for development and single-node deployments.
    All data is isolated per tenant.
    """

    PRESENCE_TIMEOUT = 60

    # Class-level storage for all tenants
    _presences: Dict[str, Dict[str, Dict[str, Any]]] = {}
    _heartbeats: Dict[str, Dict[str, float]] = {}

    def __init__(self, tenant_id: str, timeout: int = PRESENCE_TIMEOUT):
        self._tenant_id = tenant_id
        self._timeout = timeout

        # Initialize tenant storage if needed
        if tenant_id not in self._presences:
            self._presences[tenant_id] = {}
            self._heartbeats[tenant_id] = {}

    def _get_tenant_presences(self, presence_key: str) -> Dict[str, Dict[str, Any]]:
        """Get presences dict for current tenant and key."""
        tenant_data = self._presences.get(self._tenant_id, {})
        return tenant_data.get(presence_key, {})

    def _set_tenant_presences(self, presence_key: str, data: Dict[str, Dict[str, Any]]):
        """Set presences dict for current tenant and key."""
        if self._tenant_id not in self._presences:
            self._presences[self._tenant_id] = {}
        self._presences[self._tenant_id][presence_key] = data

    def join(self, presence_key: str, user_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Join presence group."""
        now = time.time()
        record = {
            "id": user_id,
            "tenant_id": self._tenant_id,
            "joined_at": now,
            "meta": meta,
        }

        presences = self._get_tenant_presences(presence_key)
        presences[user_id] = record
        self._set_tenant_presences(presence_key, presences)

        # Set heartbeat
        self._heartbeats.setdefault(self._tenant_id, {})[f"{presence_key}:{user_id}"] = now

        logger.debug(
            "User %s joined tenant %s presence %s (memory)", user_id, self._tenant_id, presence_key
        )
        return record

    def leave(self, presence_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Leave presence group."""
        presences = self._get_tenant_presences(presence_key)
        record = presences.pop(user_id, None)
        self._set_tenant_presences(presence_key, presences)

        # Remove heartbeat
        heartbeat_key = f"{presence_key}:{user_id}"
        if self._tenant_id in self._heartbeats:
            self._heartbeats[self._tenant_id].pop(heartbeat_key, None)

        if record:
            logger.debug(
                "User %s left tenant %s presence %s (memory)",
                user_id,
                self._tenant_id,
                presence_key,
            )
        return record

    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """List active presences."""
        self.cleanup_stale(presence_key)
        presences = self._get_tenant_presences(presence_key)
        return list(presences.values())

    def count(self, presence_key: str) -> int:
        """Count active users."""
        self.cleanup_stale(presence_key)
        presences = self._get_tenant_presences(presence_key)
        return len(presences)

    def heartbeat(self, presence_key: str, user_id: str) -> None:
        """Update heartbeat."""
        heartbeat_key = f"{presence_key}:{user_id}"
        self._heartbeats.setdefault(self._tenant_id, {})[heartbeat_key] = time.time()

    def cleanup_stale(self, presence_key: str) -> int:
        """Remove stale presences."""
        now = time.time()
        cutoff = now - self._timeout

        presences = self._get_tenant_presences(presence_key)
        tenant_heartbeats = self._heartbeats.get(self._tenant_id, {})

        stale_users = []
        for user_id in list(presences.keys()):
            heartbeat_key = f"{presence_key}:{user_id}"
            last_heartbeat = tenant_heartbeats.get(heartbeat_key, 0)
            if last_heartbeat < cutoff:
                stale_users.append(user_id)

        for user_id in stale_users:
            presences.pop(user_id, None)
            tenant_heartbeats.pop(f"{presence_key}:{user_id}", None)

        self._set_tenant_presences(presence_key, presences)

        if stale_users:
            logger.debug(
                "Cleaned %d stale presences from tenant %s:%s (memory)",
                len(stale_users),
                self._tenant_id,
                presence_key,
            )
        return len(stale_users)

    def health_check(self) -> Dict[str, Any]:
        """Check backend health."""
        return {
            "status": "healthy",
            "backend": "tenant_memory",
            "tenant_id": self._tenant_id,
            "presence_count": sum(
                len(v) for v in self._presences.get(self._tenant_id, {}).values()
            ),
        }

    @classmethod
    def clear_tenant(cls, tenant_id: str) -> None:
        """Clear all data for a tenant (useful for testing)."""
        cls._presences.pop(tenant_id, None)
        cls._heartbeats.pop(tenant_id, None)

    @classmethod
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
        if tenant_id in cls._instances:
            return cls._instances[tenant_id]

        # Get backend configuration
        try:
            from django.conf import settings

            config = getattr(settings, "DJUST_CONFIG", {})
        except Exception:
            logger.debug("Could not load DJUST_CONFIG, using defaults")
            config = {}

        backend_type = config.get("PRESENCE_BACKEND", "memory")

        if backend_type in ("redis", "tenant_redis"):
            redis_url = config.get(
                "PRESENCE_REDIS_URL", config.get("REDIS_URL", "redis://localhost:6379/0")
            )
            backend = TenantAwareRedisBackend(
                tenant_id=tenant_id,
                redis_url=redis_url,
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
