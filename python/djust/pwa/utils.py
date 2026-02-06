"""
PWA utilities for connection detection and data management.
"""

import json
import logging
import time
import zlib
from typing import Any, Dict, Optional, List

logger = logging.getLogger(__name__)


def is_online() -> bool:
    """
    Check if the server-side application is online.

    On the server side, this always returns True since the server is
    inherently "online" if it's processing a request. Offline detection
    is handled client-side via JavaScript's navigator.onLine API and
    the service worker.

    The OfflineMixin.handle_connection_change() method receives client-side
    connection state changes via WebSocket events.

    Returns:
        True — server-side code is always online by definition
    """
    return True


def get_connection_info() -> Dict[str, Any]:
    """
    Get connection information.

    Returns:
        Dictionary with connection details
    """
    return {
        "online": is_online(),
        "timestamp": time.time(),
        "type": "unknown",  # Would be filled by client-side Network Information API
        "effective_type": "unknown",
        "downlink": None,
        "rtt": None,
    }


def estimate_sync_time(data_size: int, connection_type: str = "unknown") -> int:
    """
    Estimate sync time in seconds based on data size and connection.

    Args:
        data_size: Size of data to sync in bytes
        connection_type: Connection type ('slow-2g', '2g', '3g', '4g', 'wifi')

    Returns:
        Estimated sync time in seconds
    """
    # Rough estimates for different connection types (bytes per second)
    connection_speeds = {
        "slow-2g": 50 * 1024,  # 50 KB/s
        "2g": 250 * 1024,  # 250 KB/s
        "3g": 750 * 1024,  # 750 KB/s
        "4g": 1.5 * 1024 * 1024,  # 1.5 MB/s
        "wifi": 5 * 1024 * 1024,  # 5 MB/s
        "unknown": 1 * 1024 * 1024,  # 1 MB/s (conservative)
    }

    speed = connection_speeds.get(connection_type, connection_speeds["unknown"])

    # Add 20% overhead for protocol, retries, etc.
    estimated_seconds = (data_size / speed) * 1.2

    # Minimum 1 second, maximum 300 seconds (5 minutes)
    return max(1, min(int(estimated_seconds), 300))


def compress_state(data: Any) -> bytes:
    """
    Compress state data for efficient storage.

    Args:
        data: Data to compress

    Returns:
        Compressed data as bytes
    """
    try:
        # Serialize to JSON first
        json_data = json.dumps(data, default=str, separators=(",", ":"))

        # Compress with zlib
        compressed = zlib.compress(json_data.encode("utf-8"), level=6)

        logger.debug(
            "Compressed %d bytes to %d bytes (%.1f%%)",
            len(json_data),
            len(compressed),
            len(compressed) / len(json_data) * 100,
        )

        return compressed
    except Exception as e:
        logger.error("Failed to compress state: %s", e, exc_info=True)
        # Fall back to JSON without compression
        return json.dumps(data, default=str).encode("utf-8")


def decompress_state(compressed_data: bytes) -> Any:
    """
    Decompress state data.

    Args:
        compressed_data: Compressed data bytes

    Returns:
        Decompressed data
    """
    try:
        # Try to decompress with zlib first
        try:
            decompressed = zlib.decompress(compressed_data)
            json_data = decompressed.decode("utf-8")
        except zlib.error:
            # Might not be compressed, try as raw JSON
            json_data = compressed_data.decode("utf-8")

        return json.loads(json_data)
    except Exception as e:
        logger.error("Failed to decompress state: %s", e, exc_info=True)
        return None


def calculate_storage_quota(storage_type: str = "indexeddb") -> Dict[str, int]:
    """
    Calculate available storage quota.

    Args:
        storage_type: Type of storage ('indexeddb', 'localstorage')

    Returns:
        Dictionary with quota information in bytes
    """
    # Browser-typical quota limits. Actual quota is determined client-side
    # via the StorageManager API (navigator.storage.estimate()). These
    # server-side defaults represent conservative browser limits for
    # capacity planning and are NOT live measurements.
    default_quotas = {
        "indexeddb": {
            "total": 50 * 1024 * 1024 * 1024,  # 50GB typical browser limit
            "available": 0,  # Unknown server-side
            "used": 0,  # Unknown server-side
            "note": "Actual usage available via navigator.storage.estimate()",
        },
        "localstorage": {
            "total": 10 * 1024 * 1024,  # 10MB typical browser limit
            "available": 0,  # Unknown server-side
            "used": 0,  # Unknown server-side
            "note": "Actual usage available via client-side measurement",
        },
    }

    return default_quotas.get(storage_type, default_quotas["indexeddb"])


