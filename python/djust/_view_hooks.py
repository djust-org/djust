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


# ``handle_async_result`` / ``handle_tick`` reach here from the HTTP fallback's
# background work (#3630), which has no ``call_async_result`` / ``call_tick``.
_HOOK_STEPS = {
    "mount": "mount",
    "handle_params": "handle_params",
    "handle_invalid_params": "handle_params",
    "handle_async_result": "async",
    "handle_tick": "tick",
}


def _hook_step(fn: Any) -> str:
    """The ``sync_turn_wrapper`` step of a view hook (``djust.sync_turn``)."""
    return _HOOK_STEPS.get(getattr(fn, "__name__", ""), "hook")


async def call_hook(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` from async code: awaited on the loop if async, else threaded."""
    if is_async_hook(fn):
        return await fn(*args, **kwargs)
    from .sync_turn import sync_step

    stepped = sync_step(getattr(fn, "__self__", None), _hook_step(fn), fn)
    result = await sync_to_async(stepped)(*args, **kwargs)
    if inspect.isawaitable(result):
        # A sync callable that returned a coroutine (a wrapper the check above
        # could not see through): run it here, on the loop.
        result = await result
    return result


async def call_async_result(
    view: Any, handler: Callable[..., Any], name: str, **outcome: Any
) -> Any:
    """Call ``view``'s ``handle_async_result`` off the loop, as an ``async`` step.

    The one place the framework runs that hook from async code, so the
    ``sync_turn_wrapper`` (``djust.sync_turn``) sees it on every transport and
    for every owner (a root view, an embedded child, a sticky child). ``handler``
    is the view's bound ``handle_async_result``; ``outcome`` is ``result=`` /
    ``error=`` (#3625).
    """
    from .sync_turn import sync_step

    if is_async_hook(handler):
        # Not wrapped, as ``call_hook`` and ``run_async_callback`` do not wrap an
        # ``async def``: ``sync_to_async`` refuses it with the same TypeError it
        # raises with no wrapper configured, instead of the wrapper's
        # ``functools.wraps`` shim hiding it and dropping the coroutine.
        return await sync_to_async(handler)(name, **outcome)
    return await sync_to_async(sync_step(view, "async", handler))(name, **outcome)


async def call_tick(view: Any) -> Any:
    """Call ``view.handle_tick`` off the loop, as a ``tick`` step (#3625).

    The offloaded tick (``worker_threads``) runs ``handle_tick`` inside its own
    sync hop and enters the step there with ``run_sync_step`` instead.
    """
    from .sync_turn import sync_step

    if is_async_hook(view.handle_tick):
        # See call_async_result: refused loudly, with or without a wrapper.
        return await sync_to_async(view.handle_tick)()
    return await sync_to_async(sync_step(view, "tick", view.handle_tick))()


def call_hook_sync(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` from sync code, running an async hook to completion."""
    if is_async_hook(fn):
        result = fn(*args, **kwargs)
    else:
        from .sync_turn import run_sync_step

        result = run_sync_step(getattr(fn, "__self__", None), _hook_step(fn), fn, *args, **kwargs)
    if inspect.isawaitable(result):
        result = async_to_sync(_await)(result)
    return result
