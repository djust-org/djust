"""
Utility functions for WebSocket event handling.

Extracted from websocket.py to keep the consumer module focused on
the LiveViewConsumer and LiveViewRouter classes.
"""

import inspect
import logging
from typing import Callable, Dict, Any, Optional

from asgiref.sync import sync_to_async

from .config import config as djust_config
from .decorators import is_event_handler
from .rate_limit import ConnectionRateLimiter, get_rate_limit_settings, ip_tracker
from .security import is_safe_event_name, sanitize_for_log

logger = logging.getLogger(__name__)


def _safe_error(detailed_msg: str, generic_msg: str = "Event rejected") -> str:
    """Return detailed message in DEBUG mode, generic message in production."""
    try:
        from django.conf import settings

        if settings.DEBUG:
            return detailed_msg
    except Exception:
        pass  # Django not configured; fall back to generic (safe default)
    return generic_msg


def _format_handler_not_found_error(event_name: str, owner_instance) -> str:
    """
    Format helpful error message when event handler is not found.

    Provides actionable suggestions for common mistakes:
    1. Missing dj- prefix (e.g., 'click' instead of 'dj-click')
    2. Missing @event_handler decorator
    3. Missing mount() method for state variables

    Args:
        event_name: The event name that was not found
        owner_instance: The LiveView instance

    Returns:
        Formatted error message with suggestions
    """
    error_msg = f"No handler found for event: '{event_name}'"

    # Check if user forgot the 'dj-' prefix
    if not event_name.startswith("dj-"):
        suggested_name = f"dj-{event_name}"
        # Check if the prefixed name exists
        prefixed_handler = getattr(owner_instance, suggested_name, None)
        if prefixed_handler and callable(prefixed_handler):
            error_msg += (
                f"\n\n💡 Did you forget the 'dj-' prefix?"
                f"\n   Template uses: {event_name}"
                f"\n   Handler found: {suggested_name}"
                f"\n   Fix: Change @click=\"{event_name}\" to @click=\"{suggested_name}\""
            )
            return error_msg

    # Check if handler exists but is not decorated with @event_handler
    # Look for similar method names
    class_name = type(owner_instance).__name__
    similar_methods = [
        name for name in dir(owner_instance)
        if not name.startswith("_") and callable(getattr(owner_instance, name, None))
    ]

    # Try to find methods that might match (case-insensitive partial match)
    lowercase_event = event_name.lower().replace("dj-", "")
    similar = [
        name for name in similar_methods
        if name.lower().replace("dj-", "") == lowercase_event
        or name.lower().replace("dj-", "") in lowercase_event
        or lowercase_event.replace("dj-", "") in name.lower()
    ]

    if similar:
        error_msg += (
            f"\n\n💡 Found similar method(s): {similar}"
            f"\n   If one of these is your handler, make sure it's decorated with @event_handler."
            f"\n   Example:\n"
            f"       @event_handler\n"
            f"       def {similar[0]}(self):\n"
            f"           # Your code here\n"
            f"           pass"
        )

    # Check if this is a state variable access (common mount() mistake)
    if "_" not in event_name and len(event_name) < 20:
        error_msg += (
            f"\n\n⚠️  If you're trying to update state like 'self.{event_name}', "
            f"make sure it's initialized in mount():\n"
            f"   def mount(self, request, **kwargs):\n"
            f"       self.{event_name} = 0  # Initialize state here"
        )

    return error_msg


def get_handler_coerce_setting(handler: Callable) -> bool:
    """
    Get the coerce_types setting from a handler's @event_handler decorator.

    Args:
        handler: The event handler method

    Returns:
        True if type coercion should be enabled (default), False if disabled
    """
    if hasattr(handler, "_djust_decorators"):
        return handler._djust_decorators.get("event_handler", {}).get("coerce_types", True)
    return True


