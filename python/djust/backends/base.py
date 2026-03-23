"""
Abstract base class for presence backends.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PresenceBackend(ABC):
    """
    Abstract base class for presence tracking backends.

    Subclasses must implement all abstract methods to provide
    presence tracking functionality.
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
