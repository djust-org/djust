"""Owned-subtree disposal for the gated explicit lifecycle."""

import asyncio
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


def call_teardown_hook(owner: Any, hook: Callable[[], Any]) -> Any:
    """Call one application teardown hook of ``owner``: ``_cleanup_uploads``,
    ``_cleanup_on_unregister`` or ``_on_sticky_unmount`` (#3646).

    Every place a view or child is torn down calls its hooks through here, so a
    hook that reads the database gets the ``sync_turn_wrapper`` scope wherever the
    teardown runs on a worker thread: SSE navigation and the release of a view
    mounted beside the page (``sync_to_async(release_root_view)``), SSE
    ``shutdown()`` reached through ``abandon()`` / ``response.close()``, and a
    WebSocket ``live_redirect`` discarding a sticky child whose auth now fails
    (``_preserve_sticky_children`` runs on a worker thread). On the event loop
    (the WebSocket release, the SSE stream's own close) the hook runs as before,
    unwrapped: a wrapper that opens a transaction would raise there
    (:func:`djust.sync_turn.run_teardown_step`).
    """
    from .sync_turn import run_teardown_step

    return run_teardown_step(owner, "lifecycle", hook)


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
        owner_bindings = getattr(owner, "_explicit_child_vdom_bindings", None)
        if type(owner_bindings) is dict:
            for node_id, bound in list(owner_bindings.items()):
                if bound is current:
                    owner_bindings.pop(node_id)
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
        current._explicit_child_carry_identity = None
        current._explicit_child_render_regions = {}
        current._explicit_child_vdom_bindings = {}
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
        # A child's own cleanup hook runs on navigation too, as it did for 1.3
        # children: whatever it frees (files, subscriptions) must not leak when
        # the page moves on. Disposal marks the child, so it runs once.
        for name in ("_cleanup_uploads", "_cleanup_on_unregister", "_on_sticky_unmount"):
            try:
                hook = getattr(current, name, None)
                if callable(hook):
                    call_teardown_hook(current, hook)
            except Exception:  # noqa: BLE001
                logger.error("Child lifecycle cleanup failed")


def discard_sticky_child(child: Any, *, navigation: bool = True) -> None:
    """Drop a sticky child that a navigation does not keep (#3244).

    The one teardown for every place a live_redirect discards a sticky child:
    its auth re-check failed, the destination has no slot for it, the redirect
    could not be resolved or failed, or the socket closed mid-redirect; and
    ``{% live_render %}`` refusing a reused sticky child at render
    (``navigation=False``). The child is disposed with its owned subtree
    (``dispose_child_subtree``).
    """
    dispose_child_subtree(child, navigation=navigation)


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
        from .sync_turn import run_sync_step

        # ``untrack_presence`` reads ``get_presence_key()``: a ``"hook"`` step (#3637).
        run_sync_step(view, "hook", untrack)
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
    hook = _lifecycle_hook(view, "connected")
    if hook is None:
        return None
    from .sync_turn import sync_step

    # A ``"lifecycle"`` step of the sync-turn wrapper (#3637).
    return sync_step(view, "lifecycle", hook)


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
        from .sync_turn import run_sync_step

        with _tenant_context(getattr(view, "_tenant", None)):
            run_sync_step(view, "lifecycle", hook)
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


def release_root_view(
    view: Any, *, navigation: bool, reason: str, clear_live_handles: bool = True
) -> None:
    """Tear down a root view its transport discards (#3244, #3245).

    The one view teardown shared by every place a transport lets go of its
    mounted view: a WebSocket ``live_redirect``, a second ``mount`` or
    ``mount_batch`` frame on a mounted socket, the WebSocket disconnect, SSE
    navigation (``_replace_view``) and SSE close (``shutdown``). The transport
    work (leaving channel groups, stopping the tick task) stays with the
    transport.

    * The view is disposed with its whole owned subtree
      (``dispose_child_subtree``), which cancels its background work and
      waiters and refuses later waits.
    * Its Rust live handles are dropped (``_clear_live_handles``, #2539): the
      handles hold application objects, which the GC cannot see.

    Sticky children that navigation keeps must be removed from the view's
    registry BEFORE this runs; everything still registered is torn down.
    Every step is best effort, and a failure is logged without its value.
    """
    from .websocket import _clear_live_handles

    dispose_child_subtree(view, navigation=navigation)
    if clear_live_handles:
        _clear_live_handles(view)


async def arelease_root_view(view: Any, *, navigation: bool, reason: str) -> None:
    """:func:`release_root_view` for a transport on the event loop.

    The live handles are dropped on the view's sync thread rather than on
    the loop: ``clear_live_handles`` borrows the Rust view mutably, and a
    render of the view may still be running on that thread (a tick, a
    deferred push or a background result queued before the release; a
    cancelled coroutine does not stop its thread). On the loop the two
    collided as ``Already borrowed`` and the handles stayed; as a hop on the
    same thread the clear runs after that render. The rest of the release
    stays on the loop, where the waiters and tasks it cancels live.
    """
    from .websocket import _clear_live_handles

    release_root_view(view, navigation=navigation, reason=reason, clear_live_handles=False)
    try:
        await sync_to_async(_clear_live_handles)(view)
    except asyncio.CancelledError:
        # Cancelled again while waiting for the thread: drop the handles here
        # rather than not at all.
        _clear_live_handles(view)
        raise
