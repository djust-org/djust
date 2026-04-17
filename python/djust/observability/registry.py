"""
Session → LiveView instance registry.

Threadsafe. Uses a WeakValueDictionary so entries evaporate when the
view instance is garbage-collected — prevents the registry from
growing unboundedly if consumers forget to call unregister_view().
"""

from __future__ import annotations

import threading
import weakref
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from djust.live_view import LiveView


_registry: "weakref.WeakValueDictionary[str, LiveView]" = weakref.WeakValueDictionary()
_lock = threading.Lock()


def register_view(session_id: str, view: "LiveView") -> None:
    """Add a session→view mapping. Called by LiveViewConsumer after mount."""
    with _lock:
        _registry[session_id] = view


def unregister_view(session_id: str) -> None:
    """Remove a session from the registry. Called on disconnect."""
    with _lock:
        _registry.pop(session_id, None)


def get_view_for_session(session_id: str) -> Optional["LiveView"]:
    """Return the view bound to session_id, or None if not registered."""
    with _lock:
        return _registry.get(session_id)


def get_registered_session_count() -> int:
    """Number of sessions currently in the registry. Test + debug helper."""
    with _lock:
        return len(_registry)


def _clear_registry() -> None:
    """Test-only: wipe the registry. Not exported from __init__."""
    with _lock:
        _registry.clear()
