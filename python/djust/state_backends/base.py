"""
Base state backend ABC and shared constants.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Tuple, TypedDict
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
    from importlib import import_module as _im

    _im("zstandard")
    ZSTD_AVAILABLE = True
    del _im
    logger.debug("zstd compression available")
except ImportError:
    ZSTD_AVAILABLE = False
    logger.debug("zstd not available - install with: pip install zstandard")


class DjustPerformanceWarning(UserWarning):
    """Warning for potential performance issues in djust LiveViews."""

    pass


class SnapshotBreakdown(TypedDict):
    """Where the bytes of a stored view snapshot are (see :func:`describe_snapshot`)."""

    template: int
    state: int
    tree: int
    #: ``(context key, bytes)`` for the largest state entries, biggest first.
    keys: List[Tuple[str, int]]


def describe_snapshot(snapshot: bytes, top: int = 3) -> Optional[SnapshotBreakdown]:
    """Split a ``RustLiveView.serialize_msgpack()`` blob into its three parts (#3626).

    The blob is one positional MessagePack array,
    ``[template_source, state, last_vdom, version, timestamp]``
    (``SerializableViewState`` in ``crates/djust_live``): the template text, the
    context the Rust renderer holds (every key the render used, whether it came
    from a public attribute, a render-only value or a stream), and the rendered
    tree the next turn diffs against. The sizes are exact byte spans of the blob
    itself, read with the unpacker's ``skip``/``tell`` so nothing is decoded or
    re-encoded.

    Returns ``None`` when the blob is not that shape, so a caller falls back to
    the total alone rather than reporting a wrong split.
    """
    import msgpack

    try:
        unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
        unpacker.feed(snapshot)
        if unpacker.read_array_header() != 5:
            return None
        start = unpacker.tell()
        unpacker.skip()
        template = unpacker.tell() - start

        start = unpacker.tell()
        entries = unpacker.read_map_header()
        keys: List[Tuple[str, int]] = []
        for _ in range(entries):
            name = unpacker.unpack()
            value_start = unpacker.tell()
            unpacker.skip()
            if not isinstance(name, str):
                return None
            keys.append((name, unpacker.tell() - value_start))
        state = unpacker.tell() - start

        start = unpacker.tell()
        unpacker.skip()
        tree = unpacker.tell() - start
    except (msgpack.exceptions.UnpackException, ValueError, TypeError, StopIteration):
        # Not a snapshot this reader understands (a future layout): report the
        # total only. Nothing here is the caller's data, so there is no
        # exception worth logging.
        logger.debug("describe_snapshot: unrecognised snapshot layout")
        return None
    keys.sort(key=lambda item: item[1], reverse=True)
    return {"template": template, "state": state, "tree": tree, "keys": keys[:top]}


def large_state_warning_message(
    key: str, total: int, threshold_kb: int, snapshot: Optional[bytes] = None
) -> str:
    """The text of the ``Large LiveView state`` warning (#3626).

    What is measured is the whole stored snapshot, so the message says what is
    in it and which advice moves which part. Each claim below is pinned by
    ``test_large_state_warning_3626.py`` against a real view, because the
    previous advice ("use temporary_assigns or streams") did not lower this
    number for the case it was shown for.
    """
    head = "Large LiveView state detected for '%s': %.1fKB (threshold: %sKB)." % (
        key,
        total / 1024,
        threshold_kb,
    )
    parts = describe_snapshot(snapshot) if snapshot is not None else None
    if parts is None:
        breakdown = ""
    else:
        biggest = ", ".join("%s %.1fKB" % (name, size / 1024) for name, size in parts["keys"])
        breakdown = (
            " The stored snapshot holds the context the template used (%.1fKB; largest: %s), "
            "the rendered tree the next turn diffs against (%.1fKB) and the template (%.1fKB)."
            % (
                parts["state"] / 1024,
                biggest or "none",
                parts["tree"] / 1024,
                parts["template"] / 1024,
            )
        )
    advice = (
        " Both of the first two grow with the rows you render, so render fewer and "
        "leaner ones (paginate, or pass only the fields the template prints). "
        "temporary_assigns empties the context copy after the next render but leaves the "
        "tree; streams and get_render_only_context() do not shrink this snapshot (the latter "
        "only keeps values out of the session save). See the Best Practices guide, "
        "'Large LiveView state'."
    )
    return head + breakdown + advice


class StateBackend(ABC):
    """
    Abstract base class for LiveView state storage backends.

    Backends manage the lifecycle of RustLiveView instances, providing:
    - Persistent storage across requests
    - TTL-based session expiration
    - Statistics and monitoring
    """

    def _register_observation(self, lifetime: str) -> None:
        """Register a newly rendered private observation lifetime, without resetting it.

        Custom backends must implement atomic registration/claim before opting
        into the staged client observation contract. Never fall back to a
        non-atomic get/set or application session save.
        """
        raise NotImplementedError("State backend does not support observation cursors")

    def _claim_observation(self, lifetime: str, sequence: int) -> bool:
        """Atomically advance an existing lifetime before invoking its observer.

        Missing/expired lifetimes fail closed. Callback failure cannot roll the
        claim back. This is deduplication, not a durable business-action log.
        """
        raise NotImplementedError("State backend does not support observation cursors")

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
    def set(self, key: str, view: RustLiveView, ttl: Optional[int] = None) -> None:
        """
        Store a RustLiveView instance with optional TTL.

        Args:
            key: Unique session key
            view: RustLiveView instance to store
            ttl: Time-to-live in seconds. None = use backend default; positive int
                = expire after N seconds; 0 or negative = never expire (sessions
                persist until explicitly deleted).
        """
        pass

    def refresh(self, key: str, view: RustLiveView) -> None:
        """Record ``view``'s state after its owner rendered it.

        Called on the owner's thread after every render of a view whose state
        is cached. A backend that holds the latest state in process (the
        in-memory backend) re-stores its snapshot, so the next reader of the
        key (an HTTP page-POST request, a reconnect, another tab) starts from
        the last render. The default does nothing: a remote backend keeps the
        state written by :meth:`set`, as it always has.
        """

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
            ttl: Time-to-live threshold in seconds. None = use backend default;
                positive int = remove sessions older than N seconds; 0 or negative
                = do not remove any sessions (never-expire mode).

        Returns:
            Number of sessions cleaned up
        """
        pass

    @abstractmethod
    def delete_all(self) -> int:
        """
        Delete every session unconditionally.

        Used by ``djust clear --all``.  Implementations must provide an
        efficient bulk-delete operation for their storage backend.

        Returns:
            Number of sessions deleted
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
