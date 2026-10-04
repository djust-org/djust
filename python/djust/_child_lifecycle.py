"""Owned-subtree disposal for the gated explicit lifecycle."""

import inspect
import logging
import weakref
from typing import Any, Callable, Optional

from asgiref.sync import sync_to_async

from .mixins.async_work import AsyncWorkMixin
from .mixins.waiters import WaiterMixin

logger = logging.getLogger(__name__)

#: Set on a root view when it reaches the connected phase of its live mount;
#: popped by whoever runs ``disconnected()``, so the hook runs at most once.
_CONNECTED_FLAG = "_djust_connected_phase"


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
        owner_regions = getattr(owner, "_explicit_child_render_regions", None)
        if type(owner_regions) is dict:
            region = owner_regions.get(slot)
            if type(region) is tuple and len(region) == 2 and region[0] is current:
                owner_regions.pop(slot)
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
        current._explicit_child_render_regions = {}
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


def _close_view_waiters(view: Any, reason: str) -> None:
    """``view._close_waiters(reason)``: cancel its waiters and refuse later ones.

    Nothing strong holds a waiter's future except the view, so a discarded
    view, the future and the ``start_async`` / ``@background`` task blocked on
    it become an unreachable cycle, and the garbage collector destroys the
    task while it is still pending ("Task was destroyed but it is pending!"),
    closing its coroutine with ``GeneratorExit``: its ``except
    CancelledError`` cleanup never runs (#3236). Cancelling gives the task
    ``CancelledError``; closing makes a later ``wait_for_event`` fail at once
    (#3242 review M2). Best effort: a failure is logged without its value.
    """
    close = getattr(view, "_close_waiters", None)
    if not callable(close):
        return
    try:
        close(reason=reason)
    except Exception:  # noqa: BLE001 — cleanup must never break a teardown
        logger.warning("Closing a discarded view's waiters failed")


def release_legacy_child(child: Any) -> None:
    """The framework half of unregistering a legacy embedded child (#3244).

    Closes the child's ``wait_for_event`` waiters (a waiting ``start_async``
    task gets ``CancelledError`` and runs its own cleanup; a later wait is
    refused) and unregisters the child's own embedded children, descendant
    first. Before #3244 a legacy child's waiters were cancelled on no path, so
    its waiting task was destroyed pending by the garbage collector. As for a
    legacy root, the child's other background work runs to completion.

    Called by ``StickyChildRegistry._unregister_child`` before the child's
    ``_cleanup_on_unregister`` hook, and by :func:`discard_sticky_child`. Each
    step is best effort.
    """
    _close_view_waiters(child, "child_unregistered")
    registry = getattr(child, "_child_views", None)
    unregister = getattr(child, "_unregister_child", None)
    if type(registry) is dict and callable(unregister):
        for view_id in list(registry):
            try:
                unregister(view_id)
            except Exception:  # noqa: BLE001
                logger.warning("Unregistering a legacy child's embedded view failed")


def discard_sticky_child(child: Any, *, navigation: bool = True) -> None:
    """Drop a sticky child that a navigation does not keep (#3244).

    The one teardown for every place a live_redirect discards a sticky child:
    its auth re-check failed, the destination has no slot for it, the redirect
    could not be resolved or failed, or the socket closed mid-redirect; and
    ``{% live_render %}`` refusing a reused sticky child at render
    (``navigation=False``). It is detached from the page it was registered on,
    if it still is. An explicit child is disposed (``dispose_child_subtree``);
    a legacy child gets :func:`release_legacy_child` (waiters and nested
    children) and then its ``_on_sticky_unmount`` hook (which cancels its
    background work), and outside a navigation its ``_cleanup_on_unregister``
    hook too, as unregistering it would.
    """
    from ._exposure import uses_legacy_exposure

    if not uses_legacy_exposure(child):
        dispose_child_subtree(child, navigation=navigation)
        return
    # Still registered on the page being left: detach it, so that page's own
    # teardown does not unregister it a second time.
    owner_registry = getattr(getattr(child, "_parent_view", None), "_child_views", None)
    slot = getattr(child, "_view_id", None)
    if type(owner_registry) is dict and owner_registry.get(slot) is child:
        owner_registry.pop(slot)
    release_legacy_child(child)
    hooks = (
        ("_on_sticky_unmount",) if navigation else ("_on_sticky_unmount", "_cleanup_on_unregister")
    )
    for name in hooks:
        hook = getattr(child, name, None)
        if callable(hook):
            try:
                hook()
            except Exception:  # noqa: BLE001 — legacy child only; cleanup must not raise
                logger.exception("sticky child %s raised", name)


