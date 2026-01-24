"""
LiveView base class and decorator for reactive Django views
"""

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, date, time
from decimal import Decimal
from urllib.parse import parse_qs, urlencode
from uuid import UUID
from typing import Any, Dict, Optional, Callable
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.db import models
from .validation import validate_handler_params

# Try to use orjson for faster JSON operations (2-3x faster than stdlib)
import importlib.util

HAS_ORJSON = importlib.util.find_spec("orjson") is not None

# Configure logger
logger = logging.getLogger(__name__)

try:
    from ._rust import (
        RustLiveView,
        create_session_actor,
        SessionActorHandle,
        extract_template_variables,
    )
except ImportError:
    RustLiveView = None
    create_session_actor = None
    SessionActorHandle = None
    extract_template_variables = None

# JIT optimization imports
try:
    from .optimization.query_optimizer import analyze_queryset_optimization, optimize_queryset
    from .optimization.codegen import generate_serializer_code, compile_serializer

    JIT_AVAILABLE = True
except ImportError:
    JIT_AVAILABLE = False

# Module-level cache for context processors, keyed by settings object id
# This invalidates when Django settings are overridden (e.g., in tests)
_context_processors_cache: Dict[int, list] = {}


class DjangoJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles common Django and Python types.

    Automatically converts:
    - datetime/date/time → ISO format strings
    - UUID → string
    - Decimal → float
    - Component/LiveComponent → rendered HTML string
    - Django models → dict with id and __str__
    - QuerySets → list
    """

    # Class variable to track recursion depth
    _depth = 0

    @staticmethod
    def _get_max_depth():
        """Get max depth from config (lazy load to avoid circular import)"""
        from .config import config

        return config.get("serialization_max_depth", 3)

    def default(self, obj):
        # Track recursion depth to prevent infinite loops
        DjangoJSONEncoder._depth += 1
        try:
            return self._default_impl(obj)
        finally:
            DjangoJSONEncoder._depth -= 1

    def _default_impl(self, obj):
        # Handle Component and LiveComponent instances (render to HTML)
        # Import from both old and new locations for compatibility
        from .component import Component, LiveComponent
        from .components.base import Component as BaseComponent, LiveComponent as BaseLiveComponent

        if isinstance(obj, (Component, LiveComponent, BaseComponent, BaseLiveComponent)):
            return str(obj)  # Calls __str__() which calls render()

        # Handle datetime types
        if isinstance(obj, (datetime, date, time)):
            return obj.isoformat()

        # Handle UUID
        if isinstance(obj, UUID):
            return str(obj)

        # Handle Decimal
        if isinstance(obj, Decimal):
            return float(obj)

        # Handle Django model instances
        if isinstance(obj, models.Model):
            return self._serialize_model_safely(obj)

        # Handle QuerySets
        if hasattr(obj, "model") and hasattr(obj, "__iter__"):
            # This is likely a QuerySet
            return list(obj)

        return super().default(obj)

    def _serialize_model_safely(self, obj):
        """Cache-aware model serialization that prevents N+1 queries.

        Only accesses related objects if they were prefetched via
        select_related() or prefetch_related(). Otherwise, only includes
        the FK ID without triggering a database query.
        """
        result = {
            "id": str(obj.pk) if obj.pk else None,
            "__str__": str(obj),
            "__model__": obj.__class__.__name__,
        }

        for field in obj._meta.get_fields():
            if not hasattr(field, "name"):
                continue

            field_name = field.name

            # Skip all reverse relations (ManyToOneRel, OneToOneRel, ManyToManyRel)
            # and many-to-many fields (forward or backward)
            # concrete=False means it's a reverse relation, not a forward FK/O2O
            if field.is_relation:
                is_concrete = getattr(field, "concrete", True)
                is_m2m = getattr(field, "many_to_many", False)
                if not is_concrete or is_m2m:
                    continue

            # Handle ForeignKey/OneToOne (forward relations only now)
            if field.is_relation and hasattr(field, "related_model"):
                if self._is_relation_prefetched(obj, field_name):
                    # Relation is cached, safe to access without N+1
                    try:
                        related = getattr(obj, field_name, None)
                    except Exception:
                        # Handle deferred fields or descriptor errors gracefully
                        related = None

                    if related and DjangoJSONEncoder._depth < self._get_max_depth():
                        result[field_name] = self._serialize_model_safely(related)
                    elif related:
                        result[field_name] = {
                            "id": str(related.pk) if related.pk else None,
                            "__str__": str(related),
                        }
                    else:
                        result[field_name] = None
                else:
                    # Include FK ID without fetching the related object (no N+1!)
                    fk_id = getattr(obj, f"{field_name}_id", None)
                    if fk_id is not None:
                        result[f"{field_name}_id"] = fk_id
            else:
                # Regular field - safe to access
                try:
                    result[field_name] = getattr(obj, field_name, None)
                except (AttributeError, ValueError):
                    # Skip fields that can't be accessed (deferred, property errors, etc.)
                    pass

        # Only include explicitly defined get_* methods (skip auto-generated ones)
        self._add_safe_model_methods(obj, result)
        return result

    def _is_relation_prefetched(self, obj, field_name):
        """Check if a relation was loaded via select_related/prefetch_related.

        This prevents N+1 queries by only accessing relations that are
        already cached in memory.
        """
        # Check Django's fields_cache (populated by select_related)
        state = getattr(obj, "_state", None)
        if state:
            fields_cache = getattr(state, "fields_cache", {})
            if field_name in fields_cache:
                return True

        # Check prefetch cache (populated by prefetch_related)
        prefetch_cache = getattr(obj, "_prefetched_objects_cache", {})
        if field_name in prefetch_cache:
            return True

        return False

    def _add_safe_model_methods(self, obj, result):
        """Add only explicitly defined model methods, skip auto-generated ones.

        Django auto-generates methods like get_next_by_created_at(),
        get_previous_by_updated_at() which execute expensive cursor queries.
        We only want explicitly defined methods like get_full_name().
        """
        # Skip Django's auto-generated methods that cause N+1 queries
        SKIP_PREFIXES = ("get_next_by_", "get_previous_by_")

        # Known problematic methods
        SKIP_METHODS = {
            "get_all_permissions",
            "get_user_permissions",
            "get_group_permissions",
            "get_session_auth_hash",
            "get_deferred_fields",
        }

        model_class = obj.__class__

        for attr_name in dir(obj):
            if attr_name.startswith("_") or attr_name in result:
                continue
            if not attr_name.startswith("get_"):
                continue
            if any(attr_name.startswith(p) for p in SKIP_PREFIXES):
                continue
            if attr_name in SKIP_METHODS:
                continue

            # Only include methods explicitly defined on the model class
            if not self._is_method_explicit(model_class, attr_name):
                continue

            try:
                attr = getattr(obj, attr_name)
                if callable(attr):
                    value = attr()
                    if isinstance(value, (str, int, float, bool, type(None))):
                        result[attr_name] = value
            except Exception:
                # Silently skip methods that fail - they may require arguments,
                # access missing related objects, or have other runtime errors.
                # This is expected behavior for introspection-based serialization.
                pass

    def _is_method_explicit(self, model_class, method_name):
        """Check if method is explicitly defined, not auto-generated by Django.

        Auto-generated methods like get_next_by_* are not in the class __dict__
        of any user-defined model class, only in Django's base Model class.
        """
        for cls in model_class.__mro__:
            if cls is models.Model:
                break
            if method_name in cls.__dict__:
                return True
        return False


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


def _get_model_hash(model_class: type) -> str:
    """
    Generate a hash of a model's field structure and serializable methods.

    This hash changes when the model's fields or get_*/is_*/has_*/can_* methods
    are modified, ensuring the JIT serializer cache is invalidated.

    Args:
        model_class: The Django model class to hash

    Returns:
        8-character hexadecimal hash string
    """
    # Build a string representation of the model's field structure
    field_info = []
    for field in sorted(model_class._meta.get_fields(), key=lambda f: f.name if hasattr(f, 'name') else ''):
        if hasattr(field, 'name'):
            field_type = type(field).__name__
            # Include related model name for FK/O2O fields
            related = ''
            if hasattr(field, 'related_model') and field.related_model:
                related = f':{field.related_model.__name__}'
            field_info.append(f"{field.name}:{field_type}{related}")

    # Include serializable methods (get_*, is_*, has_*, can_*)
    # These are included in JIT serialization, so changes should invalidate cache
    method_prefixes = ('get_', 'is_', 'has_', 'can_')
    skip_prefixes = ('get_next_by_', 'get_previous_by_')
    for attr_name in sorted(dir(model_class)):
        if attr_name.startswith('_'):
            continue
        if not any(attr_name.startswith(p) for p in method_prefixes):
            continue
        if any(attr_name.startswith(p) for p in skip_prefixes):
            continue
        # Only include methods explicitly defined on the model (not inherited from Model)
        for cls in model_class.__mro__:
            if cls.__name__ == 'Model':
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
    logger.debug(f"[JIT] Cleared {count} cached serializers")
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
            if file_path.suffix == '.py':
                count = clear_jit_cache()
                if count > 0:
                    logger.debug(f"[JIT] Cache cleared ({count} entries) due to file change: {file_path.name}")

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


class LiveView(View):
    """
    Base class for reactive LiveView components.

    Usage:
        class CounterView(LiveView):
            template_name = 'counter.html'
            use_actors = True  # Enable actor-based state management (optional)

            def mount(self, request, **kwargs):
                self.count = 0

            def increment(self):
                self.count += 1

            def decrement(self):
                self.count -= 1

    Memory Optimization with temporary_assigns:
        For views with large collections (chat messages, feed items, etc.),
        use temporary_assigns to clear data from server memory after each render.

        class ChatView(LiveView):
            template_name = 'chat.html'
            temporary_assigns = {'messages': []}  # Clear after each render

            def mount(self, request, **kwargs):
                self.messages = Message.objects.all()[:50]

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.messages = [msg]  # Only new messages sent to client

        IMPORTANT: When using temporary_assigns, use dj-update="append" in your
        template to tell the client to append new items instead of replacing:

            <ul dj-update="append" id="messages">
                {% for msg in messages %}
                    <li id="msg-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>

    Streams API (recommended for collections):
        For a more ergonomic API, use streams instead of temporary_assigns:

        class ChatView(LiveView):
            template_name = 'chat.html'

            def mount(self, request, **kwargs):
                self.stream('messages', Message.objects.all()[:50])

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.stream_insert('messages', msg)

        Template:
            <ul dj-stream="messages">
                {% for msg in streams.messages %}
                    <li id="messages-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>
    """

    template_name: Optional[str] = None
    template: Optional[str] = None
    use_actors: bool = False  # Enable Tokio actor-based state management (Phase 5+)

    # Memory optimization: assigns to clear after each render
    # Format: {'assign_name': default_value, ...}
    # Example: {'messages': [], 'feed_items': [], 'notifications': []}
    temporary_assigns: Dict[str, Any] = {}

    # ============================================================================
    # INITIALIZATION & SETUP
    # ============================================================================

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rust_view: Optional[RustLiveView] = None
        self._actor_handle: Optional[SessionActorHandle] = None
        self._session_id: Optional[str] = None
        self._cache_key: Optional[str] = None
        self._handler_metadata: Optional[dict] = None  # Cache for decorator metadata
        self._components: Dict[str, Any] = {}  # Registry of child components by ID
        self._temporary_assigns_initialized: bool = False  # Track if temp assigns are set up
        self._streams: Dict[str, Stream] = {}  # Stream collections
        self._stream_operations: list = []  # Pending stream operations for this render

    # ============================================================================
    # TEMPORARY ASSIGNS - Memory optimization for large collections
    # ============================================================================

    def _reset_temporary_assigns(self) -> None:
        """
        Reset temporary assigns to their default values after rendering.

        Called automatically after each render to free memory for large collections.
        """
        if not self.temporary_assigns:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if hasattr(self, assign_name):
                # Reset to default value (make a copy to avoid sharing state)
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

                logger.debug(
                    f"[LiveView] Reset temporary assign '{assign_name}' to {type(default_value).__name__}"
                )

        # Also reset streams
        self._reset_streams()

    def _initialize_temporary_assigns(self) -> None:
        """Initialize temporary assigns with their default values on first mount."""
        if self._temporary_assigns_initialized:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if not hasattr(self, assign_name):
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

        self._temporary_assigns_initialized = True

    # ============================================================================
    # STREAMS API - Memory-efficient collection management
    # ============================================================================

    def stream(
        self,
        name: str,
        items: Any,
        dom_id: Optional[Callable[[Any], str]] = None,
        at: int = -1,
        reset: bool = False,
    ) -> Stream:
        """
        Initialize or update a stream with items.

        Streams are memory-efficient collections that are automatically cleared
        after each render. The client preserves existing DOM elements.

        Args:
            name: Stream name (used in template as streams.{name})
            items: Iterable of items to add to the stream
            dom_id: Function to generate DOM id from item (default: lambda x: x.id)
            at: Position to insert (-1 = end, 0 = beginning)
            reset: If True, clear existing items first

        Returns:
            Stream object for chaining
        """

        def default_dom_id(x):
            return getattr(x, "id", None) or getattr(x, "pk", None) or id(x)

        if dom_id is None:
            dom_id = default_dom_id

        if name not in self._streams or reset:
            self._streams[name] = Stream(name, dom_id)
            if reset:
                self._stream_operations.append(
                    {
                        "type": "stream_reset",
                        "stream": name,
                    }
                )

        stream_obj = self._streams[name]

        # Convert items to list if needed
        if hasattr(items, "__iter__") and not isinstance(items, (str, bytes)):
            items_list = list(items)
        else:
            items_list = [items] if items is not None else []

        for item in items_list:
            stream_obj.insert(item, at=at)
            self._stream_operations.append(
                {
                    "type": "stream_insert",
                    "stream": name,
                    "dom_id": f"{name}-{dom_id(item)}",
                    "at": at,
                }
            )

        return stream_obj

    def stream_insert(self, name: str, item: Any, at: int = -1) -> None:
        """Insert an item into a stream (-1 = append, 0 = prepend)."""
        if name not in self._streams:
            raise ValueError(f"Stream '{name}' not initialized. Call stream() first.")

        stream_obj = self._streams[name]
        dom_id = stream_obj.dom_id_fn

        stream_obj.insert(item, at=at)
        self._stream_operations.append(
            {
                "type": "stream_insert",
                "stream": name,
                "dom_id": f"{name}-{dom_id(item)}",
                "at": at,
            }
        )

    def stream_delete(self, name: str, item_or_id: Any) -> None:
        """Delete an item from a stream by item or id."""
        if name not in self._streams:
            raise ValueError(f"Stream '{name}' not initialized. Call stream() first.")

        stream_obj = self._streams[name]

        # Get the DOM id
        if hasattr(item_or_id, "id"):
            dom_id_val = f"{name}-{item_or_id.id}"
        elif hasattr(item_or_id, "pk"):
            dom_id_val = f"{name}-{item_or_id.pk}"
        else:
            dom_id_val = f"{name}-{item_or_id}"

        stream_obj.delete(item_or_id)
        self._stream_operations.append(
            {
                "type": "stream_delete",
                "stream": name,
                "dom_id": dom_id_val,
            }
        )

    def stream_reset(self, name: str, items: Any = None) -> None:
        """Reset a stream, clearing all items and optionally adding new ones."""
        if name in self._streams:
            self._streams[name].clear()

        self._stream_operations.append(
            {
                "type": "stream_reset",
                "stream": name,
            }
        )

        if items is not None:
            self.stream(name, items, reset=False)

    def _get_streams_context(self) -> Dict[str, list]:
        """Get streams data for template context."""
        return {name: stream_obj.items for name, stream_obj in self._streams.items()}

    def _get_stream_operations(self) -> list:
        """Get and clear pending stream operations."""
        ops = self._stream_operations.copy()
        self._stream_operations.clear()
        return ops

    def _reset_streams(self) -> None:
        """Reset all streams after render to free memory."""
        for stream_obj in self._streams.values():
            stream_obj.clear()

    def get_template(self) -> str:
        """
        Get the Rust template source for this view.

        Supports template inheritance via {% extends %} and {% block %} tags.
        Templates are resolved using Rust template inheritance for performance.

        For templates with inheritance, extracts only [data-liveview-root] content
        for VDOM tracking to avoid tracking the entire document.
        """
        if self.template:
            return self.template
        elif self.template_name:
            # Load the raw template source
            from django.template import loader
            from django.conf import settings

            template = loader.get_template(self.template_name)
            template_source = template.template.source

            # Check if template uses {% extends %} - if so, resolve inheritance in Rust
            if "{% extends" in template_source or "{%extends" in template_source:
                # Get template directories from Django settings in the EXACT same order Django searches
                # Django's search order:
                # 1. DIRS from each TEMPLATES config (in order)
                # 2. APP_DIRS (if enabled) - searches app templates in app order
                template_dirs = []

                # Step 1: Add DIRS from all TEMPLATES configs
                for template_config in settings.TEMPLATES:
                    if "DIRS" in template_config:
                        template_dirs.extend(template_config["DIRS"])

                # Step 2: Add app template directories (only for DjangoTemplates with APP_DIRS=True)
                for template_config in settings.TEMPLATES:
                    if (
                        template_config["BACKEND"]
                        == "django.template.backends.django.DjangoTemplates"
                    ):
                        if template_config.get("APP_DIRS", False):
                            from django.apps import apps
                            from pathlib import Path

                            for app_config in apps.get_app_configs():
                                templates_dir = Path(app_config.path) / "templates"
                                if templates_dir.exists():
                                    template_dirs.append(str(templates_dir))

                # Convert to strings
                template_dirs_str = [str(d) for d in template_dirs]

                # Get the actual path Django resolved for verification
                django_resolved_path = (
                    template.origin.name
                    if hasattr(template, "origin") and template.origin
                    else None
                )

                # Use Rust template inheritance resolution
                try:
                    from djust._rust import resolve_template_inheritance

                    resolved = resolve_template_inheritance(self.template_name, template_dirs_str)

                    # Verify Rust found the same template as Django
                    # This ensures our template search order matches Django's exactly
                    if django_resolved_path:
                        # Check if any of our template dirs + template_name matches Django's path
                        rust_would_find = None
                        for template_dir in template_dirs_str:
                            candidate = os.path.join(template_dir, self.template_name)
                            if os.path.exists(candidate):
                                rust_would_find = os.path.abspath(candidate)
                                break

                        if (
                            rust_would_find
                            and os.path.abspath(django_resolved_path) != rust_would_find
                        ):
                            print(
                                f"[WARNING] Template resolution mismatch!\n"
                                f"  Django found: {django_resolved_path}\n"
                                f"  Rust found:   {rust_would_find}\n"
                                f"  Template dirs order: {template_dirs_str[:3]}...",
                                file=sys.stderr,
                            )

                    # Store full template for initial GET rendering
                    self._full_template = resolved

                    # For VDOM tracking, extract liveview-root from the RESOLVED template
                    # This ensures Rust VDOM tracks exactly what the client receives
                    # Extract liveview-root div (with wrapper) for VDOM tracking
                    vdom_template = self._extract_liveview_root_with_wrapper(resolved)

                    # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
                    # This ensures Rust VDOM baseline matches client DOM structure
                    vdom_template = self._strip_comments_and_whitespace(vdom_template)

                    print(
                        f"[LiveView] Template inheritance resolved ({len(resolved)} chars), extracted liveview-root for VDOM ({len(vdom_template)} chars)",
                        file=sys.stderr,
                    )
                    return vdom_template

                except Exception as e:
                    # Fallback to raw template if Rust resolution fails
                    print(f"[LiveView] Template inheritance resolution failed: {e}")
                    print("[LiveView] Falling back to raw template source")
                    # Store full template for render_full_template()
                    self._full_template = template_source
                    # Extract liveview-root div (with wrapper) for VDOM tracking
                    extracted = self._extract_liveview_root_with_wrapper(template_source)

                    # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
                    extracted = self._strip_comments_and_whitespace(extracted)

                    print(
                        f"[LiveView] Extracted and stripped liveview-root: {len(extracted)} chars (from {len(template_source)} chars)",
                        file=sys.stderr,
                    )
                    return extracted

            # No template inheritance - store full template and extract liveview-root for VDOM
            # Store full template for render_full_template() to use in GET responses
            self._full_template = template_source

            # Extract liveview-root div (with wrapper) for VDOM tracking
            # This ensures server VDOM and client VDOM track the same structure
            extracted = self._extract_liveview_root_with_wrapper(template_source)

            # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
            # This ensures Rust VDOM baseline matches client DOM structure
            extracted = self._strip_comments_and_whitespace(extracted)

            print(
                f"[LiveView] No inheritance - extracted and stripped liveview-root: {len(extracted)} chars (from {len(template_source)} chars)",
                file=sys.stderr,
            )
            return extracted
        else:
            raise ValueError("Either template_name or template must be set")

    # ============================================================================
    # COMPONENT MANAGEMENT
    # ============================================================================

    def mount(self, request, **kwargs):
        """
        Called when the view is mounted. Override to set initial state.

        Args:
            request: The Django request object
            **kwargs: URL parameters
        """
        pass

    def handle_component_event(self, component_id: str, event: str, data: Dict[str, Any]):
        """
        Handle events sent from child components.

        Override this method to respond to component events sent via send_parent().

        Args:
            component_id: Unique ID of the component sending the event
            event: Event name (e.g., "item_selected", "form_submitted")
            data: Event payload data

        Example:
            def handle_component_event(self, component_id, event, data):
                if event == "user_selected":
                    self.selected_user_id = data['user_id']
                    # Update child component props
                    if hasattr(self, 'user_detail'):
                        self.user_detail.update(user_id=data['user_id'])
                elif event == "item_added":
                    self.items.append(data['item'])
        """
        pass

    def update_component(self, component_id: str, **props):
        """
        Update a child component's props.

        Args:
            component_id: ID of the component to update
            **props: New prop values to pass to component

        Example:
            # Update user detail component with new user
            self.update_component(
                self.user_detail.component_id,
                user_id=selected_id
            )
        """
        from .component import LiveComponent

        component = self._components.get(component_id)
        if component and isinstance(component, LiveComponent):
            component.update(**props)

    def _register_component(self, component):
        """
        Register a child component for event handling.

        Internal method called during mount() to set up component callbacks.

        Args:
            component: LiveComponent instance to register
        """
        from .component import LiveComponent

        if isinstance(component, LiveComponent):
            # Register component by ID
            self._components[component.component_id] = component

            # Set up parent callback for event handling
            def component_callback(event_data):
                self.handle_component_event(
                    event_data["component_id"],
                    event_data["event"],
                    event_data["data"],
                )

            component._set_parent_callback(component_callback)

    # ============================================================================
    # CONTEXT & STATE SYNCHRONIZATION
    # ============================================================================

    def _get_template_content(self) -> Optional[str]:
        """
        Get template source code for JIT variable extraction.

        Returns:
            Template source as string, or None if not available

        Used by JIT auto-serialization to analyze template variable access patterns.
        """
        # Try template_string first (inline templates)
        if hasattr(self, "template") and self.template:
            return self.template

        # Try template_name (file-based templates)
        if hasattr(self, "template_name") and self.template_name:
            try:
                from django.template.loader import get_template

                django_template = get_template(self.template_name)

                # Try to get source from template
                if hasattr(django_template, "template") and hasattr(
                    django_template.template, "source"
                ):
                    return django_template.template.source
                elif hasattr(django_template, "origin") and hasattr(django_template.origin, "name"):
                    # Read from file
                    with open(django_template.origin.name, "r") as f:
                        return f.read()
            except Exception as e:
                logger.debug(f"Could not load template for JIT: {e}")
                return None

        return None

    def _jit_serialize_queryset(self, queryset, template_content: str, variable_name: str):
        """
        Apply JIT auto-serialization to a Django QuerySet.

        Automatically:
        1. Extracts variable access patterns from template (e.g., lease.property.name)
        2. Generates optimized select_related/prefetch_related calls
        3. Compiles custom serializer function
        4. Caches serializer for reuse

        Args:
            queryset: Django QuerySet to serialize
            template_content: Template source code
            variable_name: Variable name in template (e.g., "leases")

        Returns:
            List of serialized dictionaries

        Performance:
        - First call: ~10-50ms (codegen + compilation)
        - Subsequent calls: <1ms (cache hit)
        - Query optimization: 80%+ reduction in database queries

        Loop Variable Support:
        - Paths accessed via loop variables are automatically transferred to the iterable.
          Example: {% for email in emails %}{{ email.sender.name }}{% endfor %}
          will correctly add select_related('sender') to the emails QuerySet.
        """
        if not JIT_AVAILABLE or not extract_template_variables:
            # Fallback to default DjangoJSONEncoder
            return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

        try:
            # Extract variable paths from template
            variable_paths_map = extract_template_variables(template_content)
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var:
                # No template access detected, use default serialization
                print(
                    f"[JIT] No paths found for '{variable_name}', using DjangoJSONEncoder fallback",
                    file=sys.stderr,
                )
                return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

            # Generate cache key (includes model hash for invalidation on model changes)
            model_class = queryset.model
            template_hash = hashlib.sha256(template_content.encode()).hexdigest()[:8]
            model_hash = _get_model_hash(model_class)
            cache_key = (template_hash, variable_name, model_hash)

            # Check cache
            if cache_key in _jit_serializer_cache:
                paths_for_var, optimization = _jit_serializer_cache[cache_key]
                print(
                    f"[JIT] Cache HIT for '{variable_name}' - using cached paths: {paths_for_var}",
                    file=sys.stderr,
                )
            else:
                # Generate and compile serializer
                optimization = analyze_queryset_optimization(model_class, paths_for_var)

                print(
                    f"[JIT] Cache MISS for '{variable_name}' ({model_class.__name__}) - generating serializer for paths: {paths_for_var}",
                    file=sys.stderr,
                )
                if optimization:
                    print(
                        f"[JIT] Query optimization: select_related={sorted(optimization.select_related)}, prefetch_related={sorted(optimization.prefetch_related)}",
                        file=sys.stderr,
                    )

                # Cache paths for Rust serializer
                _jit_serializer_cache[cache_key] = (paths_for_var, optimization)

            # Optimize queryset
            if optimization:
                queryset = optimize_queryset(queryset, optimization)

            # Call Rust serializer (5-10x faster than Python!)
            # Returns Python list of dicts directly (no JSON string intermediate!)
            from djust._rust import serialize_queryset

            result = serialize_queryset(list(queryset), paths_for_var)

            # Log serialization results (using len() to avoid COUNT query)
            from .config import config

            if config.get("jit_debug"):
                logger.debug(
                    f"[JIT] Serialized {len(result)} {queryset.model.__name__} objects for '{variable_name}' using Rust"
                )
                logger.debug(f"[JIT DEBUG] Rust serializer returned {len(result)} items")
                if result:
                    logger.debug(f"[JIT DEBUG] First item keys: {list(result[0].keys())}")
            return result

        except Exception as e:
            # Fallback to default serialization on any error
            import traceback

            logger.error(
                f"[JIT ERROR] Serialization failed for '{variable_name}': {e}\nTraceback:\n{traceback.format_exc()}"
            )
            return [json.loads(json.dumps(obj, cls=DjangoJSONEncoder)) for obj in queryset]

    def _jit_serialize_model(self, obj, template_content: str, variable_name: str) -> Dict:
        """
        Apply JIT auto-serialization to a single Django Model instance.

        Args:
            obj: Django Model instance
            template_content: Template source code
            variable_name: Variable name in template

        Returns:
            Serialized dictionary
        """
        if not JIT_AVAILABLE or not extract_template_variables:
            # Fallback to default DjangoJSONEncoder
            return json.loads(json.dumps(obj, cls=DjangoJSONEncoder))

        try:
            # Extract variable paths
            variable_paths_map = extract_template_variables(template_content)
            paths_for_var = variable_paths_map.get(variable_name, [])

            if not paths_for_var:
                # Use default DjangoJSONEncoder
                return json.loads(json.dumps(obj, cls=DjangoJSONEncoder))

            # Generate cache key (includes model hash for invalidation on model changes)
            model_class = obj.__class__
            template_hash = hashlib.sha256(template_content.encode()).hexdigest()[:8]
            model_hash = _get_model_hash(model_class)
            cache_key = (template_hash, variable_name, model_hash)

            # Check cache
            if cache_key in _jit_serializer_cache:
                serializer, _ = _jit_serializer_cache[cache_key]
            else:
                # Generate and compile serializer
                code = generate_serializer_code(model_class.__name__, paths_for_var)
                func_name = f"serialize_{variable_name}_{template_hash}"
                serializer = compile_serializer(code, func_name)

                # Cache for future use (no optimization for single instances)
                _jit_serializer_cache[cache_key] = (serializer, None)

            return serializer(obj)

        except Exception as e:
            # Fallback to default serialization on any error
            logger.debug(f"JIT serialization failed for {variable_name}: {e}")
            return json.loads(json.dumps(obj, cls=DjangoJSONEncoder))

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        """
        Get the context data for rendering. Override to customize context.

        Returns:
            Dictionary of context variables

        Notes:
            - Automatically serializes datetime, UUID, Decimal, and Django models
            - Use DjangoJSONEncoder for custom type handling
            - JIT auto-serialization for QuerySets and Models (Phase 4)

        JIT Auto-Serialization (Phase 4):
            - Automatically extracts template variable access patterns
            - Generates optimized select_related/prefetch_related calls
            - Compiles custom serializer functions for each variable
            - 80%+ reduction in database queries
            - <1ms serialization overhead after first call
        """
        from .component import Component, LiveComponent
        from django.db.models import QuerySet

        context = {}

        # Add all non-private attributes as context
        for key in dir(self):
            if not key.startswith("_"):
                try:
                    value = getattr(self, key)
                    if not callable(value):
                        # Include Component instances (stateless components)
                        # Include LiveComponent instances (stateful components)
                        # OR JSON-serializable values (for state storage)
                        if isinstance(value, (Component, LiveComponent)):
                            # Auto-register LiveComponents for event handling
                            if isinstance(value, LiveComponent):
                                self._register_component(value)
                            context[key] = value
                        else:
                            # PERFORMANCE FIX: Don't test serializability by actually serializing!
                            # The old approach (json.dumps() test) triggered expensive recursive
                            # serialization with database queries for nested models (e.g., 402 queries
                            # for tenant lists due to User → Groups → Permissions traversal).
                            #
                            # Instead, skip only known non-serializable types (functions, modules, etc.)
                            # and let everything else through. JIT serialization and template rendering
                            # will handle Django models/QuerySets appropriately.
                            import types
                            from django.http import HttpRequest

                            if isinstance(
                                value,
                                (
                                    types.FunctionType,
                                    types.MethodType,
                                    types.ModuleType,
                                    type,
                                    types.BuiltinFunctionType,
                                    HttpRequest,  # Skip request objects
                                ),
                            ):
                                # Skip non-serializable types
                                pass
                            else:
                                # Include everything else (primitives, collections, models, etc.)
                                context[key] = value
                except (AttributeError, TypeError):
                    # Skip class-only methods and other inaccessible attributes
                    continue

        # JIT auto-serialization for QuerySets and Models (Phase 4)
        jit_serialized_keys = set()  # Track which keys were JIT-serialized
        if JIT_AVAILABLE:
            try:
                template_content = self._get_template_content()
                if template_content:
                    # Apply JIT serialization to QuerySets and Models
                    for key, value in list(context.items()):
                        if isinstance(value, QuerySet):
                            # Auto-serialize QuerySet with query optimization
                            serialized = self._jit_serialize_queryset(value, template_content, key)
                            context[key] = serialized
                            jit_serialized_keys.add(key)

                            # GENERIC OPTIMIZATION: Auto-add count using len() on serialized list
                            # This avoids redundant COUNT(*) queries when views need totals
                            if isinstance(serialized, list):
                                count_key = f"{key}_count"
                                if count_key not in context:
                                    context[count_key] = len(serialized)

                        elif isinstance(value, models.Model):
                            # Auto-serialize Model instance
                            context[key] = self._jit_serialize_model(value, template_content, key)
                            jit_serialized_keys.add(key)
            except Exception as e:
                # Graceful fallback - log but continue with default serialization
                logger.debug(f"JIT auto-serialization failed: {e}", exc_info=True)

        # GENERIC OPTIMIZATION: Auto-add count for plain lists in context (Phase 4+)
        # This handles cases where views create processed lists (not QuerySets)
        for key, value in list(context.items()):
            if isinstance(value, list) and not key.endswith("_count"):
                count_key = f"{key}_count"
                if count_key not in context:
                    context[count_key] = len(value)

        # Fallback serialization for Model instances that weren't JIT-serialized
        # This handles cases where:
        # 1. No template is defined
        # 2. Variable is not accessed in template
        for key, value in list(context.items()):
            if key not in jit_serialized_keys and isinstance(value, models.Model):
                # Serialize using DjangoJSONEncoder
                serialized = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
                context[key] = serialized

        # Store JIT-serialized keys for debugging/optimization tracking
        self._jit_serialized_keys = jit_serialized_keys

        return context

    def _get_context_processors(self) -> list:
        """
        Get context processors from DjustTemplateBackend settings.

        Results are cached in module-level dict keyed by settings hash.
        Cache is invalidated when Django settings change (e.g., in tests).
        """
        from django.conf import settings

        # Use id(settings._wrapped) as cache key - changes when settings are overridden
        cache_key = id(getattr(settings, '_wrapped', settings))

        if cache_key in _context_processors_cache:
            return _context_processors_cache[cache_key]

        # Find DjustTemplateBackend in TEMPLATES setting
        for template_config in getattr(settings, 'TEMPLATES', []):
            if template_config.get('BACKEND') == 'djust.template_backend.DjustTemplateBackend':
                processors = template_config.get('OPTIONS', {}).get('context_processors', [])
                _context_processors_cache[cache_key] = processors
                return processors

        _context_processors_cache[cache_key] = []
        return []

    def _apply_context_processors(self, context: Dict[str, Any], request) -> Dict[str, Any]:
        """
        Apply Django context processors to the context.

        This ensures that variables provided by context processors (like GOOGLE_ANALYTICS_ID,
        user, messages, etc.) are available in templates rendered by LiveView.

        Note: Context processors are only applied during HTTP requests (GET/POST).
        WebSocket updates via _sync_state_to_rust() don't have access to the request
        object, so context processor values from the initial render are preserved.

        Args:
            context: The context dict from get_context_data()
            request: The HTTP request object

        Returns:
            Updated context dict with context processor values
        """
        if request is None:
            return context

        from django.utils.module_loading import import_string

        # Get cached context processors list
        context_processors = self._get_context_processors()

        # Apply each context processor
        for processor_path in context_processors:
            try:
                processor = import_string(processor_path)
                processor_context = processor(request)
                if processor_context:
                    context.update(processor_context)
            except Exception as e:
                logger.warning(f"Failed to apply context processor {processor_path}: {e}")

        return context

    def _initialize_rust_view(self, request=None):
        """Initialize the Rust LiveView backend"""

        print(
            f"[LiveView] _initialize_rust_view() called, _rust_view={self._rust_view}",
            file=sys.stderr,
        )

        if self._rust_view is None:
            # Try to get from cache if we have a session
            # Prefer WebSocket session_id for consistency across mount + events
            if hasattr(self, "_websocket_session_id") and self._websocket_session_id:
                # WebSocket mode: use WebSocket session_id for consistent VDOM caching
                # Include view class name AND path to differentiate views/URLs with different structures
                ws_path = getattr(self, "_websocket_path", "/")
                ws_query = getattr(self, "_websocket_query_string", "")

                # Hash query string for consistent cache keys (handles param ordering)
                # Note: MD5 is used here for non-cryptographic cache key generation only
                query_hash = ""
                if ws_query:
                    # Sort params for consistent hashing regardless of order
                    params = parse_qs(ws_query)
                    sorted_query = urlencode(sorted(params.items()), doseq=True)
                    query_hash = hashlib.md5(sorted_query.encode()).hexdigest()[:8]

                view_key = f"liveview_ws_{self.__class__.__name__}_{ws_path}"
                if query_hash:
                    view_key = f"{view_key}_{query_hash}"
                session_key = self._websocket_session_id

                from .state_backend import get_backend

                backend = get_backend()
                self._cache_key = f"{session_key}_{view_key}"
                print(
                    f"[LiveView] Cache lookup (WebSocket): cache_key={self._cache_key}",
                    file=sys.stderr,
                )

                # Try to get cached RustLiveView from backend
                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    print("[LiveView] Cache HIT! Using cached RustLiveView", file=sys.stderr)
                    # Update timestamp on access

                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    print("[LiveView] Cache MISS! Will create new RustLiveView", file=sys.stderr)
            elif request and hasattr(request, "session"):
                # HTTP mode: use Django session
                # Include query string hash for consistent caching of different URL params
                # Note: MD5 is used here for non-cryptographic cache key generation only
                view_key = f"liveview_{request.path}"
                if request.GET:
                    query_hash = hashlib.md5(request.GET.urlencode().encode()).hexdigest()[:8]
                    view_key = f"{view_key}_{query_hash}"
                session_key = request.session.session_key
                if not session_key:
                    request.session.create()
                    session_key = request.session.session_key

                from .state_backend import get_backend

                backend = get_backend()
                self._cache_key = f"{session_key}_{view_key}"
                print(
                    f"[LiveView] Cache lookup (HTTP): cache_key={self._cache_key}", file=sys.stderr
                )

                # Try to get cached RustLiveView from backend
                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    print("[LiveView] Cache HIT! Using cached RustLiveView", file=sys.stderr)
                    # Update timestamp on access

                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    print("[LiveView] Cache MISS! Will create new RustLiveView", file=sys.stderr)

            # Create new RustLiveView with the template
            # The template includes <div data-liveview-root> which matches what the client patches
            # get_template() returns child blocks (with wrapper) for inheritance,
            # or raw template for non-inheritance
            template_source = self.get_template()

            print(
                f"[LiveView] Creating NEW RustLiveView for cache_key={self._cache_key}",
                file=sys.stderr,
            )
            print(f"[LiveView] Template length: {len(template_source)} chars", file=sys.stderr)
            print(f"[LiveView] Template preview: {template_source[:200]}...", file=sys.stderr)

            self._rust_view = RustLiveView(template_source)

            # Cache it if we have a cache key
            if self._cache_key:
                from .state_backend import get_backend

                backend = get_backend()
                backend.set(self._cache_key, self._rust_view)

    def _sync_state_to_rust(self):
        """Sync Python state to Rust backend"""
        if self._rust_view:
            from .component import Component, LiveComponent
            from django import forms

            context = self.get_context_data()

            # Pre-render components since Rust can't call Python methods
            # Also exclude Django Form instances which aren't JSON serializable
            rendered_context = {}
            for key, value in context.items():
                if isinstance(value, (Component, LiveComponent)):
                    # Create a dict with the pre-rendered HTML so {{ component.render }} works
                    rendered_html = str(value.render())
                    rendered_context[key] = {"render": rendered_html}
                elif isinstance(value, forms.Form):
                    # Skip Form instances - they're not JSON serializable
                    # Form state is managed separately via form_data, form_errors, etc.
                    continue
                else:
                    rendered_context[key] = value

            # Serialize and deserialize to ensure all types are JSON-compatible
            # This converts UUIDs, datetimes, etc. to their JSON representations
            json_str = json.dumps(rendered_context, cls=DjangoJSONEncoder)
            json_compatible_context = json.loads(json_str)

            self._rust_view.update_state(json_compatible_context)

    def _extract_handler_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Extract decorator metadata from all event handlers.

        Inspects all methods for the _djust_decorators attribute
        added by decorators like @debounce, @throttle, @optimistic, etc.

        Results are cached after first extraction since decorator metadata
        is static for a given view class.

        Returns:
            Dictionary mapping handler names to their decorator metadata.

            Example:
                {
                    "search": {
                        "debounce": {"wait": 0.5, "max_wait": None},
                        "optimistic": True
                    },
                    "update_slider": {
                        "throttle": {"interval": 0.1, "leading": True, "trailing": True}
                    }
                }
        """
        # Return cached metadata if available
        if self._handler_metadata is not None:
            logger.debug(
                f"[LiveView] Using cached handler metadata for {self.__class__.__name__} "
                f"({len(self._handler_metadata)} handlers)"
            )
            return self._handler_metadata

        logger.debug(f"[LiveView] Extracting handler metadata for {self.__class__.__name__}")
        metadata = {}

        # Iterate all methods
        for name in dir(self):
            # Skip private methods
            if name.startswith("_"):
                continue

            try:
                method = getattr(self, name)

                # Check if it's callable
                if not callable(method):
                    continue

                # Check for decorator metadata
                if hasattr(method, "_djust_decorators"):
                    metadata[name] = method._djust_decorators
                    logger.debug(
                        f"[LiveView]   Found decorated handler: {name} -> "
                        f"{list(method._djust_decorators.keys())}"
                    )

            except (AttributeError, TypeError):
                # Skip attributes that can't be accessed
                continue

        # Cache the result
        self._handler_metadata = metadata
        logger.debug(
            f"[LiveView] Extracted {len(metadata)} decorated handlers, caching for future use"
        )

        return metadata

    # ============================================================================
    # TEMPLATE RENDERING
    # ============================================================================

    def render(self, request=None) -> str:
        """
        Render the view to HTML.

        Returns the rendered HTML from the template. For WebSocket updates,
        caller should use _extract_liveview_content() to get innerHTML only.

        After rendering, temporary_assigns and streams are reset to free memory.
        Use dj-update="append" or dj-stream in templates to preserve client content.

        Args:
            request: The request object

        Returns:
            Rendered HTML with embedded handler metadata
        """
        self._initialize_rust_view(request)
        self._sync_state_to_rust()
        html = self._rust_view.render()

        # Post-process to hydrate React components
        html = self._hydrate_react_components(html)

        # Inject handler metadata for client-side decorators
        html = self._inject_handler_metadata(html)

        # Reset temporary assigns and streams to free memory after rendering
        self._reset_temporary_assigns()

        return html

    def _inject_handler_metadata(self, html: str) -> str:
        """
        Inject handler metadata script into HTML.

        Adds a <script> tag that sets window.handlerMetadata with
        decorator metadata for all handlers.

        Args:
            html: Rendered HTML

        Returns:
            HTML with injected metadata script
        """
        # Extract metadata
        metadata = self._extract_handler_metadata()

        # Skip injection if no metadata
        if not metadata:
            logger.debug("[LiveView] No handler metadata to inject, skipping script injection")
            return html

        logger.debug(f"[LiveView] Injecting handler metadata script for {len(metadata)} handlers")

        # Build script tag
        script = f"""
<script>
// Handler metadata for client-side decorators
window.handlerMetadata = window.handlerMetadata || {{}};
Object.assign(window.handlerMetadata, {json.dumps(metadata)});
</script>"""

        # Try to inject before </body>
        if "</body>" in html:
            html = html.replace("</body>", f"{script}\n</body>")
            logger.debug("[LiveView] Injected metadata script before </body>")
        # Fallback: inject before </html>
        elif "</html>" in html:
            html = html.replace("</html>", f"{script}\n</html>")
            logger.debug("[LiveView] Injected metadata script before </html>")
        # Fallback: append to end (for template fragments)
        else:
            html = html + script
            logger.debug("[LiveView] Appended metadata script to end of HTML")

        return html

    def _strip_comments_and_whitespace(self, html: str) -> str:
        """
        Strip HTML comments and normalize whitespace to match Rust VDOM parser behavior.

        The Rust VDOM parser (parser.rs) filters out comments and whitespace-only text nodes.
        We need the client DOM to match the server VDOM structure, so we strip comments
        from rendered HTML before sending to client.

        IMPORTANT: Preserve whitespace inside <pre> and <code> tags to maintain formatting.
        """
        import re

        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # Preserve whitespace inside <pre> and <code> tags
        # Extract and temporarily replace these blocks
        preserved_blocks = []

        def preserve_block(match):
            preserved_blocks.append(match.group(0))
            return f"__PRESERVED_BLOCK_{len(preserved_blocks) - 1}__"

        # Preserve <pre> blocks (including nested content)
        html = re.sub(r"<pre[^>]*>.*?</pre>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE)
        # Preserve <code> blocks not inside <pre> (already handled above)
        html = re.sub(
            r"<code[^>]*>.*?</code>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE
        )

        # Now normalize whitespace in the rest of the HTML
        # Collapse multiple whitespace to single space
        html = re.sub(r"\s+", " ", html)
        # Remove whitespace between tags
        html = re.sub(r">\s+<", "><", html)

        # Restore preserved blocks
        for i, block in enumerate(preserved_blocks):
            html = html.replace(f"__PRESERVED_BLOCK_{i}__", block)

        return html

    def _extract_liveview_content(self, html: str) -> str:
        """
        Extract the inner content of [data-liveview-root] from full HTML.

        This ensures the HTML sent over WebSocket matches what the client expects:
        just the content to insert into the existing [data-liveview-root] container.
        """
        import re

        # Find the opening tag for [data-liveview-root]
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", html, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return full HTML
            return html

        start_pos = opening_match.end()

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = start_pos

        while depth > 0 and pos < len(html):
            # Look for next <div or </div
            open_match = re.search(r"<div\b", html[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", html[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                # Found opening div first
                depth += 1
                pos = open_pos + 4  # Skip past '<div'
            else:
                # Found closing div first
                depth -= 1
                if depth == 0:
                    # This is the matching closing tag - return inner content only
                    return html[start_pos:close_pos]
                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return full HTML
        return html

    def _extract_liveview_root_with_wrapper(self, template: str) -> str:
        """
        Extract the <div data-liveview-root>...</div> section from a template (WITH the wrapper div).

        This is used to ensure server VDOM and client VDOM track the same structure:
        - Client's getNodeByPath([]) returns the div[data-liveview-root] element
        - Server VDOM must also track div[data-liveview-root] as the root
        - This ensures paths match between server patches and client DOM

        Args:
            template: Template string (may include DOCTYPE, html, head, body, etc.)

        Returns:
            Template with just the <div data-liveview-root>...</div> section (WITH wrapper)
        """
        import re

        # Find the opening tag for [data-liveview-root]
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", template, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return template as-is
            return template

        start_pos = opening_match.start()  # Start of <div tag, not end
        inner_start_pos = opening_match.end()  # End of opening tag

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = inner_start_pos

        while depth > 0 and pos < len(template):
            # Look for next <div or </div
            open_match = re.search(r"<div\b", template[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", template[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                # Found opening div first
                depth += 1
                pos = open_pos + 4  # Skip past '<div'
            else:
                # Found closing div first
                depth -= 1
                if depth == 0:
                    # This is the matching closing tag - return WITH wrapper
                    end_pos = pos + close_match.end()  # Include </div>
                    return template[start_pos:end_pos]
                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return template as-is
        return template

    def _extract_liveview_template_content(self, template: str) -> str:
        """
        Extract the innerHTML of [data-liveview-root] from a TEMPLATE (not rendered HTML).

        This is used to establish VDOM baseline with only the innerHTML portion of the template,
        ensuring patches are calculated for the correct structure.

        Args:
            template: Template string with variables (e.g., "{{ count }}")

        Returns:
            Template innerHTML without the wrapper div
        """
        import re

        # Find the opening tag for [data-liveview-root]
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", template, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return template as-is
            return template

        start_pos = opening_match.end()

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = start_pos

        while depth > 0 and pos < len(template):
            # Look for next <div or </div (but not in Django template tags)
            # This is a simplified parser - doesn't handle all edge cases
            open_match = re.search(r"<div\b", template[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", template[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                # Found opening div first
                depth += 1
                pos = open_pos + 4  # Skip past '<div'
            else:
                # Found closing div first
                depth -= 1
                if depth == 0:
                    # This is the matching closing tag - return inner content only
                    return template[start_pos:close_pos]
                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return template as-is
        return template

    def _strip_liveview_root_in_html(self, html: str) -> str:
        """
        Strip comments and whitespace from [data-liveview-root] div in full HTML page.

        This ensures the liveview-root div structure matches the stripped template used
        by the server VDOM, while preserving the rest of the page (DOCTYPE, head, body, etc.)
        as-is.

        Args:
            html: Full HTML page including DOCTYPE, html, head, body tags

        Returns:
            HTML with liveview-root div stripped but rest of page preserved
        """
        import re

        # Find the liveview-root div (WITH wrapper)
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", html, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return HTML as-is
            return html

        start_pos = opening_match.start()  # Start of <div tag
        inner_start_pos = opening_match.end()  # End of opening tag

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = inner_start_pos

        while depth > 0 and pos < len(html):
            # Look for next <div or </div
            open_match = re.search(r"<div\b", html[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", html[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                # Found opening div first
                depth += 1
                pos = open_pos + 4  # Skip past '<div'
            else:
                # Found closing div first
                depth -= 1
                if depth == 0:
                    # This is the matching closing tag
                    end_pos = pos + close_match.end()  # Include </div>

                    # Extract the liveview-root div (WITH wrapper)
                    liveview_div = html[start_pos:end_pos]

                    # Strip comments and whitespace from this div only
                    stripped_div = self._strip_comments_and_whitespace(liveview_div)

                    # Replace the original liveview-root div with the stripped version
                    return html[:start_pos] + stripped_div + html[end_pos:]

                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return HTML as-is
        return html

    # ============================================================================
    # FULL TEMPLATE RENDERING (with inheritance)
    # ============================================================================

    def render_full_template(self, request=None, serialized_context=None) -> str:
        """
        Render the full template including base template inheritance.
        Used for initial GET requests when using template inheritance.

        Args:
            request: HTTP request object
            serialized_context: Optional pre-serialized context dict (optimization to avoid re-serialization)

        Returns the complete HTML document (DOCTYPE, html, head, body, etc.)
        """
        # Check if we have a full template from template inheritance
        if hasattr(self, "_full_template") and self._full_template:
            # Render the full template using Rust
            from djust._rust import RustLiveView

            temp_rust = RustLiveView(self._full_template)

            # Use pre-serialized context if provided (optimization for GET requests)
            if serialized_context is not None:
                json_compatible_context = serialized_context
            else:
                # Sync state to this temporary view
                from .component import Component, LiveComponent

                context = self.get_context_data()
                # Apply context processors (for GOOGLE_ANALYTICS_ID, user, messages, etc.)
                context = self._apply_context_processors(context, request)

                rendered_context = {}
                for key, value in context.items():
                    if isinstance(value, (Component, LiveComponent)):
                        rendered_context[key] = {"render": str(value.render())}
                    else:
                        rendered_context[key] = value

                # Serialize and deserialize to ensure all types are JSON-compatible
                # This converts Django models, QuerySets, UUIDs, datetimes, etc.
                json_str = json.dumps(rendered_context, cls=DjangoJSONEncoder)
                json_compatible_context = json.loads(json_str)

            temp_rust.update_state(json_compatible_context)
            html = temp_rust.render()

            html = self._hydrate_react_components(html)

            # Inject handler metadata for client-side decorators
            html = self._inject_handler_metadata(html)

            return html
        else:
            # No full template - use regular render
            return self.render(request)

    def render_with_diff(
        self, request=None, extract_liveview_root=False
    ) -> tuple[str, Optional[str], int]:
        """
        Render the view and compute diff from last render.

        Args:
            extract_liveview_root: If True, extract innerHTML of [data-liveview-root]
                                  before establishing VDOM. This ensures Rust VDOM
                                  tracks exactly what the client's innerHTML contains.

        Returns:
            Tuple of (html, patches_json, version)
        """

        print(
            f"[LiveView] render_with_diff() called (extract_liveview_root={extract_liveview_root})",
            file=sys.stderr,
        )
        print(f"[LiveView] _rust_view before init: {self._rust_view}", file=sys.stderr)

        # Initialize Rust view if not already done
        self._initialize_rust_view(request)

        # If template is a property (dynamic), update the template
        # while preserving VDOM state for efficient patching
        if hasattr(self.__class__, "template") and isinstance(
            getattr(self.__class__, "template"), property
        ):
            print("[LiveView] template is a property - updating template", file=sys.stderr)
            new_template = self.get_template()
            self._rust_view.update_template(new_template)

        print(f"[LiveView] _rust_view after init: {self._rust_view}", file=sys.stderr)

        self._sync_state_to_rust()

        result = self._rust_view.render_with_diff()
        html, patches_json, version = result

        print(
            f"[LiveView] Rendered HTML length: {len(html)} chars, starts with: {html[:100]}...",
            file=sys.stderr,
        )

        # Extract [data-liveview-root] innerHTML if requested
        # This ensures Rust VDOM tracks exactly what the client's innerHTML contains
        if extract_liveview_root:
            html = self._extract_liveview_content(html)
            print(
                f"[LiveView] Extracted [data-liveview-root] content ({len(html)} chars)",
                file=sys.stderr,
            )

        print(
            f"[LiveView] Rust returned: version={version}, patches={'YES' if patches_json else 'NO'}",
            file=sys.stderr,
        )
        if not patches_json:
            print("[LiveView] NO PATCHES GENERATED!", file=sys.stderr)
        else:
            # Show first few patches for debugging (if enabled)
            from djust.config import config

            if config.get("debug_vdom", False):
                import json as json_module

                patches_list = json_module.loads(patches_json) if patches_json else []
                print(f"[LiveView] Generated {len(patches_list)} patches:", file=sys.stderr)
                for i, patch in enumerate(patches_list[:5]):  # Show first 5 patches
                    patch_type = patch.get("type", "Unknown")
                    path = patch.get("path", [])

                    # Add context about what we're patching
                    if patch_type == "SetAttr":
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} '{patch.get('key')}' = '{patch.get('value')}' at path {path}",
                            file=sys.stderr,
                        )
                    elif patch_type == "RemoveAttr":
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} '{patch.get('key')}' at path {path}",
                            file=sys.stderr,
                        )
                    elif patch_type == "SetText":
                        text_preview = patch.get("text", "")[:50]
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} to '{text_preview}' at path {path}",
                            file=sys.stderr,
                        )
                    else:
                        print(f"[LiveView]   Patch {i}: {patch}", file=sys.stderr)

        # Reset temporary assigns and streams to free memory after rendering
        self._reset_temporary_assigns()

        return (html, patches_json, version)

    # ============================================================================
    # COMPONENT STATE PERSISTENCE
    # ============================================================================

    def _extract_component_state(self, component) -> dict:
        """
        Extract state from a component for session storage.

        Args:
            component: LiveComponent instance

        Returns:
            Dictionary of component state
        """
        import json as json_module

        state = {}
        for key in dir(component):
            if not key.startswith("_") and key not in ("template_name",):
                try:
                    value = getattr(component, key)
                    if not callable(value):
                        # Only store JSON-serializable values
                        try:
                            json_module.dumps(value)
                            state[key] = value
                        except (TypeError, ValueError):
                            # Skip non-serializable values
                            pass
                except (AttributeError, TypeError):
                    pass
        return state

    def _restore_component_state(self, component, state: dict):
        """
        Restore state to a component from session storage.

        Args:
            component: LiveComponent instance
            state: Dictionary of component state
        """
        for key, value in state.items():
            if not key.startswith("_"):
                try:
                    setattr(component, key, value)
                except (AttributeError, TypeError):
                    # Skip read-only properties
                    pass

    def _assign_component_ids(self):
        """
        Automatically assign IDs to components based on their attribute names.

        This is called after mount() to ensure components have stable, deterministic IDs
        instead of random UUIDs. For example:
            self.navbar_example = NavBar(...)  → automatically gets _auto_id="navbar_example"

        This makes HTTP mode work seamlessly with VDOM diffing, as IDs remain consistent
        across requests without needing session storage.
        """
        from .component import Component, LiveComponent

        for key, value in self.__dict__.items():
            if isinstance(value, (Component, LiveComponent)) and not key.startswith("_"):
                # Assign automatic ID based on variable name
                value._auto_id = key

    def _save_components_to_session(self, request, context: dict):
        """
        Save component state to session with stable IDs.

        This is the DRY method used by both GET and POST handlers to persist
        component state (including Component and LiveComponent IDs) across requests.
        Essential for HTTP mode where view instances are recreated on each request.

        Args:
            request: Django request object
            context: Context dictionary containing components
        """
        from .component import Component, LiveComponent

        view_key = f"liveview_{request.path}"
        component_state = {}

        for key, component in context.items():
            # Save state for both Component and LiveComponent to preserve IDs across requests
            if isinstance(component, (Component, LiveComponent)):
                # Automatic ID assignment: attribute name -> component_id
                # e.g., self.navbar_example gets component_id="navbar_example"
                component.component_id = key
                component_state[key] = self._extract_component_state(component)

        # Serialize component state to ensure session-compatible types
        component_state_json = json.dumps(component_state, cls=DjangoJSONEncoder)
        component_state_serializable = json.loads(component_state_json)
        request.session[f"{view_key}_components"] = component_state_serializable
        request.session.modified = True

    def _lazy_serialize_context(self, context: dict) -> dict:
        """
        Lazy serialization: only serialize values that need conversion.

        This is 2-3x faster than full JSON round-trip because:
        - Simple types (str, int, float, bool, None) are left alone
        - Lists and dicts are recursively processed
        - Only Django models, Components, datetimes, etc. get serialized

        Args:
            context: Raw context dictionary

        Returns:
            JSON-compatible context dictionary
        """
        from .component import Component, LiveComponent
        from django.db.models import Model
        from datetime import datetime, date, time
        from decimal import Decimal
        from uuid import UUID

        def serialize_value(value):
            # Fast path: already JSON-compatible
            if value is None or isinstance(value, (str, int, float, bool)):
                return value

            # Lists: recursively process
            if isinstance(value, (list, tuple)):
                return [serialize_value(item) for item in value]

            # Dicts: recursively process
            if isinstance(value, dict):
                return {k: serialize_value(v) for k, v in value.items()}

            # Components: render to dict
            if isinstance(value, (Component, LiveComponent)):
                return {"render": str(value.render())}

            # Django models: convert to dict (if needed)
            # For now, just convert to string to avoid circular refs
            if isinstance(value, Model):
                return str(value)

            # Datetime types: convert to ISO format
            if isinstance(value, (datetime, date)):
                return value.isoformat()

            if isinstance(value, time):
                return value.isoformat()

            # Decimal/UUID: convert to string
            if isinstance(value, (Decimal, UUID)):
                return str(value)

            # Fallback: use DjangoJSONEncoder for complex types
            # This handles any remaining Django/Python types
            try:
                from django.core.serializers.json import DjangoJSONEncoder
                import json

                return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
            except (TypeError, ValueError):
                # Last resort: convert to string
                return str(value)

        return {k: serialize_value(v) for k, v in context.items()}

    @method_decorator(ensure_csrf_cookie)
    # ============================================================================
    # HTTP REQUEST HANDLERS
    # ============================================================================

    def get(self, request, *args, **kwargs):
        """Handle GET requests - initial page load"""
        import time

        t_start = time.perf_counter()

        # Initialize temporary assigns with default values before mount
        self._initialize_temporary_assigns()

        # IMPORTANT: mount() must be called first to initialize clean state
        t0 = time.perf_counter()
        self.mount(request, **kwargs)
        t_mount = (time.perf_counter() - t0) * 1000

        # Automatically assign deterministic IDs to components based on variable names
        t0 = time.perf_counter()
        self._assign_component_ids()
        t_assign = (time.perf_counter() - t0) * 1000

        # OPTIMIZATION: On GET requests, serialize for rendering but skip session storage
        # Session storage is only needed for POST events to restore state
        # GET requests call mount() which creates fresh state anyway

        # Ensure session exists
        if not request.session.session_key:
            request.session.create()

        # Get context for rendering
        t0 = time.perf_counter()
        context = self.get_context_data()
        # Apply context processors (for GOOGLE_ANALYTICS_ID, user, messages, etc.)
        context = self._apply_context_processors(context, request)
        t_get_context = (time.perf_counter() - t0) * 1000

        # Serialize state for rendering (but don't store in session)
        from .component import LiveComponent

        state = {k: v for k, v in context.items() if not isinstance(v, LiveComponent)}

        t0 = time.perf_counter()
        # OPTIMIZATION: JIT-serialized data is already JSON-compatible!
        # Don't re-serialize it with serialize_context() as that destroys the nested structure
        # The JIT serializer already created perfect nested dictionaries with method results
        # Just use the context directly after JIT serialization
        state_serializable = state
        t_json = (time.perf_counter() - t0) * 1000

        # OPTIMIZATION: Skip session storage on GET (session populated on first POST)
        # request.session[view_key] = state_serializable  # Skipped on GET
        t_save_components = 0.0  # Skipped: no session storage on GET

        # NOTE: Don't clear the cache on GET! The cache persists across requests
        # to maintain VDOM state. Only clear if we detect the user explicitly
        # wants a fresh page (e.g., query param ?refresh=1)
        # if cache_key in _rust_view_cache:
        #     print(f"[LiveView] Clearing cached RustLiveView for fresh session", file=sys.stderr)
        #     del _rust_view_cache[cache_key]
        #     # Also clear our reference so a new one will be created
        #     self._rust_view = None
        #     self._cache_key = None

        # OPTIMIZATION: On GET requests, render full HTML for the browser
        # Then establish VDOM baseline for future PATCH responses
        # This allows subsequent POST events to return minimal patches instead of full HTML

        # IMPORTANT: Always call get_template() on GET requests to set _full_template
        # This is needed because _full_template is used by render_full_template()
        t0 = time.perf_counter()
        self.get_template()
        t_get_template = (time.perf_counter() - t0) * 1000

        # Render full template for the browser (includes full HTML structure)
        # OPTIMIZATION: Pass the already-serialized context to avoid re-serialization
        # render_full_template() creates its own temporary RustLiveView, so we don't
        # need to initialize the cached one or sync state on GET requests
        t0 = time.perf_counter()
        html = self.render_full_template(request, serialized_context=state_serializable)
        t_render_full = (time.perf_counter() - t0) * 1000
        liveview_content = html

        # CRITICAL: Establish VDOM baseline for subsequent PATCH responses
        # Even though we rendered full HTML above, we need to initialize the RustLiveView
        # with the initial state so that future POST requests can generate patches
        t0 = time.perf_counter()
        self._initialize_rust_view(request)
        t_init_rust = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        self._sync_state_to_rust()
        t_sync = (time.perf_counter() - t0) * 1000

        # Establish VDOM baseline by calling render_with_diff() once
        # This first call returns no patches but sets up the baseline for future diffs
        t0 = time.perf_counter()
        _, _, _ = self.render_with_diff(request)
        t_render_diff = (time.perf_counter() - t0) * 1000

        # Wrap in Django template if wrapper_template is specified
        # (This is for the older wrapper pattern, not template inheritance)
        if hasattr(self, "wrapper_template") and self.wrapper_template:
            from django.template import loader

            wrapper = loader.get_template(self.wrapper_template)
            html = wrapper.render({"liveview_content": liveview_content}, request)
            # Inject LiveView content into [data-liveview-root] placeholder
            # Note: liveview_content already includes <div data-liveview-root>...</div>
            html = html.replace("<div data-liveview-root></div>", liveview_content)
        else:
            # No wrapper, return LiveView content directly
            html = liveview_content

        t_total = (time.perf_counter() - t_start) * 1000
        print("\n[LIVEVIEW GET TIMING]", file=sys.stderr)
        print(f"  mount(): {t_mount:.2f}ms", file=sys.stderr)
        print(f"  assign_component_ids(): {t_assign:.2f}ms", file=sys.stderr)
        print(f"  get_context_data(): {t_get_context:.2f}ms", file=sys.stderr)
        print(f"  JSON serialize/deserialize: {t_json:.2f}ms", file=sys.stderr)
        print(f"  save_components_to_session(): {t_save_components:.2f}ms", file=sys.stderr)
        print(f"  initialize_rust_view(): {t_init_rust:.2f}ms", file=sys.stderr)
        print(f"  sync_state_to_rust(): {t_sync:.2f}ms", file=sys.stderr)
        print(f"  get_template(): {t_get_template:.2f}ms", file=sys.stderr)
        print(f"  render_with_diff(): {t_render_diff:.2f}ms", file=sys.stderr)
        print(f"  render_full_template(): {t_render_full:.2f}ms", file=sys.stderr)
        print(f"  TOTAL get(): {t_total:.2f}ms\n", file=sys.stderr)

        # Debug: Save the rendered HTML to a file for inspection
        if "registration" in request.path:
            form_start = html.find("<form")
            if form_start != -1:
                form_end = html.find("</form>", form_start) + 7
                form_html = html[form_start:form_end]
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", delete=False, suffix=".html", prefix="registration_form_"
                ) as f:
                    f.write(form_html)
                    print(f"[LiveView] Saved form HTML to {f.name}", file=sys.stderr)

        # Inject view path into data-liveview-root for WebSocket mounting
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        html = html.replace(
            "<div data-liveview-root>", f'<div data-liveview-root data-live-view="{view_path}">'
        )

        # Inject LiveView client script
        html = self._inject_client_script(html)

        return HttpResponse(html)

    def post(self, request, *args, **kwargs):
        """Handle POST requests - event handling"""
        from .component import Component, LiveComponent
        import logging

        logger = logging.getLogger(__name__)

        try:
            data = json.loads(request.body)
            event_name = data.get("event")
            params = data.get("params", {})

            # Restore state from session
            view_key = f"liveview_{request.path}"
            saved_state = request.session.get(view_key, {})

            # IMPORTANT: Restore state BEFORE calling mount()
            # This ensures components are created with the correct restored state values
            for key, value in saved_state.items():
                if not key.startswith("_") and not callable(value):
                    try:
                        setattr(self, key, value)
                    except AttributeError:
                        # Skip read-only properties
                        pass

            # Initialize temporary assigns with default values
            self._initialize_temporary_assigns()

            # CRITICAL: Only call mount() if state was not restored from session
            # If state exists in session, we've already restored it above
            # Calling mount() would overwrite the restored state with initial values!
            if not saved_state:
                # No saved state - this is a new session, initialize with mount()
                self.mount(request, **kwargs)
            else:
                # State restored from session - skip mount() to preserve state
                # Components will be recreated with _assign_component_ids() below
                pass

            # Automatically assign deterministic IDs to components based on variable names
            self._assign_component_ids()

            # Restore component state (both Component and LiveComponent)
            # This preserves any component-specific state that wasn't captured by mount()
            component_state = request.session.get(f"{view_key}_components", {})
            for key, state in component_state.items():
                component = getattr(self, key, None)
                # Restore state for both Component and LiveComponent to preserve IDs
                if component and isinstance(component, (Component, LiveComponent)):
                    self._restore_component_state(component, state)

            # Call the event handler
            handler = getattr(self, event_name, None)
            if handler and callable(handler):
                # Check if handler has coerce_types setting from @event_handler decorator
                coerce = True  # Default to coercion enabled
                if hasattr(handler, "_djust_decorators"):
                    event_meta = handler._djust_decorators.get("event_handler", {})
                    coerce = event_meta.get("coerce_types", True)

                # Validate and coerce parameters before calling handler
                # Type coercion automatically converts string values from data-* attributes
                # to the expected types based on handler type hints
                validation = validate_handler_params(handler, params, event_name, coerce=coerce)
                if not validation["valid"]:
                    logger.error(f"Parameter validation failed: {validation['error']}")
                    return JsonResponse(
                        {
                            "type": "error",
                            "error": validation["error"],
                            "validation_details": {
                                "expected_params": validation["expected"],
                                "provided_params": validation["provided"],
                                "type_errors": validation["type_errors"],
                            },
                        },
                        status=400,
                    )

                # Use coerced params (string -> int, bool, etc. based on type hints)
                coerced_params = validation.get("coerced_params", params)
                if coerced_params:
                    handler(**coerced_params)
                else:
                    handler()

            # Save updated state back to session (exclude components)
            updated_context = self.get_context_data()
            state = {k: v for k, v in updated_context.items() if not isinstance(v, LiveComponent)}
            # Serialize to ensure session-compatible types (UUIDs, datetimes, etc.)
            state_json = json.dumps(state, cls=DjangoJSONEncoder)
            state_serializable = json.loads(state_json)
            request.session[view_key] = state_serializable

            # Save updated component state to session (DRY helper method)
            self._save_components_to_session(request, updated_context)

            # Render with diff to get patches
            html, patches_json, version = self.render_with_diff(request)

            import json as json_module

            # Threshold for when to use patches vs full HTML
            PATCH_THRESHOLD = 100

            # Extract cache_request_id if present (for @cache decorator)
            cache_request_id = params.get("_cacheRequestId")

            if patches_json:
                patches = json_module.loads(patches_json)
                patch_count = len(patches)

                # If patches are reasonable size AND non-empty, send ONLY patches
                # Otherwise send full HTML for efficiency
                if patch_count > 0 and patch_count <= PATCH_THRESHOLD:
                    # Send only patches - client will apply them
                    # If patches fail on client, it should reload the page
                    response_data = {"patches": patches, "version": version}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    return JsonResponse(response_data)
                else:
                    # Empty patches or too many patches - send full HTML instead
                    # Reset VDOM cache since we're sending full HTML
                    # This ensures next patches are calculated from the browser's normalized DOM
                    self._rust_view.reset()
                    response_data = {"html": html, "version": version}
                    if cache_request_id:
                        response_data["cache_request_id"] = cache_request_id
                    return JsonResponse(response_data)
            else:
                # No patches generated - send full HTML
                response_data = {"html": html, "version": version}
                if cache_request_id:
                    response_data["cache_request_id"] = cache_request_id
                return JsonResponse(response_data)

        except Exception as e:
            import traceback
            import logging
            from django.conf import settings

            logger = logging.getLogger(__name__)

            # Build detailed error message
            error_msg = f"Error in {self.__class__.__name__}"
            if event_name:
                error_msg += f".{event_name}()"
            error_msg += f": {type(e).__name__}: {str(e)}"

            # Log error with full traceback
            logger.error(error_msg, exc_info=True)

            # In DEBUG mode, include stack trace in response
            if settings.DEBUG:
                error_details = {
                    "error": error_msg,
                    "type": type(e).__name__,
                    "traceback": traceback.format_exc(),
                    "event": event_name,
                    "params": params,
                }
                return JsonResponse(error_details, status=500)
            else:
                # In production, just send user-friendly message
                return JsonResponse(
                    {
                        "error": "An error occurred processing your request. Please try again.",
                        "debug_hint": "Check server logs for details",
                    },
                    status=500,
                )

    # ============================================================================
    # POST-PROCESSING & CLIENT INJECTION
    # ============================================================================

    def get_debug_info(self) -> Dict[str, Any]:
        """
        Get debug information about this LiveView instance.

        Used by developer debug panel to show:
        - Available event handlers with signatures
        - Public variables and their current values
        - Decorator metadata

        Returns:
            Dict with debug information containing:
            - view_class: Name of the LiveView class
            - handlers: Dict of event handlers with parameter info
            - variables: Dict of public instance variables
            - template: Template name if set

        Example:
            >>> class MyView(LiveView):
            ...     template_name = 'my.html'
            ...     count = 0
            ...     @event_handler
            ...     def increment(self, amount: int = 1):
            ...         '''Increment the counter'''
            ...         self.count += amount
            >>> view = MyView()
            >>> info = view.get_debug_info()
            >>> assert 'increment' in info['handlers']
            >>> assert info['handlers']['increment']['params'][0]['name'] == 'amount'
        """
        from .validation import get_handler_signature_info

        handlers = {}
        variables = {}

        for name in dir(self):
            # Skip private attributes
            if name.startswith("_"):
                continue

            # Try to get attribute, skip if it raises AttributeError
            # (e.g., Django's classonlymethod decorator)
            try:
                attr = getattr(self, name)
            except AttributeError:
                continue

            # Collect event handlers
            if callable(attr) and hasattr(attr, "_is_event_handler"):
                sig_info = get_handler_signature_info(attr)

                handlers[name] = {
                    "name": name,
                    "params": sig_info["params"],
                    "description": sig_info["description"],
                    "accepts_kwargs": sig_info["accepts_kwargs"],
                    "decorators": getattr(attr, "_djust_decorators", {}),
                }

            # Collect public variables (not methods, not classes, not modules)
            elif (
                not callable(attr)
                and not isinstance(attr, type)
                and not hasattr(attr, "__module__")
            ):
                try:
                    # Skip Django Form instances - they're not useful in debug panel
                    from django import forms

                    if isinstance(attr, forms.Form):
                        continue

                    # Get type name
                    type_name = type(attr).__name__

                    # Calculate size using JSON serialization (more accurate for context size)
                    import json
                    import sys

                    try:
                        # Try to serialize to JSON to get actual context size
                        serialized = json.dumps(attr, default=str)
                        size_bytes = len(serialized.encode("utf-8"))
                    except (TypeError, ValueError):
                        # Fallback to sys.getsizeof for non-serializable objects
                        size_bytes = sys.getsizeof(attr)

                    # Truncate long values for display
                    value_repr = repr(attr)
                    if len(value_repr) > 100:
                        value_repr = value_repr[:100] + "..."

                    variables[name] = {
                        "name": name,
                        "type": type_name,
                        "value": value_repr,
                        "size_bytes": size_bytes,
                    }
                except Exception:
                    # Skip attributes that can't be represented
                    pass

        # Get debug panel configuration
        from .config import config

        max_history = config.get("debug_panel_max_history", 50)

        return {
            "view_class": self.__class__.__name__,
            "handlers": handlers,
            "variables": variables,
            "template": self.template_name if hasattr(self, "template_name") else None,
            "config": {"maxHistory": max_history},
        }

    def _hydrate_react_components(self, html: str) -> str:
        """
        Post-process HTML to hydrate React component placeholders with server-rendered content.

        The Rust renderer creates <div data-react-component="Name" data-react-props='{...}'>children</div>
        We need to call the Python renderer functions and inject their output.
        """
        import re
        from .react import react_components
        import json as json_module

        # Pattern to match React component divs
        pattern = r'<div data-react-component="([^"]+)" data-react-props=\'([^\']+)\'>(.*?)</div>'

        def replace_component(match):
            component_name = match.group(1)
            props_json = match.group(2)
            children = match.group(3)

            # Parse props
            try:
                props = json_module.loads(props_json)
            except json_module.JSONDecodeError:
                props = {}

            # Resolve any Django template variables in props (like {{ client_count }})
            context = self.get_context_data()
            resolved_props = {}
            for key, value in props.items():
                if isinstance(value, str) and "{{" in value and "}}" in value:
                    # Extract variable name from {{ var_name }}
                    var_match = re.search(r"\{\{\s*(\w+)\s*\}\}", value)
                    if var_match:
                        var_name = var_match.group(1)
                        if var_name in context:
                            resolved_props[key] = context[var_name]
                        else:
                            resolved_props[key] = value
                    else:
                        resolved_props[key] = value
                else:
                    resolved_props[key] = value

            # Get the renderer for this component
            renderer = react_components.get(component_name)

            if renderer:
                # Call the server-side renderer with resolved props
                rendered_content = renderer(resolved_props, children)
                # Create updated props JSON for client-side hydration
                resolved_props_json = json_module.dumps(resolved_props).replace('"', "&quot;")
                # Wrap with data attributes for client-side hydration
                return f"<div data-react-component=\"{component_name}\" data-react-props='{resolved_props_json}'>{rendered_content}</div>"
            else:
                # No renderer found, return placeholder
                return match.group(0)

        # Replace all React component placeholders
        html = re.sub(pattern, replace_component, html, flags=re.DOTALL)

        return html

    def _inject_client_script(self, html: str) -> str:
        """Inject the LiveView client JavaScript into the HTML"""
        from .config import config
        from django.conf import settings
        import json

        # Get WebSocket setting from config
        use_websocket = config.get("use_websocket", True)
        debug_vdom = config.get("debug_vdom", False)
        loading_grouping_classes = config.get(
            "loading_grouping_classes",
            ["d-flex", "btn-group", "input-group", "form-group", "btn-toolbar"],
        )

        # Convert Python list to JavaScript array
        loading_classes_js = json.dumps(loading_grouping_classes)

        # Include debug info if Django DEBUG mode
        debug_info_script = ""
        debug_css_link = ""
        if settings.DEBUG:
            debug_info = self.get_debug_info()
            debug_info_script = f"""
            <script>
                window.DJUST_DEBUG_INFO = {json.dumps(debug_info)};
            </script>
            """
            # Add CSS link for debug panel
            debug_css_link = '<link rel="stylesheet" href="/static/djust/debug-panel.css">'

        config_script = f"""
        <script>
            // djust configuration
            window.DJUST_USE_WEBSOCKET = {str(use_websocket).lower()};
            window.DJUST_DEBUG_VDOM = {str(debug_vdom).lower()};
            window.DJUST_LOADING_GROUPING_CLASSES = {loading_classes_js};
            // Enable debug logging for client-dev.js (development only)
            window.djustDebug = {str(settings.DEBUG).lower()};
        </script>
        {debug_info_script}
        """

        # Load client.js as external file (cacheable by browser)
        # Use defer for non-blocking load while preserving execution order
        from django.templatetags.static import static

        try:
            # Try to use Django's static file handling (production/dev with collectstatic)
            client_js_url = static("djust/client.js")
        except (ValueError, AttributeError):
            # Fall back to simple path (e.g., in test environment without collectstatic)
            client_js_url = "/static/djust/client.js"

        script = f'<script src="{client_js_url}" defer></script>'

        # In DEBUG mode, also load development tools
        if settings.DEBUG:
            try:
                client_dev_js_url = static("djust/client-dev.js")
            except (ValueError, AttributeError):
                client_dev_js_url = "/static/djust/client-dev.js"
            script += f'\n        <script src="{client_dev_js_url}" defer></script>'

        # Inject config and script tags
        full_script = config_script + script

        # Inject debug CSS in <head> if present
        if debug_css_link and "</head>" in html:
            html = html.replace("</head>", f"{debug_css_link}</head>")

        if "</body>" in html:
            html = html.replace("</body>", f"{full_script}</body>")
        else:
            html += full_script

        return html


def live_view(template_name: Optional[str] = None, template: Optional[str] = None):
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            def decrement():
                nonlocal count
                count -= 1

            return locals()

    Args:
        template_name: Path to Django template
        template: Inline template string

    Returns:
        View function
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(request, *args, **kwargs):
            # Create a dynamic LiveView class
            class DynamicLiveView(LiveView):
                pass

            if template_name:
                DynamicLiveView.template_name = template_name
            if template:
                DynamicLiveView.template = template

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                for key, value in result.items():
                    if not callable(value):
                        setattr(view, key, value)
                    else:
                        setattr(view, key, value)

            # Handle the request
            if request.method == "GET":
                return view.get(request, *args, **kwargs)
            elif request.method == "POST":
                return view.post(request, *args, **kwargs)

        return wrapper

    return decorator
