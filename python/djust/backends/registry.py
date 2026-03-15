"""
Global presence backend registry.

Reads DJUST_CONFIG['PRESENCE_BACKEND'] from Django settings:
    'memory' (default) — InMemoryPresenceBackend
    'redis'            — RedisPresenceBackend
"""

from .base import PresenceBackend
from ..utils import BackendRegistry


def _create_presence_backend(backend_type: str, config: dict) -> PresenceBackend:
    """Factory that creates the appropriate presence backend from config."""
    if backend_type == "redis":
        from .redis import RedisPresenceBackend

        redis_url = config.get(
            "PRESENCE_REDIS_URL",
            config.get("REDIS_URL", "redis://localhost:6379/0"),
        )
        key_prefix = config.get("PRESENCE_REDIS_PREFIX", "djust:presence")
        return RedisPresenceBackend(redis_url=redis_url, key_prefix=key_prefix)
    else:
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
    return _registry.get()


def set_presence_backend(backend: PresenceBackend) -> None:
    """Manually set the presence backend (useful for testing)."""
    _registry.set(backend)


def reset_presence_backend() -> None:
    """Reset to force re-initialization on next access."""
    _registry.reset()
