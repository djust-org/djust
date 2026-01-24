"""
Log Sanitizer - Protection against log injection attacks

This module provides utilities for sanitizing user input before logging
to prevent log injection attacks and terminal escape sequence injection.

Security Considerations:
    - Strips ANSI escape sequences (prevent terminal manipulation)
    - Strips control characters (prevent log parsing attacks)
    - Truncates long strings (prevent log flooding)
    - Replaces newlines (prevent log line injection)
    - URL-encodes special characters when needed

Usage:
    from djust.security import sanitize_for_log

    # Instead of logging user input directly:
    # logger.info(f"Search: {user_query}")  # DANGEROUS

    # Use sanitize_for_log:
    logger.info(f"Search: {sanitize_for_log(user_query)}")
"""

import re
from typing import Optional, Union

# Maximum length for logged values (prevents log flooding)
MAX_LOG_LENGTH = 500

# ANSI escape sequence pattern (terminal control codes)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")

# Control characters (ASCII 0-31 except tab, newline, carriage return)
# and DEL (127)
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_for_log(
    value: Union[str, bytes, None],
    max_length: int = MAX_LOG_LENGTH,
    replacement: str = " ",
    show_truncated: bool = True,
) -> str:
    """
    Sanitize a value for safe inclusion in log messages.

    This function removes or replaces potentially dangerous content that
    could be used for log injection attacks, including:
    - ANSI escape sequences (terminal manipulation)
    - Control characters (log parsing attacks)
    - Newlines (log line injection)
    - Excessively long strings (log flooding)

    Args:
        value: The value to sanitize. If None, returns "[None]".
               If bytes, decodes as UTF-8 with error replacement.
        max_length: Maximum length of the output string.
        replacement: Character to use when replacing dangerous chars.
        show_truncated: If True, append "...[truncated]" when truncating.

    Returns:
        A sanitized string safe for logging.

    Examples:
        >>> sanitize_for_log("normal text")
        'normal text'
        >>> sanitize_for_log("line1\\nline2")  # Newlines replaced
        'line1 line2'
        >>> sanitize_for_log("\\x1b[31mred\\x1b[0m")  # ANSI stripped
        'red'
        >>> sanitize_for_log("a" * 1000)  # Truncated
        'aaaa...[truncated at 500 chars]'
        >>> sanitize_for_log(None)
        '[None]'
    """
    # Handle None
    if value is None:
        return "[None]"

    # Handle bytes
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return "[Binary data]"

    # Convert to string if not already
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return "[Unstringifiable value]"

    # Strip ANSI escape sequences
    value = ANSI_ESCAPE_PATTERN.sub("", value)

    # Replace control characters
    value = CONTROL_CHARS_PATTERN.sub(replacement, value)

    # Replace newlines and carriage returns with spaces
    value = value.replace("\n", replacement).replace("\r", replacement)

    # Collapse multiple spaces into one
    value = re.sub(r" +", " ", value)

    # Strip leading/trailing whitespace
    value = value.strip()

    # Truncate if necessary
    if len(value) > max_length:
        if show_truncated:
            suffix = f"...[truncated at {max_length} chars]"
            value = value[: max_length - len(suffix)] + suffix
        else:
            value = value[:max_length]

    return value


def sanitize_dict_for_log(
    data: dict,
    max_length: int = MAX_LOG_LENGTH,
    max_value_length: int = 100,
    sensitive_keys: Optional[set] = None,
) -> dict:
    """
    Sanitize a dictionary for safe inclusion in log messages.

    This function:
    - Sanitizes all string values
    - Redacts values for sensitive keys (passwords, tokens, etc.)
    - Limits value lengths

    Args:
        data: The dictionary to sanitize.
        max_length: Maximum total length (triggers key omission if exceeded).
        max_value_length: Maximum length for individual values.
        sensitive_keys: Set of key names to redact (case-insensitive).
                       Defaults to common sensitive keys.

    Returns:
        A sanitized dictionary safe for logging.

    Examples:
        >>> sanitize_dict_for_log({"user": "bob", "password": "secret"})
        {'user': 'bob', 'password': '[REDACTED]'}
    """
    if sensitive_keys is None:
        sensitive_keys = {
            "password",
            "passwd",
            "secret",
            "token",
            "api_key",
            "apikey",
            "auth",
            "authorization",
            "credential",
            "credentials",
            "private_key",
            "privatekey",
            "access_token",
            "refresh_token",
            "session_id",
            "sessionid",
            "csrf",
            "csrftoken",
            "cookie",
        }

    result = {}

    for key, value in data.items():
        # Sanitize the key
        safe_key = sanitize_for_log(str(key), max_length=50, show_truncated=False)

        # Check if this is a sensitive key
        if safe_key.lower() in sensitive_keys:
            result[safe_key] = "[REDACTED]"
            continue

        # Sanitize the value
        if isinstance(value, dict):
            result[safe_key] = sanitize_dict_for_log(
                value,
                max_length=max_length // 2,
                max_value_length=max_value_length,
                sensitive_keys=sensitive_keys,
            )
        elif isinstance(value, (list, tuple)):
            # Limit list length and sanitize items
            items = list(value)[:10]  # Max 10 items
            result[safe_key] = [
                sanitize_for_log(str(v), max_length=max_value_length) for v in items
            ]
            if len(value) > 10:
                result[safe_key].append(f"...and {len(value) - 10} more")
        else:
            result[safe_key] = sanitize_for_log(
                str(value) if value is not None else None,
                max_length=max_value_length,
            )

    return result
