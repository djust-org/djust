"""
Session management utilities, JIT serializer cache, and Stream class.

Extracted from live_view.py for modularity.
"""

import hashlib
import logging
from functools import lru_cache
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("djust")


# Default TTL for sessions (1 hour)
DEFAULT_SESSION_TTL = 3600


def cleanup_expired_sessions(ttl: Optional[int] = None) -> int:
    """
    Clean up expired LiveView sessions from state backend.

    Args:
        ttl: Time to live in seconds. Defaults to DEFAULT_SESSION_TTL.

    Returns:
        Number of sessions cleaned up
    """
    from .state_backend import get_backend

    backend = get_backend()
    return backend.cleanup_expired(ttl)


def get_session_stats() -> Dict[str, Any]:
    """
    Get statistics about cached LiveView sessions from state backend.

    Returns:
        Dictionary with cache statistics
    """
    from .state_backend import get_backend

    backend = get_backend()
    return backend.get_stats()


# Global cache for compiled JIT serializers
# Key: (template_hash, variable_name, model_hash) -> (serializer_func, optimization)
# model_hash ensures cache invalidation when model fields change
_jit_serializer_cache: Dict[tuple, tuple] = {}


@lru_cache(maxsize=128)
def _get_model_hash(model_class: type) -> str:
    """
    Generate a hash of a model's field structure and serializable methods.

    This hash changes when the model's fields or get_*/is_*/has_*/can_* methods
    are modified, ensuring the JIT serializer cache is invalidated.

    Results are cached for performance since model structure rarely changes
    during a request. Cache is cleared when clear_jit_cache() is called.

    Args:
        model_class: The Django model class to hash

    Returns:
        8-character hexadecimal hash string
    """
    # Build a string representation of the model's field structure
    field_info = []
    for field in sorted(
        model_class._meta.get_fields(),
        key=lambda f: f.name if hasattr(f, "name") else "",
    ):
        if hasattr(field, "name"):
            field_type = type(field).__name__
            # Include related model name for FK/O2O fields
            related = ""
            if hasattr(field, "related_model") and field.related_model:
                related = f":{field.related_model.__name__}"
            field_info.append(f"{field.name}:{field_type}{related}")

    # Include serializable methods (get_*, is_*, has_*, can_*)
    # These are included in JIT serialization, so changes should invalidate cache
    method_prefixes = ("get_", "is_", "has_", "can_")
    skip_prefixes = ("get_next_by_", "get_previous_by_")
    for attr_name in sorted(dir(model_class)):
        if attr_name.startswith("_"):
            continue
        if not any(attr_name.startswith(p) for p in method_prefixes):
            continue
        if any(attr_name.startswith(p) for p in skip_prefixes):
            continue
        # Only include methods explicitly defined on the model (not inherited from Model)
        for cls in model_class.__mro__:
            if cls.__name__ == "Model":
                break
            if attr_name in cls.__dict__:
                attr = getattr(model_class, attr_name, None)
                if callable(attr):
                    field_info.append(f"method:{attr_name}")
                break

    structure = f"{model_class.__name__}|{'|'.join(field_info)}"
    return hashlib.sha256(structure.encode()).hexdigest()[:8]


def clear_jit_cache() -> int:
    """
    Clear the JIT serializer cache.

    Call this in development when model definitions change but the server
    hasn't restarted. This is automatically called when Django's autoreloader
    detects file changes (if configured).

    Returns:
        Number of cache entries cleared
    """
    global _jit_serializer_cache
    count = len(_jit_serializer_cache)
    _jit_serializer_cache.clear()
    _get_model_hash.cache_clear()  # Also clear the model hash cache
    if count > 0:
        logger.info(f"[JIT] Cleared {count} cached serializers")
    return count


# Auto-clear cache on Django's autoreload in development
def _setup_autoreload_cache_clear():
    """Register a callback to clear JIT cache when Python files change."""
    try:
        from django.conf import settings

        if not settings.DEBUG:
            return

        from django.utils.autoreload import file_changed

        def clear_cache_on_file_change(sender, file_path, **kwargs):
            # Only clear cache when Python files change (models, views, etc.)
            if file_path.suffix == ".py":
                count = clear_jit_cache()
                if count > 0:
                    logger.debug(
                        f"[JIT] Cache cleared ({count} entries) due to file change: {file_path.name}"
                    )

        file_changed.connect(clear_cache_on_file_change, weak=False)
        logger.debug("[JIT] Registered file_changed cache clear hook")
    except Exception:
        # Autoreload signal not available (e.g., older Django or production)
        pass


# Try to set up autoreload hook (fails silently if not applicable)
_setup_autoreload_cache_clear()


class Stream:
    """
    A memory-efficient collection for LiveView.

    Streams automatically track insertions and deletions, allowing the client
    to efficiently update the DOM without re-rendering the entire list.

    Items are cleared from server memory after each render, but the client
    preserves the DOM elements.

    Usage:
        # In your LiveView
        def mount(self, request, **kwargs):
            self.stream('messages', Message.objects.all()[:50])

        def handle_new_message(self, content):
            msg = Message.objects.create(content=content)
            self.stream_insert('messages', msg)

        # In template:
        <ul dj-stream="messages">
            {% for msg in streams.messages %}
                <li id="messages-{{ msg.id }}">{{ msg.content }}</li>
            {% endfor %}
        </ul>
    """

    def __init__(self, name: str, dom_id_fn: Callable[[Any], str]):
        self.name = name
        self.dom_id_fn = dom_id_fn
        self.items: list = []
        self._deleted_ids: set = set()

    def insert(self, item: Any, at: int = -1) -> None:
        """Insert item at position (-1 = end, 0 = beginning)."""
        if at == 0:
            self.items.insert(0, item)
        else:
            self.items.append(item)

    def delete(self, item_or_id: Any) -> None:
        """Mark item for deletion."""
        if hasattr(item_or_id, "id"):
            item_id = item_or_id.id
        elif hasattr(item_or_id, "pk"):
            item_id = item_or_id.pk
        else:
            item_id = item_or_id

        self._deleted_ids.add(item_id)
        # Remove from items list if present
        self.items = [
            item
            for item in self.items
            if getattr(item, "id", getattr(item, "pk", id(item))) != item_id
        ]

    def clear(self) -> None:
        """Clear all items."""
        self.items.clear()
        self._deleted_ids.clear()

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)
