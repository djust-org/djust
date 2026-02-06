"""
In-memory presence backend for development and single-node deployments.
"""

import time
import logging
from threading import RLock
from typing import Any, Dict, List, Optional

from .base import PresenceBackend

logger = logging.getLogger(__name__)

# Default timeout: if no heartbeat for this long, consider stale
PRESENCE_TIMEOUT = 60  # seconds


class InMemoryPresenceBackend(PresenceBackend):
    """
    Thread-safe in-memory presence store.

    Data structure::

        _groups = {
            "document:42": {
                "user_1": {"id": "user_1", "joined_at": 1700000000, "meta": {...}},
                ...
            }
        }
        _heartbeats = {
            ("document:42", "user_1"): 1700000030.0,
            ...
        }

    Limitations:
        - Single-process only — other workers won't see this data.
        - Data lost on restart.
    """

    def __init__(self, timeout: int = PRESENCE_TIMEOUT) -> None:
        self._groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._heartbeats: Dict[tuple, float] = {}
        self._timeout = timeout
        self._lock = RLock()

    def join(self, presence_key: str, user_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "id": user_id,
            "joined_at": time.time(),
            "meta": meta,
        }
        with self._lock:
            self._groups.setdefault(presence_key, {})[user_id] = record
            self._heartbeats[(presence_key, user_id)] = time.time()
        logger.debug("User %s joined presence %s", user_id, presence_key)
        return record

    def leave(self, presence_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            group = self._groups.get(presence_key, {})
            record = group.pop(user_id, None)
            self._heartbeats.pop((presence_key, user_id), None)
            if not group:
                self._groups.pop(presence_key, None)
        if record:
            logger.debug("User %s left presence %s", user_id, presence_key)
        return record

    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        self.cleanup_stale(presence_key)
        with self._lock:
            group = self._groups.get(presence_key, {})
            return list(group.values())

    def count(self, presence_key: str) -> int:
        return len(self.list(presence_key))

    def heartbeat(self, presence_key: str, user_id: str) -> None:
        with self._lock:
            if (presence_key, user_id) in self._heartbeats:
                self._heartbeats[(presence_key, user_id)] = time.time()

    def cleanup_stale(self, presence_key: str) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            group = self._groups.get(presence_key, {})
            stale = [
                uid
                for uid in group
                if now - self._heartbeats.get((presence_key, uid), 0) > self._timeout
            ]
            for uid in stale:
                group.pop(uid, None)
                self._heartbeats.pop((presence_key, uid), None)
                removed += 1
            if not group:
                self._groups.pop(presence_key, None)
        return removed

    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            total = sum(len(g) for g in self._groups.values())
        return {
            "status": "healthy",
            "backend": "memory",
            "total_presences": total,
            "total_groups": len(self._groups),
        }
