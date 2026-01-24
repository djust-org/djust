"""
Error Handling - Safe error response generation

This module provides utilities for creating error responses that respect
DEBUG mode and don't leak sensitive information in production.

Security Considerations:
    - Never expose stack traces in production (DEBUG=False)
    - Never expose internal module/class names in production
    - Never include user parameters in error responses (data leakage)
    - Provide generic messages in production, detailed in development

Usage:
    from djust.security import create_safe_error_response

    try:
        # ... some operation
    except Exception as e:
        response = create_safe_error_response(
            exception=e,
            event_name="click",
            debug_mode=settings.DEBUG,
        )
        await self.send_json(response)
"""

import traceback
from typing import Any, Dict, Optional


# Generic error messages for production (no information leakage)
GENERIC_ERROR_MESSAGES = {
    "default": "An error occurred. Please try again.",
    "mount": "Failed to load view. Please refresh the page.",
    "event": "An error occurred processing your request.",
    "render": "Failed to display content. Please refresh the page.",
    "validation": "Invalid input. Please check your data.",
    "not_found": "The requested resource was not found.",
    "permission": "You don't have permission for this action.",
}


def safe_error_message(
    exception: Exception,
    error_type: str = "default",
    debug_mode: bool = False,
) -> str:
    """
    Generate a safe error message based on DEBUG mode.

    In debug mode, includes exception type and message.
    In production, returns a generic message.

    Args:
        exception: The exception that occurred.
        error_type: Type of error for generic message selection.
        debug_mode: Whether DEBUG mode is enabled.

    Returns:
        A safe error message string.

    Examples:
        >>> class TestError(Exception): pass
        >>> e = TestError("sensitive details")
        >>> safe_error_message(e, debug_mode=True)
        'TestError: sensitive details'
        >>> safe_error_message(e, debug_mode=False)
        'An error occurred. Please try again.'
    """
    if debug_mode:
        return f"{type(exception).__name__}: {str(exception)}"
    else:
        return GENERIC_ERROR_MESSAGES.get(
            error_type,
            GENERIC_ERROR_MESSAGES["default"]
        )


def create_safe_error_response(
    exception: Exception,
    error_type: str = "default",
    event_name: Optional[str] = None,
    view_class: Optional[str] = None,
    debug_mode: Optional[bool] = None,
    include_traceback: bool = True,
) -> Dict[str, Any]:
    """
    Create a safe error response dictionary for WebSocket/HTTP responses.

    This function generates appropriate error responses based on DEBUG mode:
    - In DEBUG mode: Includes detailed error info for development
    - In production: Returns minimal, generic responses

    SECURITY: This function NEVER includes:
    - User parameters (prevents data reflection/leakage)
    - Internal paths or module names (in production)
    - Stack traces (in production)

    Args:
        exception: The exception that occurred.
        error_type: Type of error ("mount", "event", "render", etc.).
        event_name: Optional event name for context (DEBUG only).
        view_class: Optional view class name for context (DEBUG only).
        debug_mode: Override for Django DEBUG setting. If None, reads
                   from django.conf.settings.DEBUG.
        include_traceback: Whether to include traceback in DEBUG mode.

    Returns:
        A dictionary suitable for JSON response.

    Examples:
        >>> e = ValueError("test error")
        >>> resp = create_safe_error_response(e, debug_mode=False)
        >>> resp
        {'type': 'error', 'error': 'An error occurred. Please try again.'}
        >>> resp = create_safe_error_response(
        ...     e, error_type="event", event_name="click", debug_mode=True
        ... )
        >>> 'traceback' in resp
        True
        >>> 'event' in resp
        True
    """
    # Determine DEBUG mode
    if debug_mode is None:
        try:
            from django.conf import settings
            debug_mode = getattr(settings, "DEBUG", False)
        except Exception:
            # Django not configured, default to safe mode
            debug_mode = False

    # Base response
    response: Dict[str, Any] = {
        "type": "error",
    }

    if debug_mode:
        # Development mode: include details for debugging
        error_parts = []

        if view_class:
            error_parts.append(f"{view_class}")
        if event_name:
            error_parts.append(f".{event_name}()")

        if error_parts:
            prefix = "Error in " + "".join(error_parts) + ": "
        else:
            prefix = ""

        response["error"] = f"{prefix}{type(exception).__name__}: {str(exception)}"

        # Include traceback for debugging
        if include_traceback:
            response["traceback"] = traceback.format_exc()

        # Include event name for context
        if event_name:
            response["event"] = event_name

        # NOTE: We intentionally do NOT include params even in DEBUG mode
        # because it could leak sensitive user data in logs/responses.
        # Developers should use the debug panel for inspection instead.

    else:
        # Production mode: generic message only
        response["error"] = GENERIC_ERROR_MESSAGES.get(
            error_type,
            GENERIC_ERROR_MESSAGES["default"]
        )

    return response


def log_exception_safely(
    logger,
    exception: Exception,
    message: str,
    event_name: Optional[str] = None,
    view_class: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log an exception with sanitized context.

    This function logs exceptions in a way that:
    - Always includes exception info for debugging
    - Sanitizes any user-provided data in extra
    - Provides consistent log format

    Args:
        logger: Logger instance to use.
        exception: The exception to log.
        message: Log message prefix.
        event_name: Optional event name for context.
        view_class: Optional view class name for context.
        extra: Optional extra data (will be sanitized).

    Examples:
        >>> import logging
        >>> logger = logging.getLogger(__name__)
        >>> e = ValueError("test")
        >>> log_exception_safely(logger, e, "Failed to process")
    """
    from .log_sanitizer import sanitize_dict_for_log

    # Build context string
    context_parts = []
    if view_class:
        context_parts.append(f"view={view_class}")
    if event_name:
        context_parts.append(f"event={event_name}")

    context = f" ({', '.join(context_parts)})" if context_parts else ""

    # Sanitize extra data if provided
    safe_extra = None
    if extra:
        safe_extra = sanitize_dict_for_log(extra)

    # Log with exception info
    if safe_extra:
        logger.error(
            f"{message}{context}: {type(exception).__name__}",
            exc_info=True,
            extra={"sanitized_context": safe_extra},
        )
    else:
        logger.error(
            f"{message}{context}: {type(exception).__name__}",
            exc_info=True,
        )
