"""
JSON serialization utilities for Django models and Python types.

Extracted from live_view.py for modularity.
"""

import importlib.util
import json
import logging
from datetime import datetime, date, time
from decimal import Decimal
from typing import Dict, List
from uuid import UUID

from django.db import models

logger = logging.getLogger(__name__)

# Try to use orjson for faster JSON operations (2-3x faster than stdlib)
HAS_ORJSON = importlib.util.find_spec("orjson") is not None


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

    # Cache @property names per model class to avoid repeated MRO walks
    _property_cache: Dict[type, List[str]] = {}

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
        from .components.base import Component, LiveComponent
        from .components.base import (
            Component as BaseComponent,
            LiveComponent as BaseLiveComponent,
        )

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

        # Handle Django FieldFile/ImageFieldFile (must check before Model)
        from django.db.models.fields.files import FieldFile

        if isinstance(obj, FieldFile):
            # Return URL if file exists, otherwise None
            if obj:
                try:
                    return obj.url
                except ValueError:
                    # No file associated with this field
                    return None
            return None

        # Handle Django model instances (must be before duck-typing check
        # since models with 'url' and 'name' properties would match file-like heuristic)
        if isinstance(obj, models.Model):
            return self._serialize_model_safely(obj)

        # Duck-typing fallback for file-like objects (e.g., custom file fields, mocks)
        # Must have 'url' and 'name' attributes (signature of file fields)
        if hasattr(obj, "url") and hasattr(obj, "name") and not isinstance(obj, type):
            # Exclude dicts, lists, and strings which might have these attrs
            if not isinstance(obj, (dict, list, tuple, str)):
                if obj:
                    try:
                        return obj.url
                    except (ValueError, AttributeError):
                        return None
                return None

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

        # Include @property values defined on user model classes
        self._add_property_values(obj, result)

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

    def _add_property_values(self, obj, result):
        """Add @property values defined on user model classes (not Django base)."""
        model_class = obj.__class__

        if model_class not in DjangoJSONEncoder._property_cache:
            prop_names = []
            for cls in model_class.__mro__:
                if cls is models.Model:
                    break
                for attr_name, attr_value in cls.__dict__.items():
                    if isinstance(attr_value, property):
                        prop_names.append(attr_name)
            DjangoJSONEncoder._property_cache[model_class] = prop_names

        cache = getattr(obj, "_djust_prop_cache", None)
        if cache is None:
            cache = {}
            obj._djust_prop_cache = cache

        for attr_name in DjangoJSONEncoder._property_cache[model_class]:
            if attr_name not in result:
                if attr_name in cache:
                    result[attr_name] = cache[attr_name]
                    continue
                try:
                    val = getattr(obj, attr_name)
                    if isinstance(val, (str, int, float, bool, type(None))):
                        cache[attr_name] = val
                        result[attr_name] = val
                except Exception:
                    pass  # Property may raise; skip gracefully during serialization
