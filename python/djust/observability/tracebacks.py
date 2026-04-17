"""
Ring-buffered exception capture.

Populated from `djust.security.error_handling.handle_exception()`, which
is the single entry point every consumer / view / actor error flows
through. The MCP reads this via /_djust/observability/last_traceback/.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from typing import Any, Dict, List, Optional

_MAX_ENTRIES = 50

_buffer: "deque[Dict[str, Any]]" = deque(maxlen=_MAX_ENTRIES)
_lock = threading.Lock()


def record_traceback(
    exception: BaseException,
    *,
    error_type: str = "default",
    event_name: Optional[str] = None,
    view_class: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Push a captured exception onto the ring buffer.

    Called from `handle_exception()` so every djust-managed exception
    lands here exactly once. Safe to call concurrently.
    """
    with _lock:
        _buffer.append(
            {
                "timestamp_ms": int(time.time() * 1000),
                "exception_type": type(exception).__name__,
                "exception_module": type(exception).__module__,
                "message": str(exception),
                "error_type": error_type,
                "event_name": event_name,
                "view_class": view_class,
                "session_id": session_id,
                "traceback": "".join(traceback.format_exception(exception)),
            }
        )


def get_recent_tracebacks(n: int = 1) -> List[Dict[str, Any]]:
    """Return up to the last `n` entries, newest first."""
    with _lock:
        items = list(_buffer)
    items.reverse()
    return items[:n]


def get_buffer_size() -> int:
    """Current buffer length. Diagnostic helper."""
    with _lock:
        return len(_buffer)


def _clear_tracebacks() -> None:
    """Test-only reset."""
    with _lock:
        _buffer.clear()
