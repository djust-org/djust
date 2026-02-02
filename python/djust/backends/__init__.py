"""
djust.backends — Pluggable backend implementations for presence, channels, etc.

Configured via DJUST_CONFIG['PRESENCE_BACKEND']:
    'memory'  — In-process dict (default, single-node only)
    'redis'   — Redis-backed (multi-node production)
"""

from .base import PresenceBackend
from .memory import InMemoryPresenceBackend
from .registry import get_presence_backend, set_presence_backend

__all__ = [
    "PresenceBackend",
    "InMemoryPresenceBackend",
    "get_presence_backend",
    "set_presence_backend",
]
