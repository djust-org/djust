"""
Event handler parameter validation utilities.

Provides runtime validation of event handler signatures including:
- Required parameter checking
- Unexpected parameter detection
- Type validation using type hints
- Clear error message generation
"""

import inspect
from typing import Any, Callable, Dict, List, Optional, get_type_hints


def validate_handler_params(
    handler: Callable, params: Dict[str, Any], event_name: str
) -> Dict[str, Any]:
    """
    Validate event parameters match handler signature.

    Args:
        handler: Event handler method to validate against
        params: Parameters provided by client event
        event_name: Name of the event (for error messages)

    Returns:
        Dict with validation result:
        {
            "valid": bool,
            "error": Optional[str],
            "expected": List[str],  # Expected parameter names
            "provided": List[str],  # Provided parameter names
            "type_errors": Optional[List[Dict]]  # Type mismatch details
        }

    Example:
        >>> def my_handler(self, value: str, count: int = 0):
        ...     pass
        >>> result = validate_handler_params(my_handler, {"value": "test"}, "my_event")
        >>> assert result["valid"] is True
        >>> result = validate_handler_params(my_handler, {}, "my_event")
        >>> assert result["valid"] is False
        >>> assert "missing required parameters" in result["error"]
    """
    sig = inspect.signature(handler)

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
    missing = [p for p in required_params if p not in params]
    if missing:
        return {
            "valid": False,
            "error": f"Handler '{event_name}' missing required parameters: {missing}",
            "expected": accepted_params,
            "provided": list(params.keys()),
            "type_errors": None,
        }

    # Check for unexpected parameters (if no **kwargs)
    if not has_var_keyword:
        unexpected = [p for p in params if p not in accepted_params]
        if unexpected:
            return {
                "valid": False,
                "error": f"Handler '{event_name}' received unexpected parameters: {unexpected}. Expected: {accepted_params}",
                "expected": accepted_params,
                "provided": list(params.keys()),
                "type_errors": None,
            }

    # Validate parameter types using type hints
    type_errors = validate_parameter_types(handler, params)
    if type_errors:
        error_msg = f"Handler '{event_name}' received wrong parameter types:\n"
        for err in type_errors:
            error_msg += f"  - {err['param']}: expected {err['expected']}, got {err['actual']}\n"

        return {
            "valid": False,
            "error": error_msg.strip(),
            "expected": accepted_params,
            "provided": list(params.keys()),
            "type_errors": type_errors,
        }

    return {
        "valid": True,
        "error": None,
        "expected": accepted_params,
        "provided": list(params.keys()),
        "type_errors": None,
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
