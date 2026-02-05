"""
Synchronization management for offline data in djust PWA applications.
"""

import json
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass

from .storage import OfflineAction

logger = logging.getLogger(__name__)


class MergeStrategy(Enum):
    """Conflict resolution strategies for data synchronization."""

    CLIENT_WINS = "client_wins"
    SERVER_WINS = "server_wins"
    MERGE_BY_TIMESTAMP = "merge_by_timestamp"
    MANUAL_RESOLUTION = "manual_resolution"


@dataclass
class SyncResult:
    """Result of a synchronization operation."""

    success: bool
    processed_count: int
    failed_count: int
    conflicts: List[Dict[str, Any]]
    errors: List[str]
    duration_seconds: float

    def __post_init__(self):
        if self.success is None:
            self.success = self.failed_count == 0


class ConflictResolver:
    """
    Handles conflicts during data synchronization.

    Provides different strategies for resolving conflicts when
    offline changes conflict with server changes.
    """

    def __init__(self, default_strategy: MergeStrategy = MergeStrategy.CLIENT_WINS):
        self.default_strategy = default_strategy
        self._custom_resolvers: Dict[str, Callable] = {}

    def register_resolver(self, model_name: str, resolver_func: Callable):
        """
        Register custom conflict resolver for a specific model.

        Args:
            model_name: Model name
            resolver_func: Function that takes (local_data, server_data) and returns merged data
        """
        self._custom_resolvers[model_name] = resolver_func
        logger.info(f"Registered custom conflict resolver for model: {model_name}")

    def resolve_conflict(
        self,
        model_name: str,
        local_data: Dict[str, Any],
        server_data: Dict[str, Any],
        strategy: Optional[MergeStrategy] = None,
    ) -> Dict[str, Any]:
        """
        Resolve conflict between local and server data.

        Args:
            model_name: Name of the model
            local_data: Local (offline) data
            server_data: Server data
            strategy: Override strategy for this resolution

        Returns:
            Resolved data dictionary
        """
        strategy = strategy or self.default_strategy

        # Check for custom resolver first
        if model_name in self._custom_resolvers:
            try:
                return self._custom_resolvers[model_name](local_data, server_data)
            except Exception as e:
                logger.error(f"Custom resolver failed for {model_name}: {e}")
                # Fall back to default strategy

        if strategy == MergeStrategy.CLIENT_WINS:
            return self._client_wins(local_data, server_data)
        elif strategy == MergeStrategy.SERVER_WINS:
            return self._server_wins(local_data, server_data)
        elif strategy == MergeStrategy.MERGE_BY_TIMESTAMP:
            return self._merge_by_timestamp(local_data, server_data)
        else:
            # Default to client wins
            return self._client_wins(local_data, server_data)

    def _client_wins(
        self, local_data: Dict[str, Any], server_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Client data takes precedence."""
        result = server_data.copy()
        result.update(local_data)
        return result

    def _server_wins(
        self, local_data: Dict[str, Any], server_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Server data takes precedence."""
        return server_data.copy()

    def _merge_by_timestamp(
        self, local_data: Dict[str, Any], server_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge based on field-level timestamps."""
        result = {}

        # Get all fields from both datasets
        all_fields = set(local_data.keys()) | set(server_data.keys())

        for field in all_fields:
            local_value = local_data.get(field)
            server_value = server_data.get(field)

            # If only one has the field, use it
            if field not in server_data:
                result[field] = local_value
            elif field not in local_data:
                result[field] = server_value
            else:
                # Both have the field, check timestamps
                local_timestamp = self._extract_timestamp(field, local_value, local_data)
                server_timestamp = self._extract_timestamp(field, server_value, server_data)

                if local_timestamp > server_timestamp:
                    result[field] = local_value
                else:
                    result[field] = server_value

        return result

    def _extract_timestamp(self, field: str, value: Any, data: Dict[str, Any]) -> float:
        """Extract timestamp for a field."""
        # Look for field-specific timestamp
        timestamp_field = f"{field}_timestamp"
        if timestamp_field in data:
            return float(data[timestamp_field])

        # Look for general timestamp fields
        for ts_field in ["updated_at", "modified_at", "timestamp"]:
            if ts_field in data:
                try:
                    return float(data[ts_field])
                except (ValueError, TypeError):
                    pass

        # No timestamp found, return 0
        return 0.0


class SyncManager:
    """
    Manages synchronization of offline data with the server.

    Handles batching, retries, conflict resolution, and progress tracking.
    """

    def __init__(
        self,
        conflict_strategy: str = "client_wins",
        batch_size: int = 10,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.batch_size = batch_size
        self.timeout = timeout
        self.max_retries = max_retries

        # Convert string to enum
        strategy_map = {
            "client_wins": MergeStrategy.CLIENT_WINS,
            "server_wins": MergeStrategy.SERVER_WINS,
            "merge_by_timestamp": MergeStrategy.MERGE_BY_TIMESTAMP,
            "manual_resolution": MergeStrategy.MANUAL_RESOLUTION,
        }
        self.conflict_resolver = ConflictResolver(
            strategy_map.get(conflict_strategy, MergeStrategy.CLIENT_WINS)
        )

        self._sync_handlers: Dict[str, Callable] = {}
        self._sync_in_progress = False

    def register_sync_handler(self, model_name: str, handler_func: Callable):
        """
        Register sync handler for a specific model.

        Args:
            model_name: Model name
            handler_func: Function that handles sync for this model type
        """
        self._sync_handlers[model_name] = handler_func
        logger.info(f"Registered sync handler for model: {model_name}")

    def sync_actions(self, actions: List[OfflineAction]) -> SyncResult:
        """
        Synchronize list of offline actions.

        Args:
            actions: List of offline actions to sync

        Returns:
            SyncResult with operation details
        """
        if self._sync_in_progress:
            logger.warning("Sync already in progress")
            return SyncResult(
                success=False,
                processed_count=0,
                failed_count=0,
                conflicts=[],
                errors=["Sync already in progress"],
                duration_seconds=0.0,
            )

        self._sync_in_progress = True
        start_time = time.time()

        try:
            return self._perform_sync(actions)
        finally:
            self._sync_in_progress = False
            logger.info(f"Sync completed in {time.time() - start_time:.2f} seconds")

    def _perform_sync(self, actions: List[OfflineAction]) -> SyncResult:
        """Perform the actual synchronization."""
        processed_count = 0
        failed_count = 0
        conflicts = []
        errors = []
        start_time = time.time()

        # Group actions by type and model
        action_groups = self._group_actions(actions)

        # Process each group
        for group_key, group_actions in action_groups.items():
            action_type, model_name = group_key

            logger.info(f"Processing {len(group_actions)} {action_type} actions for {model_name}")

            # Process in batches
            for batch in self._create_batches(group_actions):
                try:
                    batch_result = self._sync_batch(batch, action_type, model_name)
                    processed_count += batch_result["processed"]
                    failed_count += batch_result["failed"]
                    conflicts.extend(batch_result.get("conflicts", []))
                    errors.extend(batch_result.get("errors", []))

                except Exception as e:
                    logger.error(f"Batch sync failed: {e}")
                    failed_count += len(batch)
                    errors.append(f"Batch sync error: {str(e)}")

        duration = time.time() - start_time

        return SyncResult(
            success=failed_count == 0,
            processed_count=processed_count,
            failed_count=failed_count,
            conflicts=conflicts,
            errors=errors,
            duration_seconds=duration,
        )

    def _group_actions(self, actions: List[OfflineAction]) -> Dict[tuple, List[OfflineAction]]:
        """Group actions by type and model."""
        groups = {}

        for action in actions:
            key = (action.type, action.model)
            if key not in groups:
                groups[key] = []
            groups[key].append(action)

        return groups

    def _create_batches(self, actions: List[OfflineAction]) -> List[List[OfflineAction]]:
        """Create batches from actions list."""
        batches = []

        for i in range(0, len(actions), self.batch_size):
            batch = actions[i : i + self.batch_size]
            batches.append(batch)

        return batches

    def _sync_batch(
        self, batch: List[OfflineAction], action_type: str, model_name: str
    ) -> Dict[str, Any]:
        """Sync a batch of actions."""
        # Check for custom sync handler
        handler_key = f"{action_type}_{model_name}"
        if handler_key in self._sync_handlers:
            return self._sync_handlers[handler_key](batch)

        # Use default sync logic
        if action_type == "create":
            return self._sync_create_batch(batch, model_name)
        elif action_type == "update":
            return self._sync_update_batch(batch, model_name)
        elif action_type == "delete":
            return self._sync_delete_batch(batch, model_name)
        else:
            return {
                "processed": 0,
                "failed": len(batch),
                "errors": [f"Unknown action type: {action_type}"],
            }

    def _sync_create_batch(self, batch: List[OfflineAction], model_name: str) -> Dict[str, Any]:
        """Sync create actions."""
        processed = 0
        failed = 0
        errors = []

        for action in batch:
            try:
                # Remove temporary fields
                data = action.data.copy()
                data.pop("temp_id", None)
                data.pop("created_offline", None)
                data.pop("id", None)  # Let server assign real ID

                # In a real implementation, this would make an API call
                logger.info(f"Creating {model_name} with data: {data}")
                processed += 1

            except Exception as e:
                failed += 1
                errors.append(f"Create failed for action {action.id}: {str(e)}")

        return {"processed": processed, "failed": failed, "errors": errors}

    def _sync_update_batch(self, batch: List[OfflineAction], model_name: str) -> Dict[str, Any]:
        """Sync update actions."""
        processed = 0
        failed = 0
        conflicts = []
        errors = []

        for action in batch:
            try:
                # Get current server data (simulate API call)
                server_data = self._fetch_server_data(model_name, action.id)

                if server_data:
                    # Resolve conflicts
                    resolved_data = self.conflict_resolver.resolve_conflict(
                        model_name, action.data, server_data
                    )

                    # Check if there were conflicts
                    if resolved_data != action.data:
                        conflicts.append(
                            {
                                "action_id": action.id,
                                "model": model_name,
                                "local_data": action.data,
                                "server_data": server_data,
                                "resolved_data": resolved_data,
                            }
                        )

                    # Send update to server (simulate API call)
                    logger.info(f"Updating {model_name} {action.id} with data: {resolved_data}")
                    processed += 1
                else:
                    # Object doesn't exist on server
                    failed += 1
                    errors.append(f"Object not found on server: {model_name} {action.id}")

            except Exception as e:
                failed += 1
                errors.append(f"Update failed for action {action.id}: {str(e)}")

        return {"processed": processed, "failed": failed, "conflicts": conflicts, "errors": errors}

    def _sync_delete_batch(self, batch: List[OfflineAction], model_name: str) -> Dict[str, Any]:
        """Sync delete actions."""
        processed = 0
        failed = 0
        errors = []

        for action in batch:
            try:
                # Send delete to server (simulate API call)
                logger.info(f"Deleting {model_name} {action.id}")
                processed += 1

            except Exception as e:
                failed += 1
                errors.append(f"Delete failed for action {action.id}: {str(e)}")

        return {"processed": processed, "failed": failed, "errors": errors}

    def _fetch_server_data(self, model_name: str, obj_id: Any) -> Optional[Dict[str, Any]]:
        """Fetch current server data for an object (simulate API call)."""
        # In a real implementation, this would make an API call
        logger.debug(f"Fetching server data for {model_name} {obj_id}")

        # Simulate server data
        return {
            "id": obj_id,
            "updated_at": time.time() - 3600,  # 1 hour ago
            "name": f"Server value for {obj_id}",
        }


# Global sync handler registry
_sync_handlers: Dict[str, Callable] = {}


def register_sync_handler(model_name: str, action_type: str):
    """
    Decorator to register sync handlers.

    Usage:
        @register_sync_handler('Task', 'create')
        def sync_create_task(actions):
            # Handle creating tasks
            pass
    """

    def decorator(func: Callable):
        handler_key = f"{action_type}_{model_name}"
        _sync_handlers[handler_key] = func
        logger.info(f"Registered sync handler: {handler_key}")
        return func

    return decorator


def sync_endpoint_view(request):
    """
    Django view to handle sync requests from service worker.

    Expected POST data:
    {
        "actions": [list of OfflineAction dicts],
        "version": "service worker version"
    }

    Returns:
    {
        "success": bool,
        "synced_ids": [list of successfully synced action IDs],
        "conflicts": [list of conflict descriptions],
        "errors": [list of error messages]
    }
    """
    from django.http import JsonResponse
    from django.views.decorators.csrf import csrf_exempt

    @csrf_exempt
    def sync_view(request):
        if request.method != "POST":
            return JsonResponse({"error": "Method not allowed"}, status=405)

        try:
            data = json.loads(request.body)
            actions_data = data.get("actions", [])
            version = data.get("version", "unknown")

            logger.info(
                f"Received sync request with {len(actions_data)} actions from version {version}"
            )

            # Convert to OfflineAction objects
            actions = [OfflineAction.from_dict(action_data) for action_data in actions_data]

            # Create sync manager and process
            sync_manager = SyncManager()

            # Register any handlers from global registry
            for handler_key, handler_func in _sync_handlers.items():
                model_name = handler_key.split("_", 1)[1]
                sync_manager.register_sync_handler(model_name, handler_func)

            # Perform sync
            result = sync_manager.sync_actions(actions)

            # Prepare response
            response_data = {
                "success": result.success,
                "synced_ids": [action.id for action in actions[: result.processed_count]],
                "processed_count": result.processed_count,
                "failed_count": result.failed_count,
                "conflicts": result.conflicts,
                "errors": result.errors,
                "duration_seconds": result.duration_seconds,
            }

            return JsonResponse(response_data)

        except Exception as e:
            logger.error(f"Sync endpoint error: {e}")
            return JsonResponse({"success": False, "error": str(e)}, status=500)

    return sync_view(request)