def _check_event_security(handler, owner_instance, event_name: str) -> Optional[str]:
    """
    Check the event_security policy for a handler.

    Returns None if allowed, or an error message string if blocked in strict mode.
    Logs a deprecation warning in warn mode for undecorated handlers.
    """
    mode = djust_config.get("event_security", "strict")
    if mode not in ("warn", "strict"):
        return None

    allowed_events = getattr(owner_instance, "_allowed_events", None)
    is_allowed = is_event_handler(handler) or (
        isinstance(allowed_events, (set, frozenset)) and event_name in allowed_events
    )
    if is_allowed:
        return None

    if mode == "strict":
        return f"Event '{event_name}' is not decorated with @event_handler or listed in _allowed_events"

    logger.warning(
        "Deprecation: handler '%s' on %s is not decorated with @event_handler. "
        "This will be blocked in strict mode.",
        event_name,
        type(owner_instance).__name__,
    )
    return None


def _ensure_handler_rate_limit(
    rate_limiter: "ConnectionRateLimiter", event_name: str, handler
) -> None:
    """Register per-handler rate limit from @rate_limit decorator metadata (once per event).

    Must be called BEFORE check_handler() so the first
    invocation is also subject to the per-handler bucket.
    """
    if event_name not in rate_limiter.handler_buckets:
        rl_settings = get_rate_limit_settings(handler)
        if rl_settings:
            rate_limiter.register_handler_limit(
                event_name, rl_settings["rate"], rl_settings["burst"]
            )


async def _validate_event_security(
    ws,
    event_name: str,
    owner_instance,
    rate_limiter: "ConnectionRateLimiter",
) -> Optional[Callable]:
    """Validate event name, handler existence, decorator allowlist, and per-handler rate limit.

    Shared by actor, component, and view paths. Returns the handler if all
    checks pass, or None after sending the appropriate error/close.
    """
    if not is_safe_event_name(event_name):
        error_msg = f"Blocked unsafe event name: {sanitize_for_log(event_name)}"
        logger.warning(error_msg)
        await ws.send_error(_safe_error(error_msg))
        return None

    handler = getattr(owner_instance, event_name, None)
    if not handler or not callable(handler):
        # Provide helpful error messages for common mistakes
        error_msg = _format_handler_not_found_error(event_name, owner_instance)
        logger.warning(error_msg)
        await ws.send_error(_safe_error(error_msg))
        return None

    security_error = _check_event_security(handler, owner_instance, event_name)
    if security_error:
        logger.warning(security_error)
        await ws.send_error(_safe_error(security_error))
        return None

    _ensure_handler_rate_limit(rate_limiter, event_name, handler)
    if not rate_limiter.check_handler(event_name):
        if rate_limiter.should_disconnect():
            client_ip = getattr(ws, "_client_ip", None)
            if client_ip:
                _rl = djust_config.get("rate_limit", {})
                cooldown = _rl.get("reconnect_cooldown", 5) if isinstance(_rl, dict) else 5
                ip_tracker.add_cooldown(client_ip, cooldown)
            await ws.close(code=4429)
            return None
        await ws.send_error("Rate limit exceeded, event dropped")
        return None

    return handler


async def _call_handler(handler: Callable, params: Optional[Dict[str, Any]] = None):
    """
    Call an event handler, handling both sync and async handlers.

    Args:
        handler: The event handler method (sync or async)
        params: Optional dictionary of parameters to pass to the handler.
            Note: Empty dict {} is treated as no params (falsy check).
            Positional args from @click="handler('value')" syntax are merged
            into params by validate_handler_params() before calling this.

    Returns:
        The result of calling the handler
    """
    if inspect.iscoroutinefunction(handler):
        # Handler is already async, call it directly
        if params:
            return await handler(**params)
        return await handler()
    else:
        # Handler is sync, wrap with sync_to_async
        if params:
            return await sync_to_async(handler)(**params)
        return await sync_to_async(handler)()
