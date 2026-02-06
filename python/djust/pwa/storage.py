"""
Offline storage backends for djust PWA support.

Provides persistent storage for offline data using various browser APIs.
"""

import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class OfflineAction:
    """
    Represents an action performed while offline that needs to be synced.

    Attributes:
        id: Unique action identifier
        type: Action type ('create', 'update', 'delete')
        model: Model name
        data: Action data
        timestamp: When the action was performed
        retries: Number of sync retry attempts
        status: Action status ('pending', 'syncing', 'completed', 'failed')
        error: Error message if failed
    """

    type: str  # 'create', 'update', 'delete'
    model: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    retries: int = 0
    status: str = "pending"
    error: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OfflineAction":
        """Create from dictionary."""
        return cls(**data)


class OfflineStorage(ABC):
    """
    Abstract base class for offline storage backends.

    Provides a consistent interface for storing data offline
    using various browser storage APIs.
    """

    def __init__(self, storage_name: str, **kwargs):
        self.storage_name = storage_name
        self._namespace = f"djust:offline:{storage_name}"

    @abstractmethod
    def get(self, key: str, default=None) -> Any:
        """Get value by key."""
        pass

    @abstractmethod
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value with optional TTL."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete value by key."""
        pass

    @abstractmethod
    def clear(self) -> bool:
        """Clear all data in this storage."""
        pass

    @abstractmethod
    def keys(self) -> List[str]:
        """Get all keys in this storage."""
        pass

    @abstractmethod
    def size(self) -> int:
        """Get storage size in bytes."""
        pass

    def _make_key(self, key: str) -> str:
        """Create namespaced key."""
        return f"{self._namespace}:{key}"

    def _serialize(self, value: Any) -> str:
        """Serialize value for storage."""
        return json.dumps(
            {
                "data": value,
                "timestamp": time.time(),
            },
            default=str,
        )

    def _deserialize(self, data: str) -> Any:
        """Deserialize value from storage."""
        try:
            parsed = json.loads(data)
            return parsed.get("data")
        except (json.JSONDecodeError, TypeError) as e:
            logger.debug("Failed to deserialize storage data: %s", e)
            return None


