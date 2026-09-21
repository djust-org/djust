"""Cancellation-safe waiting for work performed under a caller's render lock."""

import asyncio
from typing import Awaitable, TypeVar

_T = TypeVar("_T")


async def settle_render_operation(operation: Awaitable[_T]) -> _T:
    """Wait for an in-flight worker before propagating caller cancellation.

    Cancelling sync_to_async cannot stop its running thread. The caller must
    retain its render lock around this await. The operation may compute/mutate
    state, but must not send frames: a cancelled caller discards its result.
    """
    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
