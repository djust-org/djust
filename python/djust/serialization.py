"""
JSON serialization utilities for Django models and Python types.

Extracted from live_view.py for modularity.
"""

import importlib.util
import json
import logging
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from typing import Any, Dict, List
from uuid import UUID

from django.db import models
from django.utils.functional import Promise

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

        # Safety net: skip callable objects (e.g., dict.items method references
        # that leaked through JIT codegen). These should never be in serialized
        # context but can appear when template variable extraction picks up
        # dict method names like .items/.keys/.values.
        if callable(obj):
            logger.debug(
                "Skipping callable %s during JSON serialization",
                type(obj).__name__,
            )
            return None

        return super().default(obj)

    def _serialize_model_safely(self, obj):
        """Cache-aware model serialization that prevents N+1 queries.

        Only accesses related objects if they were prefetched via
        select_related() or prefetch_related(). Otherwise, only includes
        the FK ID without triggering a database query.
        """
        result = {
            "id": str(obj.pk) if obj.pk is not None else None,
            "pk": obj.pk,
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
                        logger.debug(
                            "Failed to access relation '%s' on %s", field_name, type(obj).__name__
                        )
                        related = None

                    if related and DjangoJSONEncoder._depth < self._get_max_depth():
                        result[field_name] = self._serialize_model_safely(related)
                    elif related:
                        result[field_name] = {
                            "id": str(related.pk) if related.pk is not None else None,
                            "pk": related.pk,
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
                    logger.debug(
                        "Skipping inaccessible field '%s' on %s", field_name, type(obj).__name__
                    )

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
                # Skip methods that fail - they may require arguments,
                # access missing related objects, or have other runtime errors.
                logger.debug(
                    "Skipping method '%s' on %s during serialization", attr_name, type(obj).__name__
                )

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
                    logger.debug(
                        "Skipping property '%s' on %s during serialization",
                        attr_name,
                        type(obj).__name__,
                    )


# ---------------------------------------------------------------------------
# Direct Python-to-Python value normalizer (replaces json.loads(json.dumps()))
# ---------------------------------------------------------------------------

# Singleton encoder instance reused for model serialization (GIL-safe: only
# calls _serialize_model_safely which mutates _property_cache and
# obj._djust_prop_cache -- dict writes are atomic under CPython's GIL but
# this is not truly thread-safe under free-threaded builds).
_encoder = DjangoJSONEncoder()


def normalize_django_value(value: Any, _depth: int = 0) -> Any:
    """Convert Django/Python types to JSON-safe Python primitives **directly**.

    For types supported by both, this produces output identical to
    ``json.loads(json.dumps(value, cls=DjangoJSONEncoder))`` but avoids the
    serialise-then-parse roundtrip through JSON text, giving a meaningful
    speedup when called in hot paths (context serialization, state sync).

    **Enhancements beyond DjangoJSONEncoder**: the following types would raise
    ``TypeError`` under ``json.dumps(value, cls=DjangoJSONEncoder)`` but are
    handled here as a convenience:

    - timedelta  -- ISO-8601 duration string (via ``django.utils.duration``)
    - Promise    -- str() (Django lazy translation strings)

    Supported types:
    - None, bool, int, float, str  -- pass through
    - Decimal                      -- float()
    - UUID                         -- str()
    - datetime, date, time         -- .isoformat()
    - timedelta                    -- ISO-8601 duration string (via Django util)
    - Promise (lazy strings)       -- str()
    - dict                         -- recurse values
    - list / tuple                 -- recurse elements (always returns list)
    - Django Model                 -- serialized via DjangoJSONEncoder._serialize_model_safely, then recursed
    - QuerySet                     -- list of normalized models
    - FieldFile / file-like        -- .url or None
    - Component / LiveComponent    -- str() (renders HTML)
    - callable                     -- None (safety net, matches encoder)
    - anything else                -- str() fallback

    Args:
        value: The value to normalize.
        _depth: Internal recursion depth counter (do not set manually).
    """
    # Fast path: JSON-native primitives need no conversion
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return value

    # Containers -- recurse
    if isinstance(value, dict):
        return {k: normalize_django_value(v, _depth) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [normalize_django_value(item, _depth) for item in value]

    # Django lazy translation strings (Promise) -- must be before str check
    # since Promise is not a str subclass
    if isinstance(value, Promise):
        return str(value)

    # Decimal -> float (matches DjangoJSONEncoder.default)
    if isinstance(value, Decimal):
        return float(value)

    # UUID -> str
    if isinstance(value, UUID):
        return str(value)

    # datetime/date/time -> isoformat
    # Note: check datetime before date because datetime is a subclass of date
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    # timedelta -> ISO-8601 duration string (Django compat)
    if isinstance(value, timedelta):
        from django.utils.duration import duration_iso_string

        return duration_iso_string(value)

    # Django FieldFile / ImageFieldFile (must check before Model)
    from django.db.models.fields.files import FieldFile

    if isinstance(value, FieldFile):
        if value:
            try:
                return value.url
            except ValueError:
                return None
        return None

    # Django Model -> serialize via encoder, then normalize nested values
    if isinstance(value, models.Model):
        max_depth = DjangoJSONEncoder._get_max_depth()
        if _depth >= max_depth:
            # At max depth, return a minimal representation
            return {
                "id": str(value.pk) if value.pk is not None else None,
                "pk": value.pk,
                "__str__": str(value),
            }
        # Increment DjangoJSONEncoder._depth so _serialize_model_safely
        # respects the depth limit for prefetched relations.
        DjangoJSONEncoder._depth += 1
        try:
            model_dict = _encoder._serialize_model_safely(value)
        finally:
            DjangoJSONEncoder._depth -= 1
        return normalize_django_value(model_dict, _depth + 1)

    # Duck-typing fallback for file-like objects (must be after Model check)
    if hasattr(value, "url") and hasattr(value, "name") and not isinstance(value, type):
        if not isinstance(value, (dict, list, tuple, str)):
            if value:
                try:
                    return value.url
                except (ValueError, AttributeError):
                    return None
            return None

    # QuerySet -> list of normalized models
    if hasattr(value, "model") and hasattr(value, "__iter__"):
        return [normalize_django_value(item, _depth) for item in value]

    # Components -> rendered HTML string
    try:
        from .components.base import Component, LiveComponent

        if isinstance(value, (Component, LiveComponent)):
            return str(value)
    except ImportError:
        pass

    # Safety net: skip callables (matches encoder behavior)
    if callable(value):
        logger.debug(
            "Skipping callable %s during normalization",
            type(value).__name__,
        )
        return None

    # Final fallback
    return str(value)
