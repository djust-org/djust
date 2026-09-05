"""
Serialization utilities for djust template rendering.

Prepares Django/Python values for native rendering or JSON serialization.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from django.utils.datastructures import MultiValueDict
from django.utils.functional import Promise
from typing import Any, Dict, List, Union, cast
from uuid import UUID

from django.db.models.fields.files import FieldFile

from djust.serialization import django_json_datetime

# JSON-compatible value the Rust engine accepts.
JSONValue = Union[str, int, float, bool, None, List[Any], Dict[str, Any]]


def serialize_value(
    value: Any,
    *,
    for_render: bool = False,
) -> Any:
    """
    Prepare a value for JSON or, with ``for_render=True``, native rendering.

    Handles:
    - datetime/date/time -> ``DjangoJSONEncoder.default``'s spelling (#2462)
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
    # The native render API accepts Python objects directly. JSON conversion
    # would erase the types needed by filters, comparisons, and date formatting.
    # Keep the existing JSON-oriented helper contract for other callers.
    if for_render:
        if isinstance(value, Promise):
            return str(value)
        if isinstance(value, (datetime, date, time, timedelta, Decimal, UUID)):
            return value

    if value is None:
        return None

    # datetime / date / time -> the encoder's spelling, not ``isoformat()``
    # (#2462). The THIRD sink of the same conversion: found by grepping for
    # ``isoformat()`` rather than by listing the callers already known, which
    # is the rule that keeps this kind of fix from landing on two of three
    # paths (#1646).
    if isinstance(value, (datetime, date, time)):
        return django_json_datetime(value)

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
                return cast(str, value.url)
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
                    return cast(str, value.url)
                except (ValueError, AttributeError):
                    return None
            return None

    # Django Form / BoundField — render to SafeString HTML so that
    # {{ form.field_name }} produces widget HTML. Must come before dict check.
    from djust.serialization import render_form_value

    form_result = render_form_value(value)
    if form_result is not None:
        return cast(str, form_result)

    # A `QueryDict` / `MultiValueDict` stays RAW here, and only here (#2556).
    # On this path the raw-Python sidecar is derived in Rust from this very
    # dict (`entry_sidecar` -> `build_render_sidecar`), so a rebuilt plain
    # dict would leave `{% querystring my_qd a=2 %}` with no `.urlencode()`
    # and no multi-values. The converters read the raw object the way Django
    # resolves it — LAST value per key (`djust_core::multi_value_dict_pairs`),
    # so `{{ qd.a }}` on `?a=1&a=2` is `2`, not the `['1', '2']` storage the
    # plain-dict arm used to see (PR #2596 Stage 11). The LiveView path
    # differs: `normalize_django_value` feeds JSON state, so it rebuilds the
    # dict and `rust_bridge` carries the raw object in its own sidecar.
    if isinstance(value, MultiValueDict):
        return cast(JSONValue, value)

    # Handle dict - recursively serialize
    if isinstance(value, dict):
        return {k: serialize_value(v, for_render=for_render) for k, v in value.items()}

    # Handle list/tuple - recursively serialize
    if isinstance(value, (list, tuple)):
        items = [serialize_value(item, for_render=for_render) for item in value]
        return tuple(items) if for_render and isinstance(value, tuple) else items

    # Pass through other types (str, int, float, bool, etc.)
    return cast(JSONValue, value)


def serialize_context(context: Dict[str, Any], *, for_render: bool = False) -> Dict[str, Any]:
    """
    Prepare context values for JSON or the native renderer.

    ``for_render=True`` retains Python scalar and tuple types and evaluates
    lazy translation strings. Form and file-field rendering applies in both modes.

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
    return {key: serialize_value(value, for_render=for_render) for key, value in context.items()}