def untrack_view_presence(view: Any) -> None:
    """Stop tracking a discarded view's presence (#3250 review M3).

    Before, only the view mounted when the socket disconnected was untracked,
    so a view replaced by navigation or by a second mount, and a
    ``mount_batch`` sibling, stayed in the presence list until
    ``PRESENCE_TIMEOUT``. ``untrack_presence`` broadcasts to peers through the
    synchronous channel-layer API, so async callers run this on a thread
    (``sync_to_async``). Best effort: a failure is logged under the view's
    diagnostics policy.
    """
    untrack = getattr(view, "untrack_presence", None)
    if not callable(untrack):
        return
    try:
        untrack()
    except Exception as exc:  # noqa: BLE001 — application hooks run inside
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(
            logger, (view,), exc, "Error cleaning up presence: %s", exc, level="warning"
        )


#: Hook names a view can define: ``(class, name)`` pairs already warned about.
_HOOK_WARNED: "weakref.WeakKeyDictionary[type, set[str]]" = weakref.WeakKeyDictionary()
_HOOK_NAMES = ("connected", "disconnected")


def _defines_hook(view: Any, name: str) -> bool:
    """Whether ``name`` exists on the view at all, found without running a descriptor."""
    try:
        inspect.getattr_static(view, name)
    except AttributeError:
        return False
    return True


def _lifecycle_hook(view: Any, name: str) -> Optional[Callable[[], Any]]:
    """The view's ``connected`` / ``disconnected`` hook, or None (#3007).

    Only a plain method that takes no arguments counts: a function, static or
    class method that is not ``async def`` and binds with no extra argument.
    Anything else that carries the name (a state attribute, a property, a nested
    class, a method that needs arguments, a coroutine function) was code that
    never ran as a hook before this contract, so it is skipped, with one
    value-free warning per class, instead of failing a mount or a teardown. The
    lookup is static: a property is never evaluated.
    """
    try:
        found = inspect.getattr_static(view, name)
    except AttributeError:
        return None
    reason = None
    hook: Optional[Callable[[], Any]] = None
    if not (inspect.isfunction(found) or isinstance(found, (staticmethod, classmethod))):
        reason = "is not a regular method"
    else:
        try:
            hook = getattr(view, name)
            if inspect.iscoroutinefunction(hook):
                reason = "is async def"
            else:
                inspect.signature(hook).bind()
        except TypeError:
            reason = "takes arguments"
        except Exception:  # noqa: BLE001 — a hostile descriptor must not fail a mount
            reason = "cannot be resolved"
    if reason is None:
        return hook
    warned = _HOOK_WARNED.setdefault(type(view), set())
    if name not in warned:
        warned.add(name)
        logger.warning(
            "%s.%s %s; djust skips it as the %s() lifecycle hook (it takes no arguments, "
            "is not async, and runs on a worker thread)",
            type(view).__qualname__,
            name,
            reason,
            name,
        )
    return None


def awaiting_disconnected(view: Any) -> bool:
    """Whether ``view`` reached the connected phase and has not run ``disconnected()``."""
    return bool(view is not None and view.__dict__.get(_CONNECTED_FLAG, False))


