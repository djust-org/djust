"""
Global presence backend registry.

Reads DJUST_CONFIG['PRESENCE_BACKEND'] from Django settings:
    'memory' (default) — InMemoryPresenceBackend
    'redis'            — RedisPresenceBackend
"""

import logging
from typing import Optional

from .base import PresenceBackend

logger = logging.getLogger(__name__)

_backend: Optional[PresenceBackend] = None


def get_presence_backend() -> PresenceBackend:
    """
    Get or initialize the configured presence backend.

    Configuration in settings.py::

        DJUST_CONFIG = {
            'PRESENCE_BACKEND': 'redis',
            'PRESENCE_REDIS_URL': 'redis://localhost:6379/2',
        }
    """
    global _backend
    if _backend is not None:
        return _backend

    try:
        from django.conf import settings
        config = getattr(settings, "DJUST_CONFIG", {})
    except Exception:
        config = {}

    backend_type = config.get("PRESENCE_BACKEND", "memory")

    if backend_type == "redis":
        from .redis import RedisPresenceBackend

        redis_url = config.get(
            "PRESENCE_REDIS_URL",
            config.get("REDIS_URL", "redis://localhost:6379/0"),
        )
        key_prefix = config.get("PRESENCE_REDIS_PREFIX", "djust:presence")
        _backend = RedisPresenceBackend(redis_url=redis_url, key_prefix=key_prefix)
    else:
        from .memory import InMemoryPresenceBackend

        _backend = InMemoryPresenceBackend()

    logger.info("Initialized presence backend: %s", backend_type)
    return _backend


def set_presence_backend(backend: PresenceBackend) -> None:
    """Manually set the presence backend (useful for testing)."""
    global _backend
    _backend = backend


def reset_presence_backend() -> None:
    """Reset to force re-initialization on next access."""
    global _backend
    _backend = None
