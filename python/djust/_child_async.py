"""Owned background completion for the gated eager explicit child provider."""

import asyncio
from typing import Any

from asgiref.sync import sync_to_async

from ._exposure import ExposureError
from ._exposure_auth import authorize_event, fresh_socket_request
from ._exposure_children import child_event_adapter
from .auth.core import check_view_auth, enforce_object_permission
from .mixins.async_work import run_async_callback, track_async_task


def _check_owner(runtime: Any, root: Any, child: Any, generation: int) -> None:
    if (
        runtime.view_instance is not root
        or getattr(root, "_djust_child_disposed", False)
        or getattr(child, "_djust_child_disposed", False)
        or getattr(child, "_async_work_generation", 0) != generation
    ):
        raise asyncio.CancelledError


def _authorize(runtime: Any, root: Any, child: Any, generation: int) -> Any:
    _check_owner(runtime, root, child, generation)
    # Completion has no current POST, including on SSE. Reload the supported
    # server session and Django auth instead of consuming another event's slot
    # or treating the original POST's cached principal as current authority.
    request = authorize_event(root, fresh_socket_request(root), runtime._explicit_mount_binding)
    enforce_object_permission(root, request)
    current = child
    seen: set[int] = set()
    while current is not root:
        if id(current) in seen or len(seen) >= 16:
            raise ExposureError("Invalid background child ancestry")
        seen.add(id(current))
        child_event_adapter(current, root, request)
        current.request = request
        if check_view_auth(current, request) is not None:
            raise ExposureError("Background child authorization denied")
        enforce_object_permission(current, request)
        current = current._parent_view
    # Authorization and object hooks are application code. Their success does
    # not prove they left the selected owner/registry unchanged.
    child_event_adapter(child, root, request)
    _check_owner(runtime, root, child, generation)
    return request


async def _execute(
    runtime: Any,
    root: Any,
    child: Any,
    generation: int,
    name: str,
    callback: Any,
    args: Any,
    kwargs: Any,
    event_name: str | None,
) -> None:
    try:
        async with runtime._explicit_event_lock, runtime.transport.event_context(root):
            await sync_to_async(_authorize)(runtime, root, child, generation)
        error = None
        result = None
        try:
            result = await run_async_callback(callback, args, kwargs, owner=child)
        except Exception as exc:  # noqa: BLE001 — only the owning application sees this error
            error = exc
        async with runtime._explicit_event_lock, runtime.transport.event_context(root):
            await sync_to_async(_authorize)(runtime, root, child, generation)
            handler = getattr(child, "handle_async_result", None)
            if callable(handler):
                await sync_to_async(handler)(name, result=result, error=error)
            elif error is not None:
                raise ExposureError("Child background callback failed")
            await sync_to_async(_authorize)(runtime, root, child, generation)
            from .websocket import render_embedded_child_html

            html = await sync_to_async(render_embedded_child_html)(child)
            request = await sync_to_async(_authorize)(runtime, root, child, generation)
            if not await runtime._persist_explicit_children_after_event(root, request=request):
                return
            _check_owner(runtime, root, child, generation)
            await runtime.transport.send(
                {
                    "type": "embedded_update",
                    "view_id": child._view_id,
                    "html": html,
                    "event_name": event_name,
                    "source": "async",
                }
            )
            await runtime._flush_all_pending()
            runtime._flush_push_events(child)
    except Exception:  # noqa: BLE001 — no provider/callback exception values on the wire/log
        await runtime.transport.send_error(
            "Child background work unavailable. Please reload the page.", code="async_error"
        )


def dispatch_child_work(runtime: Any, child: Any, event_name: str | None) -> None:
    """Drain only the selected child queue and track tasks on that same owner."""
    root = runtime.view_instance
    if root is None or getattr(child, "_djust_child_disposed", False):
        return
    queued = list(getattr(child, "_async_tasks", {}).items())
    child._async_tasks = {}
    pending = getattr(child, "_async_pending", None)
    if pending:
        queued.append(("_default", pending))
        child._async_pending = None
    generation = getattr(child, "_async_work_generation", 0)
    for name, (callback, args, kwargs) in queued:
        task = asyncio.ensure_future(
            _execute(runtime, root, child, generation, name, callback, args, kwargs, event_name)
        )
        track_async_task(child, task)