class IndexedDBStorage(OfflineStorage):
    """
    IndexedDB-based storage backend.

    Provides large storage capacity and structured data support.
    Requires JavaScript bridge for browser API access.
    """

    def __init__(self, storage_name: str, version: int = 1, **kwargs):
        super().__init__(storage_name, **kwargs)
        self.version = version
        self.db_name = f"djust_offline_{storage_name}"
        self._js_bridge = None

    def _get_js_bridge(self):
        """
        Get JavaScript bridge for IndexedDB operations.

        Note: This is a server-side in-memory simulation of IndexedDB.
        Actual IndexedDB operations happen client-side in pwa.js.
        This backend is used for server-side state tracking and testing.
        """
        if not self._js_bridge:
            self._js_bridge = {}
        return self._js_bridge

    def get(self, key: str, default=None) -> Any:
        """Get value from IndexedDB."""
        try:
            bridge = self._get_js_bridge()
            namespaced_key = self._make_key(key)

            if namespaced_key in bridge:
                data = bridge[namespaced_key]
                return self._deserialize(data) if isinstance(data, str) else data

            return default
        except Exception as e:
            logger.error("IndexedDB get error: %s", e, exc_info=True)
            return default

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in IndexedDB."""
        try:
            bridge = self._get_js_bridge()
            namespaced_key = self._make_key(key)

            # Store with metadata
            storage_data = {
                "data": value,
                "timestamp": time.time(),
                "ttl": ttl,
                "expires_at": time.time() + ttl if ttl else None,
            }

            bridge[namespaced_key] = json.dumps(storage_data, default=str)
            return True
        except Exception as e:
            logger.error("IndexedDB set error: %s", e, exc_info=True)
            return False

    def delete(self, key: str) -> bool:
        """Delete value from IndexedDB."""
        try:
            bridge = self._get_js_bridge()
            namespaced_key = self._make_key(key)

            if namespaced_key in bridge:
                del bridge[namespaced_key]
            return True
        except Exception as e:
            logger.error("IndexedDB delete error: %s", e, exc_info=True)
            return False

    def clear(self) -> bool:
        """Clear all data from IndexedDB store."""
        try:
            bridge = self._get_js_bridge()

            # Remove all keys with our namespace
            keys_to_remove = [k for k in bridge.keys() if k.startswith(self._namespace)]
            for key in keys_to_remove:
                del bridge[key]

            return True
        except Exception as e:
            logger.error("IndexedDB clear error: %s", e, exc_info=True)
            return False

    def keys(self) -> List[str]:
        """Get all keys in IndexedDB store."""
        try:
            bridge = self._get_js_bridge()

            # Return keys without namespace prefix
            namespace_prefix = f"{self._namespace}:"
            return [
                k[len(namespace_prefix) :] for k in bridge.keys() if k.startswith(namespace_prefix)
            ]
        except Exception as e:
            logger.error("IndexedDB keys error: %s", e, exc_info=True)
            return []

    def size(self) -> int:
        """Get estimated storage size in bytes."""
        try:
            bridge = self._get_js_bridge()

            total_size = 0
            namespace_prefix = f"{self._namespace}:"

            for key, value in bridge.items():
                if key.startswith(namespace_prefix):
                    total_size += len(key.encode("utf-8"))
                    if isinstance(value, str):
                        total_size += len(value.encode("utf-8"))
                    else:
                        total_size += len(str(value).encode("utf-8"))

            return total_size
        except Exception as e:
            logger.error("IndexedDB size error: %s", e, exc_info=True)
            return 0

    def cleanup_expired(self) -> int:
        """Remove expired entries."""
        try:
            bridge = self._get_js_bridge()
            current_time = time.time()
            removed_count = 0

            keys_to_remove = []
            namespace_prefix = f"{self._namespace}:"

            for key, value in bridge.items():
                if not key.startswith(namespace_prefix):
                    continue

                try:
                    data = json.loads(value) if isinstance(value, str) else value
                    expires_at = data.get("expires_at")

                    if expires_at and current_time > expires_at:
                        keys_to_remove.append(key)
                except (json.JSONDecodeError, TypeError):
                    # Invalid data, remove it
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del bridge[key]
                removed_count += 1

            if removed_count > 0:
                logger.info("Cleaned up %d expired IndexedDB entries", removed_count)

            return removed_count
        except Exception as e:
            logger.error("IndexedDB cleanup error: %s", e, exc_info=True)
            return 0


class LocalStorage(OfflineStorage):
    """
    LocalStorage-based storage backend.

    Provides simple key-value storage with browser persistence.
    Limited to ~5-10MB depending on browser.
    """

    def __init__(self, storage_name: str, **kwargs):
        super().__init__(storage_name, **kwargs)
        self._storage = {}  # In-memory fallback for server-side usage

    def get(self, key: str, default=None) -> Any:
        """Get value from localStorage."""
        try:
            namespaced_key = self._make_key(key)
            data = self._storage.get(namespaced_key)

            if data:
                return self._deserialize(data)

            return default
        except Exception as e:
            logger.error("localStorage get error: %s", e, exc_info=True)
            return default

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in localStorage."""
        try:
            namespaced_key = self._make_key(key)

            # Store with metadata
            storage_data = {
                "data": value,
                "timestamp": time.time(),
                "ttl": ttl,
                "expires_at": time.time() + ttl if ttl else None,
            }

            serialized = json.dumps(storage_data, default=str)

            # Check storage quota (rough estimate)
            if self.size() + len(serialized) > 5 * 1024 * 1024:  # 5MB
                logger.warning("localStorage quota exceeded")
                return False

            self._storage[namespaced_key] = serialized
            return True
        except Exception as e:
            logger.error("localStorage set error: %s", e, exc_info=True)
            return False

    def delete(self, key: str) -> bool:
        """Delete value from localStorage."""
        try:
            namespaced_key = self._make_key(key)

            if namespaced_key in self._storage:
                del self._storage[namespaced_key]
            return True
        except Exception as e:
            logger.error("localStorage delete error: %s", e, exc_info=True)
            return False

    def clear(self) -> bool:
        """Clear all localStorage data for this namespace."""
        try:
            keys_to_remove = [k for k in self._storage.keys() if k.startswith(self._namespace)]
            for key in keys_to_remove:
                del self._storage[key]
            return True
        except Exception as e:
            logger.error("localStorage clear error: %s", e, exc_info=True)
            return False

    def keys(self) -> List[str]:
        """Get all keys in localStorage for this namespace."""
        try:
            namespace_prefix = f"{self._namespace}:"
            return [
                k[len(namespace_prefix) :]
                for k in self._storage.keys()
                if k.startswith(namespace_prefix)
            ]
        except Exception as e:
            logger.error("localStorage keys error: %s", e, exc_info=True)
            return []

    def size(self) -> int:
        """Get storage size in bytes."""
        try:
            total_size = 0
            namespace_prefix = f"{self._namespace}:"

            for key, value in self._storage.items():
                if key.startswith(namespace_prefix):
                    total_size += len(key.encode("utf-8"))
                    total_size += len(value.encode("utf-8"))

            return total_size
        except Exception as e:
            logger.error("localStorage size error: %s", e, exc_info=True)
            return 0


