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
    from .state_backend import get_backend

    backend = get_backend()
    return backend.cleanup_expired(ttl)


def get_session_stats() -> Dict[str, Any]:
    from .state_backend import get_backend

    backend = get_backend()
    return backend.get_stats()


_jit_serializer_cache: Dict[tuple, tuple] = {}


@lru_cache(maxsize=128)
def _get_model_hash(model_class: type) -> str:
    field_info = []
    for field in sorted(
        model_class._meta.get_fields(), key=lambda f: f.name if hasattr(f, "name") else ""
    ):
        if hasattr(field, "name"):
            field_type = type(field).__name__
            related = ""
            if hasattr(field, "related_model") and field.related_model:
                related = f":{field.related_model.__name__}"
            field_info.append(f"{field.name}:{field_type}{related}")
    method_prefixes = ("get_", "is_", "has_", "can_")
    skip_prefixes = ("get_next_by_", "get_previous_by_")
    for attr_name in sorted(dir(model_class)):
        if attr_name.startswith("_"):
            continue
        if not any(attr_name.startswith(p) for p in method_prefixes):
            continue
        if any(attr_name.startswith(p) for p in skip_prefixes):
            continue
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
    global _jit_serializer_cache
    count = len(_jit_serializer_cache)
    _jit_serializer_cache.clear()
    _get_model_hash.cache_clear()
    if count > 0:
        logger.info(f"[JIT] Cleared {count} cached serializers")
    return count


def _setup_autoreload_cache_clear():
    try:
        from django.conf import settings

        if not settings.DEBUG:
            return
        from django.utils.autoreload import file_changed

        def clear_cache_on_file_change(sender, file_path, **kwargs):
            if file_path.suffix == ".py":
                count = clear_jit_cache()
                if count > 0:
                    logger.debug(
                        f"[JIT] Cache cleared ({count} entries) due to file change: {file_path.name}"
                    )

        file_changed.connect(clear_cache_on_file_change, weak=False)
        logger.debug("[JIT] Registered file_changed cache clear hook")
    except Exception:
        pass


_setup_autoreload_cache_clear()


class Stream:
    def __init__(self, name: str, dom_id_fn: Callable[[Any], str]):
        self.name = name
        self.dom_id_fn = dom_id_fn
        self.items: list = []
        self._deleted_ids: set = set()

    def insert(self, item: Any, at: int = -1) -> None:
        if at == 0:
            self.items.insert(0, item)
        else:
            self.items.append(item)

    def delete(self, item_or_id: Any) -> None:
        if hasattr(item_or_id, "id"):
            item_id = item_or_id.id
        elif hasattr(item_or_id, "pk"):
            item_id = item_or_id.pk
        else:
            item_id = item_or_id
        self._deleted_ids.add(item_id)
        self.items = [
            item
            for item in self.items
            if getattr(item, "id", getattr(item, "pk", id(item))) != item_id
        ]

    def clear(self) -> None:
        self.items.clear()
        self._deleted_ids.clear()

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)
