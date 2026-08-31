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
                # `getattr` with a default (#2488): an exception class built by
                # `type()` in a namespace with no `__name__` has no
                # `__module__`, and an unguarded read would raise INSIDE the
                # exception recorder — the one place a second exception has
                # nowhere to go.
                "exception_module": getattr(type(exception), "__module__", "<unknown>"),
                "message": str(exception),
                "error_type": error_type,
                "event_name": event_name,
                "view_class": view_class,
                "session_id": session_id,
                "traceback": _format(exception),
            }
        )


def _format(exception: BaseException) -> str:
    """``traceback.format_exception``, fail-soft (#2488).

    Guarding this module's OWN ``__module__`` read is not enough, and the
    regression test is what proved it: CPython's ``traceback`` makes the same
    unguarded read one frame further in —
    ``TracebackException.format_exception_only`` does ``smod =
    self.exc_type.__module__`` — so an exception class with no ``__module__``
    raises from inside the formatter as well. (It is loud: it takes pytest's own
    reporter down with it.)

    A recorder that raises loses the exception it was called to record AND
    replaces it with a less useful one, so this fails soft to the two spellings
    that cannot raise. Deliberately narrow: only the formatting call is
    wrapped, so a genuine bug in this module is not swallowed with it.
    """
    try:
        return "".join(traceback.format_exception(exception))
    except Exception:  # noqa: BLE001 — the recorder must not raise
        return f"{type(exception).__name__}: {exception}\n"


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
