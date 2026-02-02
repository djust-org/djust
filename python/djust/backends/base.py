"""
Abstract base class for presence backends.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PresenceBackend(ABC):
    """
    Abstract interface for presence storage.

    Implementations must provide atomic join/leave/list/heartbeat operations.
    """

    @abstractmethod
    def join(self, presence_key: str, user_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Add a user to a presence group. Returns the presence record."""
        ...

    @abstractmethod
    def leave(self, presence_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Remove a user. Returns the removed record or None."""
        ...

    @abstractmethod
    def list(self, presence_key: str) -> List[Dict[str, Any]]:
        """Return all active presences for a group."""
        ...

    @abstractmethod
    def count(self, presence_key: str) -> int:
        """Return the number of active users in a group."""
        ...

    @abstractmethod
    def heartbeat(self, presence_key: str, user_id: str) -> None:
        """Update the heartbeat timestamp for a user."""
        ...

    @abstractmethod
    def cleanup_stale(self, presence_key: str) -> int:
        """Remove stale presences. Returns count removed."""
        ...

    def health_check(self) -> Dict[str, Any]:
        """Check backend health. Override for backend-specific checks."""
        return {"status": "healthy", "backend": self.__class__.__name__}
