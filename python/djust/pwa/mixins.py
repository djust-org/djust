"""
PWA mixins for djust LiveViews.

Provides offline capabilities, service worker integration, and
automatic state synchronization.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Union

from .storage import get_storage_backend, OfflineAction, SyncQueue
from .sync import SyncManager
from .utils import is_online, get_connection_info

logger = logging.getLogger(__name__)


class PWAMixin:
    """
    Mixin that adds Progressive Web App features to LiveView.

    Provides:
    - Manifest generation
    - Service worker registration
    - Install prompts
    - App update notifications

    Usage::

        class AppView(PWAMixin, LiveView):
            template_name = 'app.html'
            pwa_name = 'My App'
            pwa_theme_color = '#1976d2'

            def mount(self, request, **kwargs):
                self.register_pwa_handlers()
    """

    # PWA configuration (can be overridden per view)
    pwa_name: Optional[str] = None
    pwa_short_name: Optional[str] = None
    pwa_description: Optional[str] = None
    pwa_theme_color: str = "#000000"
    pwa_background_color: str = "#ffffff"
    pwa_display: str = "standalone"
    pwa_orientation: str = "any"
    pwa_start_url: str = "/"
    pwa_scope: str = "/"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pwa_registered = False
        self._install_prompt_deferred = None

    def get_pwa_config(self) -> Dict[str, Any]:
        """
        Get PWA configuration for this view.

        Merges view-level settings with global DJUST_CONFIG.
        """
        config = {}

        # Get global config
        try:
            from django.conf import settings

            djust_config = getattr(settings, "DJUST_CONFIG", {})
            config.update(
                {
                    "name": djust_config.get("PWA_NAME", "djust App"),
                    "short_name": djust_config.get("PWA_SHORT_NAME", "djust"),
                    "description": djust_config.get("PWA_DESCRIPTION", "PWA built with djust"),
                    "theme_color": djust_config.get("PWA_THEME_COLOR", "#000000"),
                    "background_color": djust_config.get("PWA_BACKGROUND_COLOR", "#ffffff"),
                    "display": djust_config.get("PWA_DISPLAY", "standalone"),
                    "orientation": djust_config.get("PWA_ORIENTATION", "any"),
                    "start_url": djust_config.get("PWA_START_URL", "/"),
                    "scope": djust_config.get("PWA_SCOPE", "/"),
                }
            )
        except Exception:
            pass

        # Override with view-level settings
        if self.pwa_name:
            config["name"] = self.pwa_name
        if self.pwa_short_name:
            config["short_name"] = self.pwa_short_name
        if self.pwa_description:
            config["description"] = self.pwa_description

        config.update(
            {
                "theme_color": self.pwa_theme_color,
                "background_color": self.pwa_background_color,
                "display": self.pwa_display,
                "orientation": self.pwa_orientation,
                "start_url": self.pwa_start_url,
                "scope": self.pwa_scope,
            }
        )

        return config

    def register_pwa_handlers(self):
        """Register PWA-related event handlers."""
        if self._pwa_registered:
            return

        # Client-side event handlers will be registered via JavaScript
        self.push_event("pwa:register", self.get_pwa_config())
        self._pwa_registered = True

    def handle_install_prompt(self):
        """Handle PWA install prompt."""
        if not is_online():
            self.push_event("pwa:install_offline", {"message": "Install available when online"})
            return

        self.push_event("pwa:show_install_prompt", {"config": self.get_pwa_config()})

    def handle_app_update(self, version: str):
        """Handle app update notification."""
        self.push_event(
            "pwa:app_update",
            {
                "version": version,
                "message": f"App updated to version {version}. Refresh to see changes.",
            },
        )

    def get_context_data(self, **kwargs):
        """Add PWA config to template context."""
        context = super().get_context_data(**kwargs) if hasattr(super(), "get_context_data") else {}
        context["pwa_config"] = self.get_pwa_config()
        return context


class OfflineMixin:
    """
    Mixin that adds offline capabilities to LiveView.

    Features:
    - Offline state detection
    - Local data caching
    - Optimistic updates
    - Sync queue for offline actions

    Usage::

        class TodoView(OfflineMixin, LiveView):
            template_name = 'todo.html'
            offline_storage = 'todos'

            def mount(self, request, **kwargs):
                self.todos = self.get_cached_or_fetch('todos', Todo.objects.all())

            def add_todo(self, title):
                # Works offline
                todo = self.create_offline('todo', {'title': title})
                self.todos.append(todo)
    """

    # Offline configuration
    offline_storage: Optional[str] = None  # Storage key name
    offline_cache_duration: int = 86400  # 24 hours
    offline_max_items: int = 1000
    offline_compress: bool = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._storage = None
        self._sync_queue = None
        self._offline_state = {}

    @property
    def storage(self):
        """Get storage backend instance."""
        if not self._storage:
            self._storage = get_storage_backend(
                storage_name=self.offline_storage or self.__class__.__name__.lower()
            )
        return self._storage

    @property
    def sync_queue(self):
        """Get sync queue for offline actions."""
        if not self._sync_queue:
            self._sync_queue = SyncQueue(
                storage_name=f"{self.offline_storage or self.__class__.__name__.lower()}_sync"
            )
        return self._sync_queue

    def is_online(self) -> bool:
        """Check if the client is online."""
        return is_online()

    def get_cached_or_fetch(self, key: str, queryset, **kwargs) -> List[Dict]:
        """
        Get data from cache if offline, otherwise fetch from database.

        Args:
            key: Cache key
            queryset: Django QuerySet to fetch from
            **kwargs: Additional options

        Returns:
            List of data (dicts or objects)
        """
        if not self.is_online():
            # Try to get from cache
            cached = self.storage.get(key)
            if cached:
                logger.info(f"Using cached data for key: {key}")
                return cached

            logger.warning(f"No cached data for key: {key}, returning empty")
            return []

        # Online - fetch from database and cache
        try:
            if hasattr(queryset, "values"):
                data = list(queryset.values())
            else:
                data = list(queryset)

            # Cache the data
            self.storage.set(key, data, ttl=self.offline_cache_duration)
            logger.info(f"Cached {len(data)} items for key: {key}")
            return data

        except Exception as e:
            logger.error(f"Failed to fetch data for key {key}: {e}")
            # Fall back to cache if available
            cached = self.storage.get(key)
            return cached if cached else []

    def create_offline(self, model_name: str, data: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Create an object optimistically for offline use.

        Args:
            model_name: Model name for sync
            data: Object data
            **kwargs: Additional options

        Returns:
            Created object data with temporary ID
        """
        # Generate temporary ID
        temp_id = f"temp_{int(time.time() * 1000)}"

        obj_data = {
            "id": temp_id,
            "temp_id": temp_id,
            "created_offline": True,
            "created_at": time.time(),
            **data,
        }

        # Queue for sync when online
        action = OfflineAction(
            type="create",
            model=model_name,
            data=obj_data,
            timestamp=time.time(),
        )
        self.sync_queue.add(action)

        logger.info(f"Created offline object: {model_name} with temp_id: {temp_id}")
        return obj_data

    def update_offline(
        self, model_name: str, obj_id: Union[str, int], data: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """
        Update an object optimistically for offline use.

        Args:
            model_name: Model name for sync
            obj_id: Object ID
            data: Updated data
            **kwargs: Additional options

        Returns:
            Updated object data
        """
        obj_data = {"id": obj_id, "updated_offline": True, "updated_at": time.time(), **data}

        # Queue for sync when online
        action = OfflineAction(
            type="update",
            model=model_name,
            id=obj_id,
            data=obj_data,
            timestamp=time.time(),
        )
        self.sync_queue.add(action)

        logger.info(f"Updated offline object: {model_name} id: {obj_id}")
        return obj_data

    def delete_offline(self, model_name: str, obj_id: Union[str, int], **kwargs) -> bool:
        """
        Delete an object optimistically for offline use.

        Args:
            model_name: Model name for sync
            obj_id: Object ID
            **kwargs: Additional options

        Returns:
            True if queued for deletion
        """
        # Queue for sync when online
        action = OfflineAction(
            type="delete",
            model=model_name,
            id=obj_id,
            timestamp=time.time(),
        )
        self.sync_queue.add(action)

        logger.info(f"Deleted offline object: {model_name} id: {obj_id}")
        return True

    def sync_when_online(self):
        """Trigger sync when connection is restored."""
        if not self.is_online():
            logger.info("Still offline, skipping sync")
            return

        pending_actions = self.sync_queue.get_pending()
        if not pending_actions:
            logger.info("No pending sync actions")
            return

        logger.info(f"Syncing {len(pending_actions)} offline actions")

        # Trigger sync via event
        self.push_event("offline:sync_start", {"count": len(pending_actions)})

        # Process sync in background (implementation in SyncMixin)
        self._process_sync_queue()

    def _process_sync_queue(self):
        """Process pending sync actions."""
        # This will be implemented by SyncMixin
        pass

    def get_offline_state(self) -> Dict[str, Any]:
        """Get current offline state information."""
        return {
            "is_online": self.is_online(),
            "pending_sync_count": len(self.sync_queue.get_pending()),
            "cache_size": len(self.storage.keys()) if hasattr(self.storage, "keys") else 0,
            "connection_info": get_connection_info(),
        }

    def handle_connection_change(self, online: bool):
        """Handle connection state changes."""
        self._offline_state["is_online"] = online

        if online:
            logger.info("Connection restored, triggering sync")
            self.push_event("offline:online", self.get_offline_state())
            self.sync_when_online()
        else:
            logger.info("Connection lost, entering offline mode")
            self.push_event("offline:offline", self.get_offline_state())

    def get_context_data(self, **kwargs):
        """Add offline state to template context."""
        context = super().get_context_data(**kwargs) if hasattr(super(), "get_context_data") else {}
        context["offline_state"] = self.get_offline_state()
        return context


class SyncMixin:
    """
    Mixin that handles synchronization of offline data.

    Provides conflict resolution, background sync, and progress tracking.

    Usage::

        class DataView(SyncMixin, OfflineMixin, LiveView):
            sync_model = MyModel
            sync_conflict_strategy = 'server_wins'

            def sync_create_item(self, action_data):
                # Custom sync logic for creating items
                return MyModel.objects.create(**action_data)
    """

    # Sync configuration
    sync_model = None
    sync_conflict_strategy: str = "server_wins"  # 'client_wins', 'merge', 'manual'
    sync_batch_size: int = 10
    sync_timeout: int = 30  # seconds

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sync_manager = None
        self._sync_in_progress = False

    @property
    def sync_manager(self):
        """Get sync manager instance."""
        if not self._sync_manager:
            self._sync_manager = SyncManager(
                conflict_strategy=self.sync_conflict_strategy,
                batch_size=self.sync_batch_size,
                timeout=self.sync_timeout,
            )
        return self._sync_manager

    def _process_sync_queue(self):
        """Process pending sync actions (implements OfflineMixin method)."""
        if self._sync_in_progress:
            logger.info("Sync already in progress, skipping")
            return

        if not hasattr(self, "sync_queue"):
            logger.error("SyncMixin requires OfflineMixin")
            return

        self._sync_in_progress = True

        try:
            pending_actions = self.sync_queue.get_pending()

            # Group actions by type for batch processing
            create_actions = [a for a in pending_actions if a.type == "create"]
            update_actions = [a for a in pending_actions if a.type == "update"]
            delete_actions = [a for a in pending_actions if a.type == "delete"]

            total_processed = 0
            total_failed = 0

            # Process creates
            if create_actions:
                processed, failed = self._sync_create_actions(create_actions)
                total_processed += processed
                total_failed += failed

            # Process updates
            if update_actions:
                processed, failed = self._sync_update_actions(update_actions)
                total_processed += processed
                total_failed += failed

            # Process deletes
            if delete_actions:
                processed, failed = self._sync_delete_actions(delete_actions)
                total_processed += processed
                total_failed += failed

            # Send completion event
            self.push_event(
                "offline:sync_complete",
                {
                    "processed": total_processed,
                    "failed": total_failed,
                    "total": len(pending_actions),
                },
            )

            logger.info(f"Sync complete: {total_processed} processed, {total_failed} failed")

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            self.push_event("offline:sync_error", {"error": str(e)})
        finally:
            self._sync_in_progress = False

    def _sync_create_actions(self, actions: List[OfflineAction]) -> tuple[int, int]:
        """Sync create actions."""
        processed = 0
        failed = 0

        for action in actions:
            try:
                # Try custom sync method first
                method_name = f"sync_create_{action.model}"
                if hasattr(self, method_name):
                    result = getattr(self, method_name)(action.data)
                else:
                    # Default sync logic
                    result = self._default_create_sync(action)

                if result:
                    self.sync_queue.mark_completed(action.id)
                    processed += 1
                    logger.debug(f"Synced create action: {action.id}")
                else:
                    failed += 1
                    logger.warning(f"Failed to sync create action: {action.id}")

            except Exception as e:
                failed += 1
                logger.error(f"Error syncing create action {action.id}: {e}")
                self.sync_queue.mark_failed(action.id, str(e))

        return processed, failed

    def _sync_update_actions(self, actions: List[OfflineAction]) -> tuple[int, int]:
        """Sync update actions."""
        processed = 0
        failed = 0

        for action in actions:
            try:
                # Try custom sync method first
                method_name = f"sync_update_{action.model}"
                if hasattr(self, method_name):
                    result = getattr(self, method_name)(action.id, action.data)
                else:
                    # Default sync logic
                    result = self._default_update_sync(action)

                if result:
                    self.sync_queue.mark_completed(action.id)
                    processed += 1
                    logger.debug(f"Synced update action: {action.id}")
                else:
                    failed += 1
                    logger.warning(f"Failed to sync update action: {action.id}")

            except Exception as e:
                failed += 1
                logger.error(f"Error syncing update action {action.id}: {e}")
                self.sync_queue.mark_failed(action.id, str(e))

        return processed, failed

    def _sync_delete_actions(self, actions: List[OfflineAction]) -> tuple[int, int]:
        """Sync delete actions."""
        processed = 0
        failed = 0

        for action in actions:
            try:
                # Try custom sync method first
                method_name = f"sync_delete_{action.model}"
                if hasattr(self, method_name):
                    result = getattr(self, method_name)(action.id)
                else:
                    # Default sync logic
                    result = self._default_delete_sync(action)

                if result:
                    self.sync_queue.mark_completed(action.id)
                    processed += 1
                    logger.debug(f"Synced delete action: {action.id}")
                else:
                    failed += 1
                    logger.warning(f"Failed to sync delete action: {action.id}")

            except Exception as e:
                failed += 1
                logger.error(f"Error syncing delete action {action.id}: {e}")
                self.sync_queue.mark_failed(action.id, str(e))

        return processed, failed

    def _default_create_sync(self, action: OfflineAction) -> bool:
        """Default create sync implementation."""
        if not self.sync_model:
            logger.error("No sync_model configured for default sync")
            return False

        try:
            # Remove temporary fields
            data = action.data.copy()
            data.pop("temp_id", None)
            data.pop("created_offline", None)
            data.pop("id", None)  # Let database assign real ID

            # Create object
            obj = self.sync_model.objects.create(**data)
            logger.info(f"Created {self.sync_model.__name__} with ID {obj.id}")
            return True

        except Exception as e:
            logger.error(f"Default create sync failed: {e}")
            return False

    def _default_update_sync(self, action: OfflineAction) -> bool:
        """Default update sync implementation."""
        if not self.sync_model:
            logger.error("No sync_model configured for default sync")
            return False

        try:
            # Get existing object
            obj = self.sync_model.objects.get(id=action.id)

            # Apply updates
            data = action.data.copy()
            data.pop("updated_offline", None)
            data.pop("id", None)  # Don't update ID

            for field, value in data.items():
                if hasattr(obj, field):
                    setattr(obj, field, value)

            obj.save()
            logger.info(f"Updated {self.sync_model.__name__} ID {action.id}")
            return True

        except self.sync_model.DoesNotExist:
            logger.error(f"Object not found for update: {action.id}")
            return False
        except Exception as e:
            logger.error(f"Default update sync failed: {e}")
            return False

    def _default_delete_sync(self, action: OfflineAction) -> bool:
        """Default delete sync implementation."""
        if not self.sync_model:
            logger.error("No sync_model configured for default sync")
            return False

        try:
            obj = self.sync_model.objects.get(id=action.id)
            obj.delete()
            logger.info(f"Deleted {self.sync_model.__name__} ID {action.id}")
            return True

        except self.sync_model.DoesNotExist:
            logger.warning(f"Object already deleted: {action.id}")
            return True  # Consider this successful
        except Exception as e:
            logger.error(f"Default delete sync failed: {e}")
            return False
