"""
djust.backends — Pluggable backend implementations for presence, channels, etc.

Configured via DJUST_CONFIG['PRESENCE_BACKEND']:
    'memory'  — In-process dict (default, single-node only)
    'redis'   — Redis-backed (multi-node production)
"""

from .base import PresenceBackend

__all__ = [
    "PresenceBackend",
]
