"""
Ring-buffered log handler for observability.

Attached to the `djust` logger (and `django` at WARNING+ to keep
volume sane) in AppConfig.ready(). The MCP reads the buffer via
/_djust/observability/log/.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Dict, List

_MAX_ENTRIES = 500

_buffer: "deque[Dict[str, Any]]" = deque(maxlen=_MAX_ENTRIES)
_lock = threading.Lock()


class ObservabilityLogHandler(logging.Handler):
    """Append every emitted record to the ring buffer.

    Uses the record's own ``created`` timestamp (ms since epoch) so
    ``since_ms`` filtering by the endpoint is consistent with
    ``time.time() * 1000`` used elsewhere in djust.observability.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # record.getMessage() substitutes args into msg exactly once.
            # Any formatter on the handler chain doesn't apply here — we
            # want the raw message for JSON transport.
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = record.msg if isinstance(record.msg, str) else repr(record.msg)

        entry = {
            "timestamp_ms": int(record.created * 1000),
            "level": record.levelname,
            "logger_name": record.name,
            "message": message,
            "pathname": record.pathname,
            "lineno": record.lineno,
        }
        # Attach exception info when present.
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            entry["exc_message"] = str(record.exc_info[1]) if record.exc_info[1] else None

        with _lock:
            _buffer.append(entry)


# Level name → int for server-side filtering. Standard logging levels.
_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def get_recent_logs(
    since_ms: int = 0,
    level: str = "INFO",
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return entries after ``since_ms`` at ``level`` or higher."""
    min_level = _LEVEL_NAMES.get(level.upper(), logging.INFO)
    level_name_to_int = {k: v for k, v in _LEVEL_NAMES.items()}

    with _lock:
        entries = [
            e
            for e in _buffer
            if e["timestamp_ms"] > since_ms
            and level_name_to_int.get(e["level"], logging.INFO) >= min_level
        ]
    # Newest last — natural chronological order is what tail-of-log expects.
    return entries[-limit:]


def get_buffer_size() -> int:
    with _lock:
        return len(_buffer)


def _clear_logs() -> None:
    """Test-only reset."""
    with _lock:
        _buffer.clear()


# Keep a module-level reference to the installed handler so tests (and
# apps.py on reload) can detect whether install has already happened.
_installed_handler: "ObservabilityLogHandler | None" = None


def install_handler() -> None:
    """Idempotently attach the handler to the `djust` and `django` loggers.

    Called from AppConfig.ready(). Safe to call multiple times — a
    re-import during hot reload won't double-install.
    """
    global _installed_handler
    if _installed_handler is not None:
        return

    handler = ObservabilityLogHandler(level=logging.DEBUG)
    _installed_handler = handler

    # djust logs: full fidelity (DEBUG+)
    djust_logger = logging.getLogger("djust")
    djust_logger.addHandler(handler)

    # django logs: WARNING+ only (request noise isn't useful in the
    # buffer and would evict signal fast)
    django_logger = logging.getLogger("django")
    django_logger.addHandler(handler)
