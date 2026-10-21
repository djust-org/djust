"""Call a view hook that may be ``def`` or ``async def``.

A view's lifecycle hooks (``mount``, ``handle_params``, server-push hooks,
``handle_info``) may be written either way. From async framework code a sync
hook runs off the event loop (``sync_to_async``, as it always has) and an async
hook is awaited ON the loop, so its awaits overlap with other sessions' work
instead of queueing on asgiref's one shared sync thread. From sync framework
code (the HTTP render) an async hook is run to completion before the caller
continues.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from asgiref.sync import async_to_sync, iscoroutinefunction, sync_to_async


def is_async_hook(fn: Any) -> bool:
    """Whether calling ``fn`` returns a coroutine.

    Looks through ``functools.wraps`` layers, so a sync-looking decorator
    wrapper around an ``async def`` (one that returns the coroutine) counts.
    """
    if fn is None:
        return False
    if iscoroutinefunction(fn):
        return True
    try:
        inner = inspect.unwrap(fn)
    except ValueError:  # a __wrapped__ cycle
        return False
    return inner is not fn and iscoroutinefunction(inner)


async def _await(awaitable: Any) -> Any:
    return await awaitable


async def call_hook(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` from async code: awaited on the loop if async, else threaded."""
    if is_async_hook(fn):
        return await fn(*args, **kwargs)
    result = await sync_to_async(fn)(*args, **kwargs)
    if inspect.isawaitable(result):
        # A sync callable that returned a coroutine (a wrapper the check above
        # could not see through): run it here, on the loop.
        result = await result
    return result


def call_hook_sync(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` from sync code, running an async hook to completion."""
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        result = async_to_sync(_await)(result)
    return result
