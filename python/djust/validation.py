"""
Event handler parameter validation utilities.

Provides runtime validation of event handler signatures including:
- Required parameter checking
- Unexpected parameter detection
- Type validation using type hints
- Automatic type coercion from string values
- Clear error message generation
"""

import inspect
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Union, get_type_hints, get_origin, get_args
from uuid import UUID

from djust.security import sanitize_for_log

logger = logging.getLogger(__name__)


def coerce_parameter_types(handler: Callable, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Coerce parameter values to match handler type hints.

    Template data-* attributes always pass values as strings. This function
    automatically converts string values to the expected types based on
    the handler's type hints.

    Args:
        handler: Event handler method with type hints
        params: Parameters from client event (typically strings from data-* attributes)

    Returns:
        New dict with coerced parameter values

    Supported coercions:
        - str -> int: "123" -> 123
        - str -> float: "3.14" -> 3.14
        - str -> bool: "true"/"1"/"yes"/"on" -> True, others -> False
        - str -> Decimal: "123.45" -> Decimal("123.45")
        - str -> UUID: "550e8400-..." -> UUID("550e8400-...")
        - str -> list: "a,b,c" -> ["a", "b", "c"]
        - str -> List[int]: "1,2,3" -> [1, 2, 3]

    Example:
        >>> def handler(self, count: int, enabled: bool = False):
        ...     pass
        >>> coerce_parameter_types(handler, {"count": "42", "enabled": "true"})
        {"count": 42, "enabled": True}
    """
    try:
        type_hints = get_type_hints(handler)
    except Exception:
        # If type hints can't be extracted, return params unchanged
        return params

    coerced = {}

    for name, value in params.items():
        expected_type = type_hints.get(name)

        # Skip if no type hint or value is not a string
        if expected_type is None:
            coerced[name] = value
            continue

        # Handle Optional[T] and Union types
        origin = get_origin(expected_type)
        if origin is Union:
            # Get non-None types from Union (handles Optional[T])
            # For Union types like Union[int, str], coerce to the first non-None type.
            # This means Union[int, str] will try int coercion first.
            args = [arg for arg in get_args(expected_type) if arg is not type(None)]
            if args:
                expected_type = args[0]
                origin = get_origin(expected_type)

        # Only coerce if value is a string
        if not isinstance(value, str):
            coerced[name] = value
            continue

        try:
            coerced[name] = _coerce_value(value, expected_type, origin)
        except (ValueError, TypeError, InvalidOperation):
            # If coercion fails, keep original value
            # Type validation will catch the error with a helpful message
            coerced[name] = value

    return coerced


def _coerce_value(value: str, expected_type: Any, origin: Any) -> Any:
    """
    Coerce a string value to the expected type.

    Args:
        value: String value to coerce
        expected_type: Target type from type hints
        origin: Origin type for generics (e.g., list for List[int])

    Returns:
        Coerced value

    Raises:
        ValueError: If coercion fails
    """
    # Handle List types
    if origin is list or expected_type is list:
        if not value:
            return []
        items = [item.strip() for item in value.split(",")]
        # Check for typed list like List[int]
        args = get_args(expected_type) if origin is list else None
        if args:
            item_type = args[0]
            return [_coerce_single_value(item, item_type) for item in items]
        return items

    return _coerce_single_value(value, expected_type)


def _coerce_single_value(value: str, expected_type: type) -> Any:
    """
    Coerce a single string value to the expected type.

    Args:
        value: String value to coerce
        expected_type: Target type

    Returns:
        Coerced value

    Raises:
        ValueError: If coercion fails
    """
    if not isinstance(expected_type, type):
        return value

    if expected_type is int:
        return int(value) if value else 0

    if expected_type is float:
        return float(value) if value else 0.0

    if expected_type is bool:
        return value.lower() in ("true", "1", "yes", "on")

    if expected_type is Decimal:
        return Decimal(value) if value else Decimal("0")

    if expected_type is UUID:
        return UUID(value)

    if expected_type is str:
        return value

    # Unknown type - return original
    return value


def validate_handler_params(
    handler: Callable,
    params: Dict[str, Any],
    event_name: str,
    coerce: bool = True,
    positional_args: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Validate event parameters match handler signature.

    Automatically coerces string parameters to expected types based on
    type hints (enabled by default). Template data-* attributes always
    pass strings, so coercion converts them to int, float, bool, etc.

    Positional arguments from inline handler syntax (e.g., @click="handler('value')")
    are mapped to named parameters based on their position in the handler signature.

    Args:
        handler: Event handler method to validate against
        params: Parameters provided by client event
        event_name: Name of the event (for error messages)
        coerce: Whether to coerce string values to expected types (default: True)
        positional_args: Optional list of positional arguments from inline handler
            syntax (e.g., ['value'] from @click="handler('value')")

    Returns:
        Dict with validation result:
        {
            "valid": bool,
            "error": Optional[str],
            "expected": List[str],  # Expected parameter names
            "provided": List[str],  # Provided parameter names
            "type_errors": Optional[List[Dict]],  # Type mismatch details
            "coerced_params": Dict[str, Any],  # Parameters after coercion
        }

    Example:
        >>> def my_handler(self, value: str, count: int = 0):
        ...     pass
        >>> result = validate_handler_params(my_handler, {"value": "test", "count": "5"}, "my_event")
        >>> assert result["valid"] is True
        >>> assert result["coerced_params"]["count"] == 5  # String coerced to int
        >>> result = validate_handler_params(my_handler, {}, "my_event")
        >>> assert result["valid"] is False
        >>> assert "missing required parameters" in result["error"]
        >>> # Positional args are mapped to named parameters
        >>> result = validate_handler_params(my_handler, {}, "my_event", positional_args=["hello"])
        >>> assert result["valid"] is True
        >>> assert result["coerced_params"]["value"] == "hello"
    """
    # Map positional arguments to named parameters based on handler signature
    sig = inspect.signature(handler)

    # Build list of parameter names (excluding self, *args, **kwargs)
    param_names = []
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        param_names.append(name)

    # Merge positional args into params (positional args take precedence)
    merged_params = params.copy()
    if positional_args:
        for i, arg in enumerate(positional_args):
            if i < len(param_names):
                param_name = param_names[i]
                if param_name in params and logger.isEnabledFor(logging.DEBUG):
                    # Sanitize values for safe logging to prevent log injection
                    old_val = sanitize_for_log(str(params[param_name]), max_length=100)
                    new_val = sanitize_for_log(str(arg), max_length=100)
                    logger.debug(
                        "Positional arg overrides data-* attribute for %r: %s -> %s",
                        param_name,
                        old_val,
                        new_val,
                    )
                merged_params[param_name] = arg

    # Coerce parameters before validation
    coerced_params = coerce_parameter_types(handler, merged_params) if coerce else merged_params

    # Extract parameter information
    required_params = []
    optional_params = []
    accepted_params = []
    has_var_keyword = False

    for name, param in sig.parameters.items():
        # Skip 'self' parameter
        if name == "self":
            continue

        # Check for **kwargs
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            has_var_keyword = True
            continue

        # Skip *args
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue

        accepted_params.append(name)

        if param.default == inspect.Parameter.empty:
            required_params.append(name)
        else:
            optional_params.append(name)

    # Check for missing required parameters
    missing = [p for p in required_params if p not in coerced_params]
    if missing:
        return {
            "valid": False,
            "error": f"Handler '{event_name}' missing required parameters: {missing}",
            "expected": accepted_params,
            "provided": list(coerced_params.keys()),
            "type_errors": None,
            "coerced_params": coerced_params,
        }

    # Check for unexpected parameters (if no **kwargs)
    if not has_var_keyword:
        unexpected = [p for p in coerced_params if p not in accepted_params]
        if unexpected:
            return {
                "valid": False,
                "error": f"Handler '{event_name}' received unexpected parameters: {unexpected}. Expected: {accepted_params}",
                "expected": accepted_params,
                "provided": list(coerced_params.keys()),
                "type_errors": None,
                "coerced_params": coerced_params,
            }

    # Validate parameter types using type hints (on coerced params)
    type_errors = validate_parameter_types(handler, coerced_params)
    if type_errors:
        error_msg = f"Handler '{event_name}' received wrong parameter types:\n"
        for err in type_errors:
            # Truncate repr safely to avoid cutting mid-escape sequence
            val_repr = repr(err.get("value", "?"))
            if len(val_repr) > 50:
                val_repr = val_repr[:47] + "..."

            error_msg += f"  - {err['param']}: expected {err['expected']}, got {err['actual']} ({val_repr})\n"

            # Add detailed hint with actionable fix suggestions
            hint = format_type_error_hint(
                param=err["param"],
                expected=err["expected"],
                actual=err["actual"],
                value=err.get("value"),
                coercion_attempted=coerce,
            )
            error_msg += hint + "\n\n"

        return {
            "valid": False,
            "error": error_msg.strip(),
            "expected": accepted_params,
            "provided": list(coerced_params.keys()),
            "type_errors": type_errors,
            "coerced_params": coerced_params,
        }

    return {
        "valid": True,
        "error": None,
        "expected": accepted_params,
        "provided": list(coerced_params.keys()),
        "type_errors": None,
        "coerced_params": coerced_params,
    }


def validate_parameter_types(
    handler: Callable, params: Dict[str, Any]
) -> Optional[List[Dict[str, str]]]:
    """
    Validate parameter types against type hints.

    Args:
        handler: Event handler method
        params: Parameters provided by client

    Returns:
        List of type errors, or None if all types valid
        Each error dict contains: {param, expected, actual}

    Example:
        >>> def handler(self, count: int):
        ...     pass
        >>> errors = validate_parameter_types(handler, {"count": "not_an_int"})
        >>> assert errors is not None
        >>> assert errors[0]["param"] == "count"
        >>> assert errors[0]["expected"] == "int"
        >>> assert errors[0]["actual"] == "str"
    """
    try:
        type_hints = get_type_hints(handler)
    except Exception:
        # If type hints can't be extracted, skip type validation
        return None

    errors = []

    for param_name, param_value in params.items():
        if param_name not in type_hints:
            continue

        expected_type = type_hints[param_name]

        # Skip complex types (Union, Optional, etc.) for now
        if not isinstance(expected_type, type):
            continue

        # Check type match
        if not isinstance(param_value, expected_type):
            errors.append(
                {
                    "param": param_name,
                    "expected": expected_type.__name__,
                    "actual": type(param_value).__name__,
                    "value": param_value,
                }
            )

    return errors if errors else None


def get_handler_signature_info(handler: Callable) -> Dict[str, Any]:
    """
    Extract comprehensive signature information from handler.

    Used by debug panel and @event_handler decorator.

    Args:
        handler: Event handler method

    Returns:
        Dict containing:
        - params: List of parameter dicts with name, type, required, default
        - description: Handler docstring
        - accepts_kwargs: Whether handler accepts **kwargs

    Example:
        >>> def handler(self, value: str = "", count: int = 0, **kwargs):
        ...     '''Search items'''
        ...     pass
        >>> info = get_handler_signature_info(handler)
        >>> assert len(info["params"]) == 2
        >>> assert info["params"][0]["name"] == "value"
        >>> assert info["params"][0]["type"] == "str"
        >>> assert info["params"][0]["required"] is False
        >>> assert info["description"] == "Search items"
        >>> assert info["accepts_kwargs"] is True
    """
    sig = inspect.signature(handler)

    try:
        type_hints = get_type_hints(handler)
    except Exception:
        type_hints = {}

    params = []
    accepts_kwargs = False

    for name, param in sig.parameters.items():
        if name == "self":
            continue

        if param.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_kwargs = True
            continue

        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue

        param_info = {
            "name": name,
            "type": type_hints.get(name, Any).__name__ if name in type_hints else "Any",
            "required": param.default == inspect.Parameter.empty,
            "default": str(param.default) if param.default != inspect.Parameter.empty else None,
        }

        params.append(param_info)

    return {
        "params": params,
        "description": inspect.getdoc(handler) or "",
        "accepts_kwargs": accepts_kwargs,
    }


def format_type_error_hint(
    param: str,
    expected: str,
    actual: str,
    value: Any,
    coercion_attempted: bool = True,
) -> str:
    """
    Format a validation error with actionable hints.

    Provides clear guidance on how to fix type mismatches,
    with specific suggestions based on the mismatch type.

    Args:
        param: Parameter name that failed validation
        expected: Expected type name
        actual: Actual type name
        value: The actual value received
        coercion_attempted: Whether type coercion was tried

    Returns:
        Formatted hint string
    """
    # Build appropriate hint based on the type mismatch
    if expected == "int" and actual == "str":
        return _get_string_to_int_hint(param, value, coercion_attempted)
    elif expected == "float" and actual == "str":
        return _get_string_to_float_hint(param, value, coercion_attempted)
    elif expected == "bool" and actual == "str":
        return _get_string_to_bool_hint(param, value, coercion_attempted)
    elif expected == "list" and actual == "str":
        return _get_string_to_list_hint(param, value, coercion_attempted)
    elif expected == "Decimal" and actual == "str":
        return _get_string_to_decimal_hint(param, value, coercion_attempted)
    else:
        # Generic hint
        return f"    Hint: Check that the value is valid for type '{expected}'."


def _get_string_to_int_hint(param: str, value: Any, coercion_attempted: bool) -> str:
    """Generate hint for string->int coercion failure."""
    hint = "    This is a common issue! Template data-* attributes are always strings.\n\n"

    if coercion_attempted:
        hint += f"    The value '{value}' could not be converted to int.\n"
        hint += "    Check that it's a valid integer (e.g., '42', not '42.5' or 'abc').\n\n"

    hint += "    Quick fixes:\n"
    hint += "      1. Use typed template attribute:\n"
    hint += f"         data-{param.replace('_', '-')}:int=\"{{{{ value }}}}\"\n"
    hint += "      2. Ensure the value is numeric in your template:\n"
    hint += f"         data-{param.replace('_', '-')}=\"{{{{ item.id|default:0 }}}}\""

    return hint


def _get_string_to_float_hint(param: str, value: Any, coercion_attempted: bool) -> str:
    """Generate hint for string->float coercion failure."""
    hint = "    Template data-* attributes are always strings.\n\n"

    if coercion_attempted:
        hint += f"    The value '{value}' could not be converted to float.\n"
        hint += "    Check that it's a valid number (e.g., '3.14' or '42').\n\n"

    hint += "    Quick fixes:\n"
    hint += "      1. Use typed template attribute:\n"
    hint += f"         data-{param.replace('_', '-')}:float=\"{{{{ value }}}}\"\n"
    hint += "      2. Ensure the value is numeric in your template:\n"
    hint += f"         data-{param.replace('_', '-')}=\"{{{{ item.price|default:0.0 }}}}\""

    return hint


def _get_string_to_bool_hint(param: str, value: Any, coercion_attempted: bool) -> str:
    """Generate hint for string->bool coercion failure."""
    hint = "    Template data-* attributes pass 'true'/'false' as strings.\n\n"

    if coercion_attempted:
        hint += f"    The value '{value}' was treated as False.\n"
        hint += "    Valid true values: 'true', '1', 'yes', 'on'\n\n"

    hint += "    Quick fixes:\n"
    hint += "      1. Use typed template attribute:\n"
    hint += f"         data-{param.replace('_', '-')}:bool=\"{{{{ value|yesno:'true,false' }}}}\"\n"
    hint += "      2. Use proper boolean string:\n"
    hint += f"         data-{param.replace('_', '-')}=\"{{{{ item.enabled|yesno:'true,false' }}}}\""

    return hint


def _get_string_to_list_hint(param: str, value: Any, coercion_attempted: bool) -> str:
    """Generate hint for string->list coercion failure."""
    hint = "    Lists should be passed as JSON or comma-separated strings.\n\n"

    hint += "    Quick fixes:\n"
    hint += "      1. Use JSON typed attribute:\n"
    hint += f"         data-{param.replace('_', '-')}:json=\"{{{{ values|safe }}}}\"\n"
    hint += "      2. Use comma-separated list:\n"
    hint += f"         data-{param.replace('_', '-')}:list=\"a,b,c\"\n"
    hint += "      3. Pass as JSON string:\n"
    hint += f"         data-{param.replace('_', '-')}='[\"a\", \"b\", \"c\"]'"

    return hint


def _get_string_to_decimal_hint(param: str, value: Any, coercion_attempted: bool) -> str:
    """Generate hint for string->Decimal coercion failure."""
    hint = "    Decimal values require valid numeric strings.\n\n"

    if coercion_attempted:
        hint += f"    The value '{value}' could not be converted to Decimal.\n\n"

    hint += "    Quick fixes:\n"
    hint += "      1. Ensure the value is a valid decimal:\n"
    hint += f"         data-{param.replace('_', '-')}=\"{{{{ item.price }}}}\"\n"
    hint += "      2. Use a default value:\n"
    hint += f"         data-{param.replace('_', '-')}=\"{{{{ item.price|default:'0.00' }}}}\""

    return hint
