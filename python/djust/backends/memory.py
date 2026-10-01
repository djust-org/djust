"""
In-memory presence backend for development and single-node deployments.
"""

import time
import logging
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    PerConnectionPresenceBackend,
    aggregate_by_user,
    connection_member,
    member_belongs_to,
    merge_connection_records,
    note_first,
)

logger = logging.getLogger(__name__)

# Default timeout: if no heartbeat for this long, consider stale
PRESENCE_TIMEOUT = 60  # seconds


class InMemoryPresenceBackend(PerConnectionPresenceBackend):
    """
    Thread-safe in-memory presence store.

    One record per connection, aggregated per user when read (#3254; see
    :mod:`djust.backends.base`). Data structure::

        _groups = {
            "document:42": {
                "user_1": {"id": "user_1", "connection_id": None, ...},
                "user_2\\x1ftab-a": {"id": "user_2", "connection_id": "tab-a",
                                    "joined_at": 1700000000, "updated_at": ...,
                                    "meta": {...}},
                ...
            }
        }
        _heartbeats = {
            ("document:42", "user_1"): 1700000030.0,
            ("document:42", "user_2\\x1ftab-a"): 1700000031.0,
            ...
        }

    The key of a connection is :func:`~djust.backends.base.connection_member`:
    the bare user id for a legacy connection (no ``connection_id``).

    Limitations:
        - Single-process only — other workers won't see this data.
        - Data lost on restart.
    """

    def __init__(self, timeout: int = PRESENCE_TIMEOUT) -> None:
        self._groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._heartbeats: Dict[tuple, float] = {}
        self._timeout = timeout
        self._lock = RLock()

    def join(
        self,
        presence_key: str,
        user_id: str,
        meta: Dict[str, Any],
        connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._join(presence_key, user_id, connection_id, meta)[0]

    _builtin_join = join

    def _join(
        self, presence_key: str, user_id: str, connection_id: Optional[str], meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        member = connection_member(user_id, connection_id)
        now = time.time()
        with self._lock:
            self._cleanup_locked(presence_key, now)
            group = self._groups.setdefault(presence_key, {})
            first = not any(member_belongs_to(m, user_id) for m in group)
            existing = group.get(member)
            group[member] = {
                "id": user_id,
                "connection_id": connection_id,
                # A re-join of a live connection is a refresh, not a new arrival.
                "joined_at": existing["joined_at"] if existing else now,
                "updated_at": now,
                "meta": meta,
            }
            self._heartbeats[(presence_key, member)] = now
            record = self._user_record(group, user_id)
        logger.debug("User %s joined presence %s", user_id, presence_key)
        return record, note_first(first)

    def leave(
        self, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            group = self._groups.get(presence_key, {})
            if connection_id is None:
                members = [m for m in group if member_belongs_to(m, user_id)]
            else:
                members = [m for m in (connection_member(user_id, connection_id),) if m in group]
            if not members:
                return None
            removed = [group.pop(m) for m in members]
            for m in members:
                self._heartbeats.pop((presence_key, m), None)
            still_present = any(member_belongs_to(m, user_id) for m in group)
            if not group:
                self._groups.pop(presence_key, None)
            record = None if still_present else merge_connection_records(removed)
        if record:
            logger.debug("User %s left presence %s", user_id, presence_key)
        return record

    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        self.cleanup_stale(presence_key)
        with self._lock:
            return aggregate_by_user(self._groups.get(presence_key, {}).values())

    def count(self, presence_key: str) -> int:
        return len(self.list(presence_key))

    def heartbeat(
        self, presence_key: str, user_id: str, connection_id: Optional[str] = None
    ) -> None:
        now = time.time()
        with self._lock:
            if connection_id is None:
                members = [
                    m for m in self._groups.get(presence_key, {}) if member_belongs_to(m, user_id)
                ]
            else:
                members = [connection_member(user_id, connection_id)]
            for member in members:
                if (presence_key, member) in self._heartbeats:
                    self._heartbeats[(presence_key, member)] = now

    def cleanup_stale(self, presence_key: str) -> int:
        with self._lock:
            return self._cleanup_locked(presence_key, time.time())

    def _cleanup_locked(self, presence_key: str, now: float) -> int:
        """Drop the connections of ``presence_key`` with no heartbeat in ``timeout``."""
        group = self._groups.get(presence_key)
        if not group:
            return 0
        stale = [
            member
            for member in group
            if now - self._heartbeats.get((presence_key, member), 0) > self._timeout
        ]
        for member in stale:
            group.pop(member, None)
            self._heartbeats.pop((presence_key, member), None)
        if not group:
            self._groups.pop(presence_key, None)
        return len(stale)

    @staticmethod
    def _user_record(group: Dict[str, Dict[str, Any]], user_id: str) -> Dict[str, Any]:
        return merge_connection_records(
            r for m, r in group.items() if member_belongs_to(m, user_id)
        )

    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            total = sum(len(aggregate_by_user(group.values())) for group in self._groups.values())
            connections = sum(len(g) for g in self._groups.values())
            groups = len(self._groups)
        return {
            "status": "healthy",
            "backend": "memory",
            "total_presences": total,
            "total_connections": connections,
            "total_groups": groups,
        }
