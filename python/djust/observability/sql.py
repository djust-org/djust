"""
Per-event SQL query capture.

When enabled via `capture_for_event()`, a `django.db.connection.execute_wrapper`
intercepts every query fired during handler execution and tags it with
`(session_id, event_id, handler_name)`. The MCP reads the aggregated
log via `/_djust/observability/sql_queries/`.

Unlike Django's own `connection.queries`, this buffer survives across
requests + is event-scoped — so the agent can ask "what SQL did the
increment handler just run?" and get a clean answer.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

_MAX_ENTRIES = 500

_buffer: "deque[Dict[str, Any]]" = deque(maxlen=_MAX_ENTRIES)
_lock = threading.Lock()

# Per-thread active scope — lets nested wrappers know the current tags.
_active = threading.local()


def _get_active() -> Optional[Dict[str, Any]]:
    return getattr(_active, "scope", None)


def _push_entry(entry: Dict[str, Any]) -> None:
    with _lock:
        _buffer.append(entry)


def _short_stack_top() -> str:
    """Grab the top frame OUTSIDE djust itself so the log points at the
    caller's code (where the query was issued from), not at Django or
    this module."""
    stack = traceback.extract_stack(limit=30)
    # Walk outermost-first, find the first frame that's NOT in djust,
    # Django, or observability itself.
    for frame in stack[:-4]:  # skip the wrapper frames themselves
        fname = frame.filename
        if "/djust/" in fname and "/site-packages/django" not in fname:
            continue
        if "/site-packages/django" in fname:
            continue
        if fname.endswith("sql.py"):
            continue
        return f"{frame.filename}:{frame.lineno} in {frame.name}"
    # Fallback: the deepest non-wrapper frame.
    if len(stack) >= 2:
        f = stack[-2]
        return f"{f.filename}:{f.lineno} in {f.name}"
    return ""


def _execute_wrapper(execute, sql, params, many, context):
    """Django execute_wrapper — called for every query while installed."""
    scope = _get_active()
    if scope is None:
        # Not in a capture scope (e.g. a stray query outside event dispatch).
        # Still record it so the buffer stays honest about global activity.
        scope = {"session_id": None, "event_id": None, "handler_name": None}

    t0 = time.perf_counter()
    try:
        return execute(sql, params, many, context)
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000
        _push_entry(
            {
                "timestamp_ms": int(time.time() * 1000),
                "session_id": scope.get("session_id"),
                "event_id": scope.get("event_id"),
                "handler_name": scope.get("handler_name"),
                "sql": sql if isinstance(sql, str) else str(sql),
                "params": list(params) if params is not None else None,
                "many": bool(many),
                "duration_ms": +round(duration_ms, 3),
                "stack_top": _short_stack_top(),
            }
        )


@contextmanager
def capture_for_event(
    session_id: Optional[str] = None,
    event_id: Optional[str] = None,
    handler_name: Optional[str] = None,
) -> Iterator[None]:
    """Install the wrapper for the duration of a handler invocation.

    Nested scopes are supported — the outermost scope's tags stick until
    it exits. (Most handlers don't nest; the protection is defensive.)
    """
    # Lazy import so djust.observability can be used without Django.
    try:
        from django.db import connection
    except Exception:  # noqa: BLE001
        yield
        return

    prev_scope = getattr(_active, "scope", None)
    _active.scope = {
        "session_id": session_id,
        "event_id": event_id,
        "handler_name": handler_name,
    }
    try:
        with connection.execute_wrapper(_execute_wrapper):
            yield
    finally:
        _active.scope = prev_scope


def get_queries_since(
    since_ms: int = 0,
    session_id: Optional[str] = None,
    handler_name: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return captured queries matching the filters. Newest-last so
    chronological reading works naturally."""
    with _lock:
        entries = list(_buffer)
    filtered = [
        e
        for e in entries
        if e["timestamp_ms"] > since_ms
        and (session_id is None or e["session_id"] == session_id)
        and (handler_name is None or e["handler_name"] == handler_name)
    ]
    return filtered[-limit:]


def get_buffer_size() -> int:
    with _lock:
        return len(_buffer)


def _clear_queries() -> None:
    """Test-only reset."""
    with _lock:
        _buffer.clear()
