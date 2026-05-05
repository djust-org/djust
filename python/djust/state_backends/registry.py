"""
Global state backend registry and initialization.
"""

from .base import StateBackend, DEFAULT_STATE_SIZE_WARNING_KB, DEFAULT_COMPRESSION_THRESHOLD_KB
from .memory import InMemoryStateBackend
from .redis import RedisStateBackend
from ..utils import BackendRegistry


def _create_state_backend(backend_type: str, config: dict) -> StateBackend:
    """Factory that creates the appropriate state backend from config."""
    ttl = config.get("SESSION_TTL", 3600)
    state_size_warning_kb = config.get("STATE_SIZE_WARNING_KB", DEFAULT_STATE_SIZE_WARNING_KB)

    if backend_type == "redis":
        redis_url = config.get("REDIS_URL", "redis://localhost:6379/0")
        key_prefix = config.get("REDIS_KEY_PREFIX", "djust:")
        compression_enabled = config.get("COMPRESSION_ENABLED", True)
        compression_threshold_kb = config.get(
            "COMPRESSION_THRESHOLD_KB", DEFAULT_COMPRESSION_THRESHOLD_KB
        )
        compression_level = config.get("COMPRESSION_LEVEL", 3)

        return RedisStateBackend(
            redis_url=redis_url,
            default_ttl=ttl,
            key_prefix=key_prefix,
            compression_enabled=compression_enabled,
            compression_threshold_kb=compression_threshold_kb,
            compression_level=compression_level,
        )
    else:
        return InMemoryStateBackend(
            default_ttl=ttl,
            state_size_warning_kb=state_size_warning_kb,
        )


_registry = BackendRegistry(
    config_key="STATE_BACKEND",
    default_type="memory",
    factory=_create_state_backend,
    name="state",
    # Top-level Django setting aliases (#1354). When ``DJUST_CONFIG`` keys
    # are absent, fall back to these top-level settings so projects that
    # configure via ``settings.DJUST_STATE_BACKEND = "redis://..."`` aren't
    # silently downgraded to in-memory. URL-shaped ``DJUST_STATE_BACKEND``
    # values are translated to ``(backend_type="redis", REDIS_URL=<url>)``
    # automatically.
    top_level_aliases={
        "DJUST_STATE_BACKEND": "STATE_BACKEND",
        "DJUST_REDIS_URL": "REDIS_URL",
    },
)


def get_backend() -> StateBackend:
    """
    Get the configured state backend instance.

    Initializes backend on first call based on Django settings.
    Returns cached instance on subsequent calls.

    Configuration in settings.py:
        DJUST_CONFIG = {
            'STATE_BACKEND': 'redis',  # or 'memory'
            'REDIS_URL': 'redis://localhost:6379/0',
            'SESSION_TTL': 3600,  # Time-to-live in seconds; 0 = never expire
            'STATE_SIZE_WARNING_KB': 100,  # Warn when state exceeds this size
            # Compression settings (Redis only)
            'COMPRESSION_ENABLED': True,  # Enable zstd compression
            'COMPRESSION_THRESHOLD_KB': 10,  # Compress states > 10KB
            'COMPRESSION_LEVEL': 3,  # zstd level 1-22 (higher = slower but smaller)
        }

    Top-level alias form (also honoured, #1354):
        DJUST_STATE_BACKEND = 'redis'         # or 'memory', or a redis:// URL
        DJUST_REDIS_URL = 'redis://localhost:6379/0'

    URL-shaped ``DJUST_STATE_BACKEND`` values (``redis://`` / ``rediss://``)
    are auto-translated to ``backend_type="redis"`` plus the URL. When
    ``DEBUG=False`` and the backend defaults to in-memory (no config found),
    a warning is logged: in-memory state doesn't survive multi-process
    deployments.

    Note:
        SESSION_TTL values: positive int = expire after N seconds; 0 or negative =
        never expire (sessions persist until explicitly deleted). The backend's
        cleanup_expired() method is a no-op when TTL ≤ 0.

    Returns:
        StateBackend instance (InMemory or Redis)
    """
    return _registry.get()


def set_backend(backend: StateBackend):
    """
    Manually set the state backend (useful for testing).

    Args:
        backend: StateBackend instance to use
    """
    _registry.set(backend)
