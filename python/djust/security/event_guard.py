"""
Event name guard for WebSocket event dispatch.

Validates event name format before getattr() to prevent calling private
or malformed method names. The @event_handler decorator allowlist (event_security
config) is the primary access control — this guard is a fast first filter.
"""

import re
import logging

from .log_sanitizer import sanitize_for_log

logger = logging.getLogger(__name__)

# Only allow lowercase alphanumeric + underscore, starting with a letter.
# This blocks: _private, __dunder__, CamelCase, dots, dashes, spaces, empty strings.
_EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def is_safe_event_name(name: str) -> bool:
    """
    Check whether an event name has a safe format for WebSocket dispatch.

    This is a fast syntactic check only — it blocks obviously dangerous
    names (private methods, dunders, malformed strings). The real access
    control is the event_security decorator allowlist in websocket.py.

    Args:
        name: The event name received from the client.

    Returns:
        True if the name format is valid, False otherwise.
    """
    if not _EVENT_NAME_PATTERN.match(name):
        logger.warning("Blocked event with invalid name pattern: %s", sanitize_for_log(name))
        return False
    return True
