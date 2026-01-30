"""
Global state backend registry and initialization.
"""

import logging
from typing import Optional

from .base import StateBackend, DEFAULT_STATE_SIZE_WARNING_KB, DEFAULT_COMPRESSION_THRESHOLD_KB
from .memory import InMemoryStateBackend
from .redis import RedisStateBackend

logger = logging.getLogger(__name__)

# Global backend instance (initialized by get_backend())
_backend: Optional[StateBackend] = None


def get_backend() -> StateBackend:
    """
    Get the configured state backend instance.

    Initializes backend on first call based on Django settings.
    Returns cached instance on subsequent calls.

    Configuration in settings.py:
        DJUST_CONFIG = {
            'STATE_BACKEND': 'redis',  # or 'memory'
            'REDIS_URL': 'redis://localhost:6379/0',
            'SESSION_TTL': 3600,
            'STATE_SIZE_WARNING_KB': 100,  # Warn when state exceeds this size
            # Compression settings (Redis only)
            'COMPRESSION_ENABLED': True,  # Enable zstd compression
            'COMPRESSION_THRESHOLD_KB': 10,  # Compress states > 10KB
            'COMPRESSION_LEVEL': 3,  # zstd level 1-22 (higher = slower but smaller)
        }

    Returns:
        StateBackend instance (InMemory or Redis)
    """
    global _backend

    if _backend is not None:
        return _backend

    # Load configuration from Django settings
    try:
        from django.conf import settings

        config = getattr(settings, "DJUST_CONFIG", {})
    except Exception:
        config = {}

    backend_type = config.get("STATE_BACKEND", "memory")
    ttl = config.get("SESSION_TTL", 3600)
    state_size_warning_kb = config.get("STATE_SIZE_WARNING_KB", DEFAULT_STATE_SIZE_WARNING_KB)

    if backend_type == "redis":
        redis_url = config.get("REDIS_URL", "redis://localhost:6379/0")
        key_prefix = config.get("REDIS_KEY_PREFIX", "djust:")
        # Compression settings
        compression_enabled = config.get("COMPRESSION_ENABLED", True)
        compression_threshold_kb = config.get(
            "COMPRESSION_THRESHOLD_KB", DEFAULT_COMPRESSION_THRESHOLD_KB
        )
        compression_level = config.get("COMPRESSION_LEVEL", 3)

        _backend = RedisStateBackend(
            redis_url=redis_url,
            default_ttl=ttl,
            key_prefix=key_prefix,
            compression_enabled=compression_enabled,
            compression_threshold_kb=compression_threshold_kb,
            compression_level=compression_level,
        )
    else:
        _backend = InMemoryStateBackend(
            default_ttl=ttl,
            state_size_warning_kb=state_size_warning_kb,
        )

    logger.info(f"Initialized state backend: {backend_type}")
    return _backend


def set_backend(backend: StateBackend):
    """
    Manually set the state backend (useful for testing).

    Args:
        backend: StateBackend instance to use
    """
    global _backend
    _backend = backend