class SyncQueue:
    """
    Queue for managing offline actions that need to be synchronized.

    Provides FIFO ordering, retry logic, and status tracking.
    """

    def __init__(self, storage_name: str, storage_backend: Optional[OfflineStorage] = None):
        self.storage_name = storage_name
        self._storage = storage_backend or IndexedDBStorage(f"{storage_name}_sync")
        self._queue_key = "sync_queue"

    def add(self, action: OfflineAction) -> bool:
        """Add action to sync queue."""
        try:
            queue = self._get_queue()

            # Generate unique ID if not provided
            if not action.id:
                action.id = f"{action.type}_{action.model}_{int(time.time() * 1000)}"

            # Add to queue
            queue.append(action.to_dict())

            # Save queue
            return self._storage.set(self._queue_key, queue)
        except Exception as e:
            logger.error("Failed to add action to sync queue: %s", e, exc_info=True)
            return False

    def get_pending(self) -> List[OfflineAction]:
        """Get all pending sync actions."""
        try:
            queue = self._get_queue()
            return [
                OfflineAction.from_dict(item) for item in queue if item.get("status") == "pending"
            ]
        except Exception as e:
            logger.error("Failed to get pending actions: %s", e, exc_info=True)
            return []

    def mark_completed(self, action_id: str) -> bool:
        """Mark action as completed."""
        return self._update_action_status(action_id, "completed")

    def mark_failed(self, action_id: str, error: str) -> bool:
        """Mark action as failed with error message."""
        return self._update_action_status(action_id, "failed", error=error)

    def mark_syncing(self, action_id: str) -> bool:
        """Mark action as currently syncing."""
        return self._update_action_status(action_id, "syncing")

    def retry_action(self, action_id: str) -> bool:
        """Retry a failed action."""
        try:
            queue = self._get_queue()

            for item in queue:
                if item.get("id") == action_id:
                    item["status"] = "pending"
                    item["retries"] = item.get("retries", 0) + 1
                    item["error"] = None
                    break

            return self._storage.set(self._queue_key, queue)
        except Exception as e:
            logger.error("Failed to retry action %s: %s", action_id, e, exc_info=True)
            return False

    def remove_completed(self, older_than_hours: int = 24) -> int:
        """Remove completed actions older than specified hours."""
        try:
            queue = self._get_queue()
            cutoff_time = time.time() - (older_than_hours * 3600)

            original_count = len(queue)
            queue = [
                item
                for item in queue
                if not (
                    item.get("status") == "completed" and item.get("timestamp", 0) < cutoff_time
                )
            ]

            removed_count = original_count - len(queue)

            if removed_count > 0:
                self._storage.set(self._queue_key, queue)
                logger.info("Removed %d completed sync actions", removed_count)

            return removed_count
        except Exception as e:
            logger.error("Failed to remove completed actions: %s", e, exc_info=True)
            return 0

    def clear(self) -> bool:
        """Clear all actions from queue."""
        return self._storage.delete(self._queue_key)

    def size(self) -> int:
        """Get number of actions in queue."""
        return len(self._get_queue())

    def _get_queue(self) -> List[Dict[str, Any]]:
        """Get queue data from storage."""
        queue_data = self._storage.get(self._queue_key, [])
        return queue_data if isinstance(queue_data, list) else []

    def _update_action_status(
        self, action_id: str, status: str, error: Optional[str] = None
    ) -> bool:
        """Update action status in queue."""
        try:
            queue = self._get_queue()

            for item in queue:
                if item.get("id") == action_id:
                    item["status"] = status
                    if error:
                        item["error"] = error
                    break

            return self._storage.set(self._queue_key, queue)
        except Exception as e:
            logger.error("Failed to update action status: %s", e, exc_info=True)
            return False


def get_storage_backend(storage_name: str, backend_type: Optional[str] = None) -> OfflineStorage:
    """
    Get storage backend instance.

    Args:
        storage_name: Name for the storage namespace
        backend_type: Backend type ('indexeddb', 'localstorage', 'sessionstorage')

    Returns:
        OfflineStorage instance
    """
    if not backend_type:
        # Get from config
        try:
            from django.conf import settings

            config = getattr(settings, "DJUST_CONFIG", {})
            backend_type = config.get("PWA_OFFLINE_STORAGE", "indexeddb")
        except Exception:
            logger.debug("Could not load DJUST_CONFIG for PWA_OFFLINE_STORAGE, using default")
            backend_type = "indexeddb"

    backend_type = backend_type.lower()

    if backend_type == "indexeddb":
        return IndexedDBStorage(storage_name)
    elif backend_type in ("localstorage", "localStorage"):
        return LocalStorage(storage_name)
    else:
        # Default to IndexedDB
        logger.warning("Unknown storage backend: %s, using IndexedDB", backend_type)
        return IndexedDBStorage(storage_name)
