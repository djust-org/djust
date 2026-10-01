"""
Abstract base class for presence backends.

Presence is stored **per connection** and read **per user** (#3254). One
browser tab (strictly: one mounted presence view) is one *connection* of its
user in a room. Every backend keeps one record per ``(room, user,
connection)`` and aggregates them when it lists a room:

* a user is present from the moment their first connection joins until their
  last connection leaves or expires;
* ``list`` / ``count`` return one record per user, however many connections
  that user has;
* heartbeats and the timeout apply to each connection, so a tab that died is
  dropped on its own without removing the user's other tabs.

The aggregated record keeps the shape every backend has always returned,
``{"id": <user_id>, "joined_at": <epoch>, "meta": {...}}``: ``joined_at`` is
the earliest of the user's connections and ``meta`` is the one the user's
most recently joined connection supplied.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Separates a user id from a connection id in a backend's per-connection key.
#: A connection with no id (the legacy ``join(key, user, meta)`` call) is keyed
#: by the bare user id, so a record written before #3254 reads as that user's
#: one legacy connection.
CONNECTION_SEPARATOR = "\x1f"

#: The per-connection fields a backend stores beside the public record. They
#: are dropped from the aggregated record a caller sees.
_CONNECTION_ONLY_FIELDS = ("connection_id", "updated_at")


def connection_member(user_id: str, connection_id: Optional[str] = None) -> str:
    """The backend key of one connection of ``user_id``."""
    if connection_id is None:
        return user_id
    return f"{user_id}{CONNECTION_SEPARATOR}{connection_id}"


def member_belongs_to(member: str, user_id: str) -> bool:
    """Whether ``member`` (a :func:`connection_member`) is a connection of ``user_id``."""
    return member == user_id or member.startswith(user_id + CONNECTION_SEPARATOR)


def merge_connection_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """One user's aggregated record from their connections' records.

    ``joined_at`` is the earliest, and every other field (``meta``, and the
    tenant backends' ``tenant_id``) comes from the connection written last, so
    the most recent ``track_presence`` meta is what peers see.
    """
    records = list(records)
    newest = records[0]
    for record in records[1:]:
        if record.get("updated_at", record["joined_at"]) >= newest.get(
            "updated_at", newest["joined_at"]
        ):
            newest = record
    merged = {k: v for k, v in newest.items() if k not in _CONNECTION_ONLY_FIELDS}
    merged["joined_at"] = min(r["joined_at"] for r in records)
    return merged


def aggregate_by_user(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse per-connection records into one record per user.

    Users keep the order in which their first record appears.
    """
    by_user: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        by_user.setdefault(record["id"], []).append(record)
    return [merge_connection_records(group) for group in by_user.values()]


class PresenceBackend(ABC):
    """
    Abstract base class for presence tracking backends.

    Subclasses must implement all abstract methods to provide
    presence tracking functionality.

    **Per-connection contract (#3254).** The built-in backends store one record
    per ``(presence_key, user_id, connection_id)`` and report per user; see the
    module docstring. They accept an optional ``connection_id`` on ``join``,
    ``leave`` and ``heartbeat``:

    * ``join(key, user, meta, connection_id=None)`` adds (or refreshes) that
      connection and returns the user's aggregated record. No ``connection_id``
      is the user's one *legacy* connection, which is what a caller written
      before #3254 gets.
    * ``leave(key, user, connection_id=None)`` removes that connection. No
      ``connection_id`` removes every connection of the user. Either way it
      returns the user's aggregated record when the user is gone from the
      group, and ``None`` when they are still present through another
      connection (or were never there).
    * ``heartbeat(key, user, connection_id=None)`` refreshes that connection,
      or every connection of the user when no id is given.

    ``PresenceMixin`` drives a backend through :meth:`join_connection`,
    :meth:`leave_connection` and :meth:`heartbeat_connection`, which report
    whether the user *just arrived* or *just left* so the join and leave hooks
    fire once per user. A third-party backend written against the old
    three-method contract keeps working without changes: the defaults below
    call its ``join`` / ``leave`` / ``heartbeat`` with no connection id, so it
    stays one record per user and a closed tab removes the user, exactly as
    before. Override the three ``*_connection`` methods (and store per
    connection) to get the new behaviour.
    """

    @abstractmethod
    def join(self, presence_key: str, user_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Join a presence group."""
        raise NotImplementedError

    @abstractmethod
    def leave(self, presence_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Leave a presence group."""
        raise NotImplementedError

    @abstractmethod
    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """List all presences in a group."""
        raise NotImplementedError

    @abstractmethod
    def count(self, presence_key: str) -> int:
        """Count presences in a group."""
        raise NotImplementedError

    @abstractmethod
    def heartbeat(self, presence_key: str, user_id: str) -> None:
        """Update heartbeat for a user."""
        raise NotImplementedError

    @abstractmethod
    def cleanup_stale(self, presence_key: str) -> int:
        """Remove stale presences."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Check backend health."""
        raise NotImplementedError

    # -- per-connection entry points (#3254) --------------------------------

    def join_connection(
        self, presence_key: str, user_id: str, connection_id: str, meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        """Join one connection of ``user_id``; returns ``(record, first)``.

        ``first`` is true when this made the user present (no live connection
        of theirs was in the group), which is when ``handle_presence_join``
        fires. The default is for a backend that stores one record per user: it
        calls ``join`` and reports every join as a first.
        """
        return self.join(presence_key, user_id, meta), True

    def leave_connection(
        self, presence_key: str, user_id: str, connection_id: str
    ) -> Optional[Dict[str, Any]]:
        """Remove one connection of ``user_id``.

        Returns the user's record when that was their last connection, which
        is when ``handle_presence_leave`` fires, and ``None`` otherwise. The
        default is for a backend that stores one record per user: it calls
        ``leave``, which removes the user.
        """
        return self.leave(presence_key, user_id)

    def heartbeat_connection(self, presence_key: str, user_id: str, connection_id: str) -> None:
        """Refresh one connection of ``user_id``. The default calls ``heartbeat``."""
        self.heartbeat(presence_key, user_id)
