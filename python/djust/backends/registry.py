"""
Global presence backend registry.

Reads DJUST_CONFIG['PRESENCE_BACKEND'] from Django settings:
    'memory' (default) — InMemoryPresenceBackend
    'redis'            — RedisPresenceBackend
    'tenant_memory'    — InMemoryPresenceBackend (the value djust.tenants documents)
    'tenant_redis'     — RedisPresenceBackend (the value djust.tenants documents)

The two ``tenant_*`` values select the same storage as their plain forms: the
tenant scope is in the presence KEY, which ``PresenceMixin`` /
``TenantMixin`` prefix with ``tenant:<id>:`` whichever order they are listed
in (#2973). Before #2973 ``tenant_redis`` silently became the per-process
memory backend. Any other value still falls back to memory, with a warning
here and the ``djust.C019`` system check at startup.
"""

import logging

from typing import cast

from .base import PresenceBackend
from ..utils import BackendRegistry

logger = logging.getLogger(__name__)

#: Every ``PRESENCE_BACKEND`` value the registry understands.
KNOWN_PRESENCE_BACKENDS = ("memory", "redis", "tenant_memory", "tenant_redis")


def _create_presence_backend(backend_type: str, config: dict) -> PresenceBackend:
    """Factory that creates the appropriate presence backend from config."""
    if backend_type in ("redis", "tenant_redis"):
        from .redis import RedisPresenceBackend

        redis_url = config.get(
            "PRESENCE_REDIS_URL",
            config.get("REDIS_URL", "redis://localhost:6379/0"),
        )
        key_prefix = config.get("PRESENCE_REDIS_PREFIX", "djust:presence")
        return RedisPresenceBackend(redis_url=redis_url, key_prefix=key_prefix)
    else:
        if backend_type not in KNOWN_PRESENCE_BACKENDS:
            logger.warning(
                "Unknown PRESENCE_BACKEND %r; using the in-memory backend (known values: %s).",
                backend_type,
                ", ".join(KNOWN_PRESENCE_BACKENDS),
            )
        from .memory import InMemoryPresenceBackend

        return InMemoryPresenceBackend()


_registry = BackendRegistry(
    config_key="PRESENCE_BACKEND",
    default_type="memory",
    factory=_create_presence_backend,
    name="presence",
)


def get_presence_backend() -> PresenceBackend:
    """
    Get or initialize the configured presence backend.

    Configuration in settings.py::

        DJUST_CONFIG = {
            'PRESENCE_BACKEND': 'redis',
            'PRESENCE_REDIS_URL': 'redis://localhost:6379/2',
        }
    """
    return cast(PresenceBackend, _registry.get())


def set_presence_backend(backend: PresenceBackend) -> None:
    """Manually set the presence backend (useful for testing)."""
    _registry.set(backend)


def reset_presence_backend() -> None:
    """Reset to force re-initialization on next access."""
    _registry.reset()
