"""
djust Security Utilities

This module provides security utilities for preventing common vulnerabilities:

- **Prototype Pollution**: Use `safe_setattr()` instead of raw `setattr()` when
  setting attributes from untrusted input (e.g., user params, state restoration).

- **Log Injection**: Use `sanitize_for_log()` to clean user input before logging
  to prevent log injection attacks and terminal escape sequence injection.

- **Information Disclosure**: Use `handle_exception()` to both log and create
  error responses that respect DEBUG mode and don't leak sensitive information.

Example Usage:
    from djust.security import safe_setattr, sanitize_for_log, handle_exception

    # Safe attribute setting (blocks __proto__, __class__, etc.)
    for key, value in user_params.items():
        safe_setattr(obj, key, value)

    # Safe logging (strips control chars, truncates)
    logger.info(f"User searched for: {sanitize_for_log(user_query)}")

    # Handle exception (logs + creates safe response in one call)
    response = handle_exception(exception, error_type="event", event_name="click")
"""

from .attribute_guard import (
    safe_setattr,
    is_safe_attribute_name,
    DANGEROUS_ATTRIBUTES,
    AttributeSecurityError,
)
from .log_sanitizer import (
    sanitize_for_log,
    MAX_LOG_LENGTH,
)
from .error_handling import (
    create_safe_error_response,
    safe_error_message,
    handle_exception,
)
from .event_guard import is_safe_event_name

__all__ = [
    # Attribute guard
    "safe_setattr",
    "is_safe_attribute_name",
    "DANGEROUS_ATTRIBUTES",
    "AttributeSecurityError",
    # Log sanitizer
    "sanitize_for_log",
    "MAX_LOG_LENGTH",
    # Error handling
    "create_safe_error_response",
    "safe_error_message",
    "handle_exception",
    # Event guard
    "is_safe_event_name",
]
