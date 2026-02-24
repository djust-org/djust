"""
Utility functions for WebSocket event handling.

Extracted from websocket.py to keep the consumer module focused on
the LiveViewConsumer and LiveViewRouter classes.
"""

import difflib
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


def _format_handler_not_found_error(owner_instance, event_name: str) -> str:
    """Build an actionable error message when no handler is found for an event.

    In DEBUG mode, suggests typo corrections, checks for private-method
    collisions, and lists available @event_handler methods.
    """
    base_msg = f"No handler found for event: {event_name}"

    try:
        from django.conf import settings

        if not settings.DEBUG:
            return base_msg
    except Exception:
        return base_msg

    cls = type(owner_instance)
    hints = []

    # 1. Typo detection — suggest similar public method names
    public_methods = [
        name
        for name in dir(owner_instance)
        if not name.startswith("_") and callable(getattr(owner_instance, name, None))
    ]
    close = difflib.get_close_matches(event_name, public_methods, n=3, cutoff=0.6)
    if close:
        hints.append(f"  Did you mean: {', '.join(close)}?")

    # 2. Private-method collision — method exists with underscore prefix
    if hasattr(owner_instance, f"_{event_name}"):
        method = getattr(owner_instance, f"_{event_name}")
        if callable(method):
            hints.append(
                f"  Found '_{event_name}' (private). "
                "Rename it to remove the leading underscore so it can be called as an event."
            )

    # 3. List available @event_handler methods on the class
    handlers = [
        name
        for name in dir(owner_instance)
        if not name.startswith("_")
        and callable(getattr(owner_instance, name, None))
        and is_event_handler(getattr(owner_instance, name))
    ]
    if handlers:
        hints.append(f"  Available handlers on {cls.__name__}: {', '.join(sorted(handlers))}")

    if not hints:
        return base_msg

    return base_msg + "\n" + "\n".join(hints)


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

    Returns None if allowed, or an error message string if blocked.
    Only @event_handler-decorated methods are allowed.
    """
    mode = djust_config.get("event_security", "strict")
    if mode not in ("warn", "strict"):
        return None

    if is_event_handler(handler):
        return None

    if mode == "strict":
        cls_name = type(owner_instance).__name__
        return (
            f"Event '{event_name}' on {cls_name} is not decorated with "
            "@event_handler.\n"
            f"  Fix: Add the decorator:\n"
            f"    @event_handler\n"
            f"    def {event_name}(self, **kwargs):"
        )

    logger.warning(
        "Deprecation: handler '%s' on %s is not decorated with @event_handler. "
        "This will be blocked in strict mode.",
        sanitize_for_log(event_name),
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
        safe_name = sanitize_for_log(event_name)
        logger.warning("Blocked unsafe event name: %s", safe_name)
        error_msg = f"Blocked unsafe event name: {safe_name}"
        await ws.send_error(_safe_error(error_msg))
        return None

    handler = getattr(owner_instance, event_name, None)
    if not handler or not callable(handler):
        error_msg = _format_handler_not_found_error(owner_instance, event_name)
        logger.warning("Handler not found: %s", sanitize_for_log(event_name))
        await ws.send_error(_safe_error(error_msg, "Event rejected"))
        return None

    security_error = _check_event_security(handler, owner_instance, event_name)
    if security_error:
        logger.warning("Security check failed for event %s", sanitize_for_log(event_name))
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

    # Handler-level permission check
    from .auth import check_handler_permission

    owner_request = getattr(owner_instance, "request", None)
    # If handler has @permission_required but request is missing, deny by default
    handler_meta = getattr(handler, "_djust_decorators", {})
    if handler_meta.get("permission_required") and not owner_request:
        logger.warning(
            "Permission check skipped (no request) for handler with @permission_required"
        )
        await ws.send_error("Permission denied")
        return None
    if owner_request and not check_handler_permission(handler, owner_request):
        await ws.send_error("Permission denied")
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
