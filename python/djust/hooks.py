"""
on_mount hooks for LiveView — cross-cutting mount logic.

on_mount hooks allow you to inject shared behaviour that runs during every
mount (and reconnect) of a LiveView, after authentication but before the
view's own ``mount()`` method.  This is the djust equivalent of Phoenix
LiveView's ``on_mount/1`` callback.

Hook signature::

    def my_hook(view, request, **kwargs) -> Optional[str]

Return ``None`` to continue the mount chain, or a redirect URL string to
halt mounting and navigate the client away.

Example::

    from djust.hooks import on_mount

    @on_mount
    def require_verified_email(view, request, **kwargs):
        if not request.user.email_verified:
            return '/verify-email/'

    class ProfileView(LiveView):
        on_mount = [require_verified_email]
"""

import logging
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)


def on_mount(func: Callable) -> Callable:
    """Mark a function as an on_mount hook.

    Hook signature: ``def hook(view, request, **kwargs) -> Optional[str]``

    Return ``None`` to continue, or a redirect URL string to halt mounting.

    Example::

        @on_mount
        def require_auth(view, request, **kwargs):
            if not request.user.is_authenticated:
                return '/login/'
    """
    func._djust_on_mount = True  # type: ignore[attr-defined]
    return func


def is_on_mount(func: Callable) -> bool:
    """Check if a function is decorated with ``@on_mount``."""
    return bool(getattr(func, "_djust_on_mount", False))


def _collect_on_mount_hooks(view_class: type) -> List[Callable]:
    """Walk MRO and collect ``on_mount`` hooks, parent-first, deduplicated."""
    seen: set = set()
    hooks: List[Callable] = []
    for klass in reversed(view_class.__mro__):
        for hook in getattr(klass, "__dict__", {}).get("on_mount", []):
            hook_id = id(hook)
            if hook_id not in seen:
                seen.add(hook_id)
                hooks.append(hook)
    return hooks


def run_on_mount_hooks(view_instance: Any, request: Any, **kwargs: Any) -> Optional[str]:
    """Execute ``on_mount`` hooks in order.

    Returns a redirect URL string if a hook halts the mount, or ``None``
    if all hooks pass.
    """
    hooks = _collect_on_mount_hooks(type(view_instance))
    for hook in hooks:
        result = hook(view_instance, request, **kwargs)
        if isinstance(result, str):
            logger.info(
                "on_mount hook %s halted mount for %s: redirect to %s",
                hook.__name__,
                view_instance.__class__.__name__,
                result,
            )
            return result
    return None