def format_bytes(bytes_count: int) -> str:
    """
    Format byte count as human-readable string.

    Args:
        bytes_count: Number of bytes

    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(bytes_count)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    return f"{size:.1f} {units[unit_index]}"


def cleanup_old_data(storage_backend, max_age_hours: int = 168) -> Dict[str, int]:
    """
    Clean up old data from storage backend.

    Args:
        storage_backend: Storage backend instance
        max_age_hours: Maximum age in hours (default: 1 week)

    Returns:
        Dictionary with cleanup statistics
    """
    try:
        cutoff_time = time.time() - (max_age_hours * 3600)

        keys = storage_backend.keys()
        removed_count = 0
        bytes_freed = 0

        for key in keys:
            try:
                data = storage_backend.get(key)

                # Check if data has timestamp
                if isinstance(data, dict) and "timestamp" in data:
                    if data["timestamp"] < cutoff_time:
                        # Calculate size before removal
                        size = len(json.dumps(data, default=str).encode("utf-8"))

                        if storage_backend.delete(key):
                            removed_count += 1
                            bytes_freed += size
            except Exception as e:
                logger.warning("Error checking key %s during cleanup: %s", key, e)

        logger.info("Cleanup removed %d items, freed %s", removed_count, format_bytes(bytes_freed))

        return {
            "removed_count": removed_count,
            "bytes_freed": bytes_freed,
            "keys_checked": len(keys),
        }
    except Exception as e:
        logger.error("Cleanup failed: %s", e, exc_info=True)
        return {"removed_count": 0, "bytes_freed": 0, "keys_checked": 0}


def validate_offline_data(data: Any, schema: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Validate offline data before sync.

    Args:
        data: Data to validate
        schema: Optional validation schema

    Returns:
        Validation result with errors if any
    """
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
    }

    try:
        # Basic validation checks
        if data is None:
            result["valid"] = False
            result["errors"].append("Data is None")
            return result

        # Check data size
        serialized_size = len(json.dumps(data, default=str).encode("utf-8"))
        if serialized_size > 1024 * 1024:  # 1MB
            result["warnings"].append(f"Large data size: {format_bytes(serialized_size)}")

        # Check for required fields if schema provided
        if schema and isinstance(data, dict):
            required_fields = schema.get("required", [])
            for field in required_fields:
                if field not in data:
                    result["valid"] = False
                    result["errors"].append(f"Missing required field: {field}")

        # Check for temporary IDs that need replacement
        if isinstance(data, dict):
            if data.get("id", "").startswith("temp_"):
                result["warnings"].append("Contains temporary ID that will be replaced during sync")

    except Exception as e:
        result["valid"] = False
        result["errors"].append(f"Validation error: {str(e)}")

    return result


def merge_offline_changes(
    server_data: Dict, offline_changes: Dict, strategy: str = "client_wins"
) -> Dict[str, Any]:
    """
    Merge offline changes with server data.

    Args:
        server_data: Current server data
        offline_changes: Local offline changes
        strategy: Merge strategy ('client_wins', 'server_wins', 'merge_by_timestamp')

    Returns:
        Merged data and conflict information
    """
    result = {
        "data": server_data.copy(),
        "conflicts": [],
        "strategy_used": strategy,
    }

    try:
        if strategy == "client_wins":
            # Client changes take precedence
            result["data"].update(offline_changes)

        elif strategy == "server_wins":
            # Server data takes precedence (no changes needed)
            pass

        elif strategy == "merge_by_timestamp":
            # Merge based on field-level timestamps
            for field, value in offline_changes.items():
                server_value = server_data.get(field)

                # If we have timestamp info, use it
                if isinstance(value, dict) and "value" in value and "timestamp" in value:
                    if isinstance(server_value, dict) and "timestamp" in server_value:
                        if value["timestamp"] > server_value["timestamp"]:
                            result["data"][field] = value["value"]
                        else:
                            result["conflicts"].append(
                                {
                                    "field": field,
                                    "client_value": value["value"],
                                    "server_value": server_value.get("value", server_value),
                                    "resolution": "used_server_value",
                                }
                            )
                    else:
                        result["data"][field] = value["value"]
                else:
                    # No timestamp info, default to client wins
                    result["data"][field] = value

        else:
            logger.warning("Unknown merge strategy: %s, using client_wins", strategy)
            result["data"].update(offline_changes)
            result["strategy_used"] = "client_wins"

    except Exception as e:
        logger.error("Merge failed: %s", e, exc_info=True)
        result["data"] = server_data  # Fall back to server data
        result["conflicts"].append({"error": str(e), "resolution": "used_server_data"})

    return result


class PWAHealthMonitor:
    """
    Monitor PWA health and performance metrics.
    """

    def __init__(self):
        self.metrics = {
            "sync_success_rate": 0.0,
            "average_sync_time": 0.0,
            "storage_usage": 0,
            "cache_hit_rate": 0.0,
            "offline_duration": 0.0,
        }
        self._sync_times = []
        self._cache_requests = 0
        self._cache_hits = 0

    def record_sync_success(self, duration: float):
        """Record successful sync."""
        self._sync_times.append(duration)

        # Keep only last 100 sync times
        if len(self._sync_times) > 100:
            self._sync_times.pop(0)

        self.metrics["average_sync_time"] = sum(self._sync_times) / len(self._sync_times)

    def record_sync_failure(self):
        """Record sync failure for health monitoring."""
        self.metrics["sync_failures"] = self.metrics.get("sync_failures", 0) + 1
        total = self.metrics.get("total_syncs", 0)
        if total > 0:
            failures = self.metrics["sync_failures"]
            self.metrics["sync_success_rate"] = (total - failures) / total

    def record_cache_request(self, hit: bool):
        """Record cache request."""
        self._cache_requests += 1
        if hit:
            self._cache_hits += 1

        if self._cache_requests > 0:
            self.metrics["cache_hit_rate"] = self._cache_hits / self._cache_requests

    def get_health_report(self) -> Dict[str, Any]:
        """Get PWA health report."""
        return {
            "metrics": self.metrics.copy(),
            "recommendations": self._get_recommendations(),
            "timestamp": time.time(),
        }

    def _get_recommendations(self) -> List[str]:
        """Get performance recommendations."""
        recommendations = []

        if self.metrics["cache_hit_rate"] < 0.7:
            recommendations.append("Consider caching more frequently accessed data")

        if self.metrics["average_sync_time"] > 30:
            recommendations.append(
                "Sync times are high, consider reducing data size or using compression"
            )

        if self.metrics["storage_usage"] > 80:
            recommendations.append("Storage usage is high, consider cleanup policies")

        return recommendations
