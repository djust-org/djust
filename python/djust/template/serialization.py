"""
Serialization utilities for djust template rendering.

Converts Django/Python types to JSON-compatible values for the Rust engine.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Union
from uuid import UUID

from django.db.models.fields.files import FieldFile


def serialize_value(
    value: Any,
) -> Union[str, int, float, bool, None, List[Any], Dict[str, Any]]:
    """
    Serialize a single value to a JSON-compatible type.

    Handles:
    - datetime/date/time -> ISO format strings
    - UUID -> string
    - Decimal -> float
    - FieldFile/ImageFieldFile -> URL string or None
    - dict -> recursively serialized dict
    - list/tuple -> recursively serialized list
    - Other types -> passed through (will fail at JSON encoding if not serializable)

    Args:
        value: Any Python value to serialize

    Returns:
        JSON-serializable value
    """
    if value is None:
        return None

    # Handle datetime types
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    # Handle UUID
    if isinstance(value, UUID):
        return str(value)

    # Handle Decimal
    if isinstance(value, Decimal):
        return float(value)

    # Handle Django FieldFile/ImageFieldFile
    # Use isinstance check first, then duck-typing for file-like objects with 'url'
    if isinstance(value, FieldFile):
        if value:
            try:
                return value.url
            except ValueError:
                return None
        return None

    # Duck-typing fallback for file-like objects (e.g., custom file fields, mocks)
    # Must have 'url' attribute and 'name' attribute (signature of file fields)
    # but not be a type (class) itself
    if hasattr(value, "url") and hasattr(value, "name") and not isinstance(value, type):
        # Check it's not a plain dict or list that happens to have these attrs
        if not isinstance(value, (dict, list, tuple, str)):
            if value:
                try:
                    return value.url
                except (ValueError, AttributeError):
                    return None
            return None

    # Handle dict - recursively serialize
    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}

    # Handle list/tuple - recursively serialize
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]

    # Pass through other types (str, int, float, bool, etc.)
    return value


def serialize_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Serialize all context values to ensure JSON compatibility for Rust rendering.

    This function recursively processes the context dictionary, converting
    Django/Python types that are not natively JSON-serializable into their
    string or primitive representations.

    Supported type conversions:
    - datetime.datetime -> ISO format string (e.g., "2024-06-15T14:30:45")
    - datetime.date -> ISO format string (e.g., "2024-06-15")
    - datetime.time -> ISO format string (e.g., "14:30:45")
    - Decimal -> float
    - UUID -> string
    - FieldFile/ImageFieldFile -> URL string if file exists, else None
    - Nested dicts and lists are processed recursively

    Args:
        context: The template context dictionary

    Returns:
        A new dictionary with all values serialized to JSON-compatible types

    Example:
        >>> from datetime import datetime
        >>> from decimal import Decimal
        >>> context = {
        ...     'created_at': datetime(2024, 6, 15, 14, 30),
        ...     'price': Decimal('99.99'),
        ... }
        >>> serialized = serialize_context(context)
        >>> serialized['created_at']
        '2024-06-15T14:30:00'
        >>> serialized['price']
        99.99
    """
    return {key: serialize_value(value) for key, value in context.items()}
