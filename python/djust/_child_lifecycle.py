"""Owned-subtree disposal for the gated explicit lifecycle."""

import logging
from typing import Any

from .mixins.async_work import AsyncWorkMixin
from .mixins.waiters import WaiterMixin

logger = logging.getLogger(__name__)


def dispose_child_subtree(child: Any, *, navigation: bool = False) -> None:
    """Detach an owned graph before running descendant-first cleanup once.

    Registry references with inconsistent ownership are removed, not followed
    into another parent's subtree. Iterative traversal handles cycles without
    recursion. Cleanup is best effort; no application exception values are logged.
    Stored session envelopes are not pruned here.
    """
    pending = [child]
    ordered = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen or getattr(current, "_djust_child_disposed", False):
            continue
        seen.add(id(current))
        current._djust_child_disposed = True
        owner = getattr(current, "_parent_view", None)
        slot = getattr(current, "_view_id", None)
        owner_registry = getattr(owner, "_child_views", None)
        if type(owner_registry) is dict and owner_registry.get(slot) is current:
            owner_registry.pop(slot)
        registry = getattr(current, "_child_views", None)
        if type(registry) is dict:
            children = list(registry.items())
            registry.clear()
            for key, descendant in children:
                if (
                    getattr(descendant, "_parent_view", None) is current
                    and getattr(descendant, "_view_id", None) == key
                ):
                    pending.append(descendant)
        current._parent_view = None
        current._view_id = None
        # Sessionless reuse identities hold the root by identity. A disposed
        # child must not retain that hidden ownership reference or be reusable.
        current._explicit_child_reuse_identity = None
        current._deferred_callbacks = []
        ordered.append(current)

    for current in reversed(ordered):
        # Framework drains are independent of optional application hooks.
        try:
            AsyncWorkMixin.cancel_async_all(current)
        except Exception:  # noqa: BLE001
            logger.error("Child async cleanup failed")
        try:
            WaiterMixin._cancel_all_waiters(current, reason="child_disposed")
        except Exception:  # noqa: BLE001
            logger.error("Child waiter cleanup failed")
        hooks = (
            ("_cleanup_uploads", "_on_sticky_unmount")
            if navigation
            else ("_cleanup_uploads", "_cleanup_on_unregister", "_on_sticky_unmount")
        )
        for name in hooks:
            try:
                hook = getattr(current, name, None)
                if callable(hook):
                    hook()
            except Exception:  # noqa: BLE001
                logger.error("Child lifecycle cleanup failed")