def begin_live_connection(view: Any) -> Optional[Callable[[], Any]]:
    """Enter the connected phase of a live mount (#3007); returns the ``connected`` hook to run.

    Called by ``ViewRuntime.dispatch_mount`` once the view is admitted and set
    up (auth, ``on_mount`` hooks, ``mount()`` or a state restore, the
    object-permission check, ``handle_params()``) and before its first render,
    so state the hook sets is in the mount frame. The caller runs the returned
    hook on a worker thread (None: nothing to run); an exception from it is the
    caller's, and fails the mount as one from ``mount()`` does. Only the live
    mount gets here: the HTTP render and the HTTP POST fallback never do.

    Marks the view so :func:`run_view_disconnected` runs for it, whether or not
    it defines ``connected``. A view whose class defines neither hook is not
    marked and costs nothing.
    """
    if not any(_defines_hook(view, name) for name in _HOOK_NAMES):
        return None
    view.__dict__[_CONNECTED_FLAG] = True
    return _lifecycle_hook(view, "connected")


def run_view_disconnected(view: Any) -> None:
    """The ``disconnected()`` view hook (#3007), sync: call it on a worker thread.

    Runs for a view that reached the connected phase, once, when its live mount
    ends: its socket (or SSE stream) closed, or a navigation, a second mount, an
    ``unmount`` frame or a revoked authorization released it. A view the mount
    refused earlier never runs it. Best effort: the tenant the view mounted
    under is bound, as for its events; an exception is logged without its value
    and never stops the teardown.
    """
    if not view.__dict__.pop(_CONNECTED_FLAG, False):
        return
    hook = _lifecycle_hook(view, "disconnected")
    if hook is None:
        return
    from .runtime import _tenant_context

    try:
        with _tenant_context(getattr(view, "_tenant", None)):
            hook()
    except Exception as exc:  # noqa: BLE001 — application hook; teardown must go on
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(logger, (view,), exc, "Error in disconnected(): %s", exc, level="warning")


async def fire_view_disconnected(view: Any) -> None:
    """:func:`run_view_disconnected` for an async caller, before the view's release.

    The hook runs on a worker thread, so it may use the ORM. A view that never
    reached the connected phase costs no thread hop. Callers release the view in
    a ``finally``, so a cancellation during the hook still releases it.
    """
    if not awaiting_disconnected(view):
        return
    await sync_to_async(run_view_disconnected)(view)


def release_root_view(view: Any, *, navigation: bool, reason: str) -> None:
    """Tear down a root view its transport discards (#3244, #3245).

    The one view teardown shared by every place a transport lets go of its
    mounted view: a WebSocket ``live_redirect``, a second ``mount`` or
    ``mount_batch`` frame on a mounted socket, the WebSocket disconnect, SSE
    navigation (``_replace_view``) and SSE close (``shutdown``). The transport
    work (leaving channel groups, stopping the tick task) stays with the
    transport.

    * An explicit view is disposed with its whole owned subtree
      (``dispose_child_subtree``), which cancels its background work and
      waiters.
    * A legacy view gets the legacy steps: its upload temp files are removed,
      its waiters are closed (``_close_waiters``: cancelled, and later waits
      refused, #3236), and every embedded child still registered is
      unregistered (``_unregister_child``: a legacy child's waiters are closed
      and its ``_cleanup_on_unregister`` runs; an explicit child is disposed).
      Its other background work runs to completion, as it always has; the
      transport drops the view, so a late result is discarded.
    * Either way its Rust live handles are dropped (``_clear_live_handles``,
      #2539): the handles hold application objects, which the GC cannot see.

    Sticky children that navigation keeps must be removed from the view's
    registry BEFORE this runs; everything still registered is torn down.
    Every step is best effort, and a failure is logged without its value.
    """
    from ._exposure import uses_legacy_exposure
    from .websocket import _clear_live_handles

    if not uses_legacy_exposure(view):
        dispose_child_subtree(view, navigation=navigation)
    else:
        if hasattr(view, "_cleanup_uploads"):
            try:
                view._cleanup_uploads()
            except Exception:  # noqa: BLE001
                logger.warning("Cleaning up a released legacy view's uploads failed")
        _close_view_waiters(view, reason)
        registry = getattr(view, "_child_views", None)
        unregister = getattr(view, "_unregister_child", None)
        if type(registry) is dict and callable(unregister):
            for view_id in list(registry):
                try:
                    unregister(view_id)
                except Exception:  # noqa: BLE001
                    logger.warning("Unregistering a released legacy view's child failed")
    _clear_live_handles(view)
