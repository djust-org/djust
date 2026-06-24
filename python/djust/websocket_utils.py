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
from django.core.exceptions import PermissionDenied


from .config import config as djust_config
from .decorators import is_event_handler
from .rate_limit import (
    ConnectionRateLimiter,
    caller_key,
    get_rate_limit_settings,
    handler_rate_check,
    ip_tracker,
)
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


def _format_handler_not_found_error(owner_instance: object, event_name: str) -> str:
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


def get_handler_coerce_setting(handler: Callable[..., Any]) -> bool:
    """
    Get the coerce_types setting from a handler's @event_handler decorator.

    Args:
        handler: The event handler method

    Returns:
        True if type coercion should be enabled (default), False if disabled
    """
    if hasattr(handler, "_djust_decorators"):
        return bool(handler._djust_decorators.get("event_handler", {}).get("coerce_types", True))
    return True


def _check_event_security(
    handler: Callable[..., Any], owner_instance: object, event_name: str
) -> Optional[str]:
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
    rate_limiter: "ConnectionRateLimiter", event_name: str, handler: Callable[..., Any]
) -> None:
    """Register per-handler rate limit into the per-connection limiter.

    LEGACY (F27): the per-handler ``@rate_limit`` is now enforced through the
    SHARED per-caller store (:func:`djust.rate_limit.handler_rate_check`), NOT
    the per-connection :class:`ConnectionRateLimiter` — opening N connections no
    longer multiplies the limit N×. This helper is retained only for backward
    compatibility of the ``websocket.py`` re-export; it is no longer called on
    the enforcement path. The per-connection limiter still owns the GLOBAL
    per-message abuse-disconnect (#17), which is untouched.
    """
    if event_name not in rate_limiter.handler_buckets:
        rl_settings = get_rate_limit_settings(handler)
        if rl_settings:
            rate_limiter.register_handler_limit(
                event_name, rl_settings["rate"], rl_settings["burst"]
            )


async def _validate_event_security(
    ws: Any,
    event_name: str,
    owner_instance: object,
    rate_limiter: "ConnectionRateLimiter",
) -> Optional[Callable[..., Any]]:
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

    handler: Optional[Callable[..., Any]] = getattr(owner_instance, event_name, None)
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

    # Per-handler @rate_limit (F27): enforce against the SHARED per-caller store
    # so a caller has ONE budget per handler regardless of how many WS/SSE
    # connections they open or which transport they use. Caller identity mirrors
    # the SSE owner-principal model (user pk / anon session key / resolved IP);
    # the IP fallback uses the transport's already-resolve_client_ip-resolved
    # ``_client_ip`` (honors DJUST_TRUSTED_PROXY_COUNT — F28).
    #
    # The per-connection ``ConnectionRateLimiter`` keeps owning the GLOBAL
    # per-message abuse-disconnect (#17, in websocket.py:receive). On a
    # per-handler rejection we still bump that connection's warning counter so a
    # single-connection per-handler flood trips should_disconnect() → close.
    rl_settings = get_rate_limit_settings(handler)
    if rl_settings:
        client_ip = getattr(ws, "_client_ip", None)
        owner_request = getattr(owner_instance, "request", None)
        key = caller_key(owner_request, client_ip)
        if not handler_rate_check(key, event_name, rl_settings):
            rate_limiter.warnings += 1
            logger.warning(
                "Per-handler rate limit exceeded for '%s' (warning %d/%d)",
                sanitize_for_log(event_name),
                rate_limiter.warnings,
                rate_limiter.max_warnings,
            )
            if rate_limiter.should_disconnect():
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
    # Wrap in sync_to_async (#1648, sibling of #1638): for a @permission_required
    # handler, check_handler_permission calls user.has_perms(), which under the
    # default ModelBackend queries the DB for a non-superuser — raising
    # SynchronousOnlyOperation when called bare from this async def.
    if owner_request and not await sync_to_async(check_handler_permission)(handler, owner_request):
        await ws.send_error("Permission denied")
        return None

    # Object-level permission check (ADR-017 § Decision 7, v0.9.5-1b).
    # Re-runs on every event so a session can't bypass mount-time denial
    # by carrying a stale _object cache or by mutating the access-
    # determining state without invalidating. The check is a no-op for
    # views that don't override get_object (Decision 6 — opt-in via
    # _has_custom_get_object short-circuit).
    #
    # Per-event denial does NOT close the WS (mount-time denial does).
    # Rationale: the user is authenticated and has the role permission;
    # only this specific action against this specific object is
    # forbidden. Closing the WS would force a full reload, which is
    # wrong UX for "you can't do this here, but you can navigate
    # elsewhere." Send the error frame and let the client decide.
    #
    # Fail-closed on developer-code exceptions: if get_object() or
    # has_object_permission() raise anything other than PermissionDenied
    # (e.g., AttributeError in the developer's body), treat as denial.
    # Security code should not fail-open when the auth predicate crashes.
    if owner_request is None:
        # #1380: when a view overrides get_object() but `request` was
        # never stamped on the instance (e.g., a sticky child whose
        # parent could not propagate the request through a read-only-
        # proxy descriptor — see mixins/sticky.py), we MUST NOT silently
        # skip the per-event object-permission check. Fail closed instead.
        # Views that did not opt into the object-permission lifecycle
        # (no get_object override) keep the original no-op semantics.
        from .auth.core import _has_custom_get_object

        if _has_custom_get_object(owner_instance):
            logger.warning(
                "Per-event object-permission check skipped on %s: "
                "owner_request is None (child view did not receive a "
                "request handle from parent). Failing closed.",
                type(owner_instance).__name__,
            )
            await ws.send_error(
                "Access denied for this object.",
                code="permission_denied",
            )
            return None
        # No custom get_object → no object-permission lifecycle → fall
        # through silently (preserves the existing no-op contract).
    else:
        from .auth.core import check_object_permission

        try:
            # Wrap in sync_to_async to mirror the mount path
            # (websocket.py handle_mount). check_object_permission calls the
            # developer's sync get_object(), which per the canonical ADR-017
            # pattern does a sync ORM read; calling it bare from this async def
            # raised SynchronousOnlyOperation, which the fail-closed catch below
            # mistranslated into a spurious "Access denied" on the first event
            # of every URL-bound LiveView (#1638).
            await sync_to_async(check_object_permission)(owner_instance, owner_request)
        except PermissionDenied:
            await ws.send_error(
                "Access denied for this object.",
                code="permission_denied",
            )
            return None
        except Exception:  # noqa: BLE001 — fail-closed by design
            logger.exception(
                "Object-permission check raised non-PermissionDenied exception "
                "for %s on event %s; failing closed (denying)",
                owner_instance.__class__.__name__,
                sanitize_for_log(event_name or ""),
            )
            await ws.send_error(
                "Access denied for this object.",
                code="permission_denied",
            )
            return None

    return handler


async def _call_handler(
    handler: Callable[..., Any], params: Optional[Dict[str, Any]] = None
) -> Any:
    """
    Call an event handler, handling both sync and async handlers.

    Args:
        handler: The event handler method (sync or async)
        params: Optional dictionary of parameters to pass to the handler.
            Note: Empty dict {} is treated as no params (falsy check).
            Positional args from dj-click="handler('value')" syntax are merged
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
        # Sync handler — run via sync_to_async to avoid blocking the event
        # loop. Handlers commonly do ORM queries or other I/O.
        if params:
            return await sync_to_async(handler)(**params)
        return await sync_to_async(handler)()
