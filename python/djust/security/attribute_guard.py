"""
Attribute Guard - Protection against prototype pollution and attribute injection

This module provides utilities for safely setting object attributes from
untrusted input, preventing attacks that could modify internal Python
object structure.

Security Considerations:
    - Blocks dunder attributes (__proto__, __class__, __init__, etc.)
    - Blocks private attributes (_* by default, configurable)
    - Validates attribute names contain only safe characters
    - Raises SecurityError for blocked attributes (fail-closed)

Usage:
    from djust.security import safe_setattr

    # Instead of raw setattr:
    # setattr(obj, key, value)  # DANGEROUS with untrusted keys

    # Use safe_setattr:
    safe_setattr(obj, key, value)  # Validates key before setting
"""

import re
from typing import Any, Set, Optional

# Attributes that should never be settable from untrusted input
DANGEROUS_ATTRIBUTES: Set[str] = frozenset(
    {
        # Python special attributes
        "__class__",
        "__bases__",
        "__mro__",
        "__subclasses__",
        "__init__",
        "__new__",
        "__del__",
        "__dict__",
        "__slots__",
        "__module__",
        "__name__",
        "__qualname__",
        "__doc__",
        "__annotations__",
        "__wrapped__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
        "__getattribute__",
        "__reduce__",
        "__reduce_ex__",
        "__getstate__",
        "__setstate__",
        "__getnewargs__",
        "__getnewargs_ex__",
        # Prototype pollution vectors (from JavaScript, but block anyway)
        "__proto__",
        "prototype",
        "constructor",
        # Code execution vectors
        "__call__",
        "__code__",
        "__globals__",
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        # Descriptor protocol
        "__get__",
        "__set__",
        "__delete__",
        "__set_name__",
        # Context managers
        "__enter__",
        "__exit__",
        "__aenter__",
        "__aexit__",
        # Iterator protocol
        "__iter__",
        "__next__",
        "__aiter__",
        "__anext__",
        # Comparison and hashing
        "__eq__",
        "__ne__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__hash__",
        "__bool__",
        # Numeric operations
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
        "__and__",
        "__or__",
        "__xor__",
        "__neg__",
        "__pos__",
        "__abs__",
        "__invert__",
        # Container operations
        "__len__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__contains__",
        # String representation
        "__repr__",
        "__str__",
        "__format__",
        "__bytes__",
        # Pickle support
        "__copy__",
        "__deepcopy__",
    }
)

# Regex for valid attribute names (alphanumeric + underscore, not starting with digit)
SAFE_ATTRIBUTE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class AttributeSecurityError(Exception):
    """Raised when an unsafe attribute name is detected."""

    pass


def is_safe_attribute_name(
    name: str,
    allow_private: bool = False,
    additional_blocked: Optional[Set[str]] = None,
) -> bool:
    """
    Check if an attribute name is safe to set from untrusted input.

    Args:
        name: The attribute name to check.
        allow_private: If True, allow single-underscore prefixed names.
                      Defaults to False for security.
        additional_blocked: Optional set of additional attribute names to block.

    Returns:
        True if the attribute name is safe, False otherwise.

    Examples:
        >>> is_safe_attribute_name("count")
        True
        >>> is_safe_attribute_name("__class__")
        False
        >>> is_safe_attribute_name("_private")
        False
        >>> is_safe_attribute_name("_private", allow_private=True)
        True
    """
    if not isinstance(name, str):
        return False

    # Check for empty string
    if not name:
        return False

    # Check against dangerous attributes
    if name in DANGEROUS_ATTRIBUTES:
        return False

    # Check additional blocked list
    if additional_blocked and name in additional_blocked:
        return False

    # Check for dunder attributes (anything starting and ending with __)
    if name.startswith("__") and name.endswith("__"):
        return False

    # Check for private attributes (single underscore prefix)
    if not allow_private and name.startswith("_"):
        return False

    # Validate attribute name format (prevent injection via special chars)
    if not SAFE_ATTRIBUTE_PATTERN.match(name):
        return False

    return True


def safe_setattr(
    obj: Any,
    name: str,
    value: Any,
    allow_private: bool = False,
    additional_blocked: Optional[Set[str]] = None,
    raise_on_blocked: bool = False,
) -> bool:
    """
    Safely set an attribute on an object, blocking dangerous attribute names.

    This function should be used instead of raw `setattr()` when the attribute
    name comes from untrusted input (e.g., user parameters, state restoration,
    deserialized data).

    Args:
        obj: The object to set the attribute on.
        name: The attribute name.
        value: The value to set.
        allow_private: If True, allow single-underscore prefixed names.
        additional_blocked: Optional set of additional attribute names to block.
        raise_on_blocked: If True, raise AttributeSecurityError for blocked
                         attributes instead of silently skipping.

    Returns:
        True if the attribute was set, False if it was blocked.

    Raises:
        AttributeSecurityError: If raise_on_blocked=True and attribute is blocked.

    Examples:
        >>> class MyObj:
        ...     pass
        >>> obj = MyObj()
        >>> safe_setattr(obj, "count", 5)
        True
        >>> obj.count
        5
        >>> safe_setattr(obj, "__class__", object)  # Blocked
        False
        >>> safe_setattr(obj, "__class__", object, raise_on_blocked=True)
        Traceback (most recent call last):
            ...
        AttributeSecurityError: Blocked dangerous attribute: __class__
    """
    if not is_safe_attribute_name(
        name,
        allow_private=allow_private,
        additional_blocked=additional_blocked,
    ):
        if raise_on_blocked:
            raise AttributeSecurityError(f"Blocked dangerous attribute: {name}")
        return False

    try:
        setattr(obj, name, value)
        return True
    except (AttributeError, TypeError):
        # Skip read-only properties or type errors
        return False
