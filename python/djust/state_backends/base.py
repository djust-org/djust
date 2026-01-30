"""
Base state backend ABC and shared constants.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple
from djust._rust import RustLiveView

logger = logging.getLogger(__name__)

# Performance warning threshold (configurable via DJUST_CONFIG)
DEFAULT_STATE_SIZE_WARNING_KB = 100

# Compression settings
DEFAULT_COMPRESSION_THRESHOLD_KB = 10  # Compress states larger than this
COMPRESSION_MARKER = b"\x01"  # Prefix byte to indicate compressed data
NO_COMPRESSION_MARKER = b"\x00"  # Prefix byte for uncompressed data

# Try to import zstd for compression (optional dependency)
try:
    import zstandard  # noqa: F401

    ZSTD_AVAILABLE = True
    logger.debug("zstd compression available")
except ImportError:
    ZSTD_AVAILABLE = False
    logger.debug("zstd not available - install with: pip install zstandard")


class DjustPerformanceWarning(UserWarning):
    """Warning for potential performance issues in djust LiveViews."""

    pass


class StateBackend(ABC):
    """
    Abstract base class for LiveView state storage backends.

    Backends manage the lifecycle of RustLiveView instances, providing:
    - Persistent storage across requests
    - TTL-based session expiration
    - Statistics and monitoring
    """

    @abstractmethod
    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        """
        Retrieve a RustLiveView instance and its timestamp from storage.

        Args:
            key: Unique session key

        Returns:
            Tuple of (RustLiveView, timestamp) if found, None otherwise
        """
        pass

    @abstractmethod
    def set(self, key: str, view: RustLiveView, ttl: Optional[int] = None):
        """
        Store a RustLiveView instance with optional TTL.

        Args:
            key: Unique session key
            view: RustLiveView instance to store
            ttl: Time-to-live in seconds (None = use backend default)
        """
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """
        Remove a session from storage.

        Args:
            key: Unique session key

        Returns:
            True if session was deleted, False if not found
        """
        pass

    @abstractmethod
    def cleanup_expired(self, ttl: Optional[int] = None) -> int:
        """
        Remove expired sessions based on TTL.

        Args:
            ttl: Time-to-live threshold in seconds

        Returns:
            Number of sessions cleaned up
        """
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """
        Get backend statistics.

        Returns:
            Dictionary with metrics like total_sessions, oldest_age, etc.
        """
        pass

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """
        Check backend health and availability.

        Performs basic connectivity and operational tests to verify the backend
        is functioning correctly. Useful for monitoring and readiness probes.

        Returns:
            Dictionary with health status:
            - status (str): 'healthy' or 'unhealthy'
            - latency_ms (float): Response time in milliseconds
            - error (str, optional): Error message if unhealthy
            - details (dict, optional): Additional backend-specific info
        """
        pass

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get detailed memory usage statistics.

        Override in subclasses for backend-specific memory tracking.

        Returns:
            Dictionary with memory metrics:
            - total_state_bytes: Total bytes used for state storage
            - average_state_bytes: Average bytes per session
            - largest_sessions: List of (key, size_bytes) for largest sessions
        """
        return {
            "total_state_bytes": 0,
            "average_state_bytes": 0,
            "largest_sessions": [],
        }
