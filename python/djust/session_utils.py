"""
Session management utilities, JIT serializer cache, and Stream class.

Extracted from live_view.py for modularity.
"""

import hashlib
import logging
from collections.abc import Iterator, Mapping
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
        # ``model_class`` is a Django ``Model`` subclass; ``_meta`` is the
        # model options object Django stamps on every model (not on plain
        # ``type``, which is why mypy can't see it without django-stubs).
        model_class._meta.get_fields(),  # type: ignore[attr-defined]
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
        logger.info("[JIT] Cleared %s cached serializers", count)
    return count


# Auto-clear cache on Django's autoreload in development
def _setup_autoreload_cache_clear() -> None:
    """Register a callback to clear JIT cache when Python files change."""
    try:
        from django.conf import settings

        if not settings.DEBUG:
            return

        from django.utils.autoreload import file_changed

        def clear_cache_on_file_change(sender: Any, file_path: Any, **kwargs: Any) -> None:
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


_NO_FACTORY_KEY = object()
"""Sentinel: this delete cannot be matched by the custom factory (#2129)."""


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

    @staticmethod
    def _identity(item: Any) -> Any:
        """Identity of a stream item, for deletion matching.

        Handles mappings as well as objects (#2116). ``getattr`` reads an
        ATTRIBUTE, so a dict item like ``{"id": 1}`` has no ``.id`` and used to
        fall through to ``id(item)`` — the CPython object address — which never
        matched a caller's id. Dict items were silently undeletable.

        Truthiness is NOT the test — ``{"id": 0}`` resolves to ``0``, not to
        the address. But ``None`` means "no identity yet" (an unsaved row), so
        it falls through to the address: treating it as a value would give
        every unsaved item the SAME identity, colliding their dom_ids. That
        regression is why this is ``is not None`` rather than ``in item``.
        """
        if isinstance(item, Mapping):
            for key in ("id", "pk"):
                if key in item and item[key] is not None:
                    return item[key]
            return id(item)
        for attr in ("id", "pk"):
            value = getattr(item, attr, None)
            if value is not None:
                return value
        return id(item)

    @staticmethod
    def resolve_id(item_or_id: Any) -> Any:
        """Identity of a delete/lookup ARGUMENT, which may be an item or a bare id.

        Distinct from :meth:`_identity`, which is for values known to be
        ITEMS. The difference is the fallback: an unrecognized item resolves
        to its object address (matching only itself), whereas an unrecognized
        ARGUMENT is the id the caller passed and must be used verbatim.

        Collapsing the two is a real bug — ``_identity(0)`` returns the
        address of the int ``0``, not ``0`` — so every caller that accepts
        "item or id" must use THIS one (#2116).
        """
        if Stream._looks_like_item(item_or_id):
            # An ITEM resolves exactly the way :meth:`_identity` resolves one,
            # so the two cannot disagree (#2129). They used to: this branch
            # returned ``.id`` whenever the ATTRIBUTE existed, without
            # ``_identity``'s ``is not None`` discipline, so an object with
            # ``id=None, pk=5`` resolved to ``None`` here and to ``5`` there —
            # and `delete()` compares one against the other, so the item was
            # never removed. Same for an unsaved row (both None): ``None`` here,
            # the object address there.
            return Stream._identity(item_or_id)
        # Not an item — the caller passed the id itself.
        return item_or_id

    @staticmethod
    def default_dom_id(item: Any) -> Any:
        """The dom-id factory used when the app does not supply ``dom_id=``.

        Lives here, not as a closure inside ``StreamsMixin.stream``, so that
        :meth:`dom_id_for` can tell a default factory from a custom one by
        identity — and so there is exactly one definition of it (#1646).
        """
        return Stream._identity(item)

    @staticmethod
    def _looks_like_item(item_or_id: Any) -> bool:
        """Whether an "item or id" argument is an ITEM.

        The same discrimination :meth:`resolve_id` makes, named so callers can
        branch on it: a mapping is always an item, an object carrying ``id``
        or ``pk`` is an item, and anything else is the bare id itself.
        """
        return (
            isinstance(item_or_id, Mapping)
            or hasattr(item_or_id, "id")
            or hasattr(item_or_id, "pk")
        )

    def dom_id_for(self, item_or_id: Any, *, allow_factory_fallback: bool = False) -> str:
        """THE dom id for a stream op — the single place it is computed (#2121).

        Insert and delete MUST agree on this string, or nothing can match the
        two ops up. They used to compute it in three separate places:
        ``stream()``'s insert loop and ``stream_insert()`` both called the
        stream's factory, while ``stream_delete()`` called :meth:`resolve_id`
        and ignored the factory entirely. A stream created with
        ``dom_id=lambda m: m["slug"]`` therefore inserted ``rows-hello-world``
        and deleted ``rows-1``.

        Scope note: ``StreamsMixin``'s ops are not delivered to a transport
        today — ``_get_stream_operations()`` has no callers, and the client's
        ``17-streaming.js`` speaks ``StreamingMixin``'s separate
        ``{op, target, html}`` protocol. So this fixes an internal contract
        (the op-dict shape ``LiveViewTestClient`` reads, and correctness for
        whenever the ops ARE wired), not a live on-screen symptom.

        A **bare id** can never produce the custom dom id — the framework
        cannot invert an arbitrary callable — so it warns and falls back to
        :meth:`resolve_id`.

        ``allow_factory_fallback`` is the DELETE path's concession, and it is
        deliberately asymmetric. `stream_delete`'s parameter is named
        ``item_or_id``, so a partial argument (``{"id": pk}`` right after a DB
        delete) is in contract; the dom id is unrecoverable either way, and
        raising would convert a cosmetic mismatch into a 500 inside an event
        handler. INSERT gets no such concession: the caller is handing over the
        item that DEFINES the row, so a factory that cannot process it is a
        programming error — a typo'd key would otherwise make ``dom_id=``
        silently do nothing, and a factory that raises on only SOME items would
        leave one stream holding ids from two different resolutions, which is
        the exact disagreement this method exists to prevent.
        """
        if self._looks_like_item(item_or_id):
            # ONE expression for the id, with the strict/lenient choice made in
            # the handler. Writing it twice — once per branch — would put this
            # module's own failure class inside the chokepoint meant to retire
            # it, and the structural pin only scans streams.py so it would not
            # catch the drift.
            try:
                return f"{self.name}-{self.dom_id_fn(item_or_id)}"
            except Exception:
                if not allow_factory_fallback:
                    raise
                # Never swallowed — logged with the traceback, then handled.
                logger.warning(
                    "Stream %r custom dom_id= factory raised on %r; falling back to "
                    "the default id resolution, which will NOT match the row that "
                    "was inserted. The argument must be an item the factory accepts.",
                    self.name,
                    item_or_id,
                    exc_info=True,
                )
                return f"{self.name}-{Stream.resolve_id(item_or_id)}"

        if self.dom_id_fn is not Stream.default_dom_id:
            logger.warning(
                "Stream %r has a custom dom_id= factory but was given the bare id %r. "
                "The factory needs the item to compute its dom id, so this op will "
                "use the id verbatim and will NOT match the row that was inserted. "
                "Pass the item itself instead.",
                self.name,
                item_or_id,
            )
        return f"{self.name}-{Stream.resolve_id(item_or_id)}"

    def delete(self, item_or_id: Any) -> None:
        """Mark item for deletion.

        Accepts either the item or its bare id, per the parameter name.

        Identity follows whatever defines a ROW for this stream (#2129):

        - **Default factory.** An id-less MAPPING resolves to its object
          address, so a look-alike dict does not match — there is nothing to
          identify it BY. An object with no ``id``/``pk`` attribute at all is
          treated as the id ITSELF (the caller passed a bare id), so passing
          one as an item does not delete. Both are intended.
        - **Custom factory.** The factory is what supplies the missing
          identity, so a look-alike DOES match: identifying id-less rows is
          the canonical reason to supply one.

        A bare id keeps working against a custom-factory stream — the factory
        cannot be applied to an id, so matching is on EITHER identity rather
        than switching wholesale to the factory.
        """
        item_id = self.resolve_id(item_or_id)

        try:
            self._deleted_ids.add(item_id)
        except TypeError:
            # Unhashable identity (an id-less dict resolves to its hashable
            # address, but a user-supplied unhashable id does not). Removal
            # from ``items`` below still works, which is what callers observe.
            #
            # NOTE: ``_deleted_ids`` currently has NO readers anywhere in the
            # codebase — it is written here and cleared in clear(), nothing
            # else. So skipping an entry cannot break a consumer today. Do not
            # read this as "the tombstone is optional"; if a client-diff
            # consumer is ever added, this branch needs revisiting.
            logger.debug("Stream %s: unhashable delete id, skipping tombstone", self.name)

        # Remove from items. Identity here must mean whatever defines a ROW for
        # THIS stream — otherwise the emitted op names a dom_id the client can
        # match while the item survives on the server (#2129).
        #
        # Matching on EITHER identity rather than switching wholesale to the
        # factory: a custom factory that reads content (``lambda m: m["slug"]``)
        # is the only thing that can identify id-less rows, but a caller may
        # still pass a BARE ID to a custom-factory stream, and the factory
        # cannot be applied to that. Switching outright would have made those
        # deletes stop working — a regression traded for a fix.
        target_key = self._factory_key_for_argument(item_or_id)
        self.items = [
            item for item in self.items if not self._is_delete_target(item, item_id, target_key)
        ]

    def _factory_key_for_argument(self, item_or_id: Any) -> Any:
        """The custom factory's key for a delete ARGUMENT, or ``_NO_FACTORY_KEY``.

        Computed ONCE per delete rather than per item — it does not depend on
        the item being scanned, and re-deriving it inside the comprehension
        made one delete on an n-item stream call the user's factory 2n+1 times.

        Returns the sentinel — meaning "compare by identity only" — when the
        stream has no custom factory, when the argument is a bare id (the
        factory cannot be applied to an id, so comparing its output against one
        would be comparing different things), or when the factory rejects the
        argument.
        """
        if self.dom_id_fn is Stream.default_dom_id:
            return _NO_FACTORY_KEY
        if not self._looks_like_item(item_or_id):
            return _NO_FACTORY_KEY
        try:
            return self.dom_id_fn(item_or_id)
        except Exception:
            # Already reported by dom_id_for on the op path; a second warning
            # here would double-report the same argument.
            logger.debug(
                "Stream %s: custom dom_id factory rejected the delete argument; "
                "falling back to identity matching",
                self.name,
            )
            return _NO_FACTORY_KEY

    def _is_delete_target(self, item: Any, item_id: Any, target_key: Any) -> bool:
        """Whether ``item`` is the row the delete argument refers to."""
        if self._identity(item) == item_id:
            return True
        if target_key is _NO_FACTORY_KEY:
            return False
        try:
            key = self.dom_id_fn(item)
        except Exception:
            # Scanning, not resolving the caller's argument: an item this
            # factory cannot process simply is not the target. Deliberately
            # silent — warning here emits one record per unmatched row, so a
            # single delete on a stream with many such rows produced thousands
            # of traceback-bearing warnings inside an event handler.
            return False
        # Compare the factory's RAW output, with types, NOT the formatted
        # dom_id string. Comparing ``f"{name}-{value}"`` collapsed values whose
        # ``str()`` happens to match — so deleting the row keyed ``5`` also
        # destroyed the row keyed ``"5"``, and a UUID destroyed its own string
        # form. Deletion is irreversible and the loss was silent; under-matching
        # is the safe direction here.
        return type(key) is type(target_key) and key == target_key

    def clear(self) -> None:
        """Clear all items."""
        self.items.clear()
        self._deleted_ids.clear()

    def __iter__(self) -> Iterator[Any]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)
