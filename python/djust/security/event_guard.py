"""
Event name guard for WebSocket event dispatch.

Validates event names before getattr() to prevent calling dangerous
internal methods (Django View internals, LiveView lifecycle, private methods).
"""

import re
import logging

logger = logging.getLogger(__name__)

# Only allow lowercase alphanumeric + underscore, starting with a letter
_EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# Public methods that must never be callable via WebSocket events
BLOCKED_EVENT_NAMES: frozenset = frozenset(
    {
        # Django View internals
        "dispatch",
        "setup",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "trace",
        "http_method_not_allowed",
        "as_view",
        # LiveView lifecycle
        "mount",
        "render",
        "render_full_template",
        "render_with_diff",
        "get_context_data",
        "get_template",
        "get_debug_info",
        # LiveView internals
        "handle_component_event",
        "update_component",
        "stream",
        "stream_insert",
        "stream_delete",
        "stream_reset",
        # Object internals
        "update",
    }
)


def is_safe_event_name(name: str) -> bool:
    """
    Check whether an event name is safe to dispatch via WebSocket.

    Returns True only if the name:
    1. Matches ^[a-z][a-z0-9_]*$ (no leading underscore, no uppercase, no dots)
    2. Is not in the blocklist of dangerous method names

    Args:
        name: The event name received from the client.

    Returns:
        True if safe to call via getattr, False otherwise.
    """
    if not _EVENT_NAME_PATTERN.match(name):
        logger.warning("Blocked event with invalid name pattern: %s", name[:100])
        return False
    if name in BLOCKED_EVENT_NAMES:
        logger.warning("Blocked event targeting internal method: %s", name)
        return False
    return True
