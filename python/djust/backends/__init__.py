"""
djust.backends — Pluggable backend implementations for presence, channels, etc.

Configured via DJUST_CONFIG['PRESENCE_BACKEND']:
    'memory'  — In-process dict (default, single-node only)
    'redis'   — Redis-backed (multi-node production)
    'tenant_memory' / 'tenant_redis' — the same storage; the tenant scope is
                in the presence key (#2973)
"""

from .base import PresenceBackend

__all__ = [
    "PresenceBackend",
]
