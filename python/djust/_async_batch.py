"""Opaque completion identity for a captured batch of owned background tasks."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from .mixins.async_work import track_async_task

logger = logging.getLogger(__name__)


class AsyncBatch:
    """Capture before acknowledgement; start only after the acknowledgement sends.

    The completion frame contains only an opaque token, never callback names,
    state or results. It releases client loading state, not application authority.
    Done callbacks also cover cancellation before a coroutine's first instruction.
    """

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.queued = list(getattr(owner, "_async_tasks", {}).items())
        owner._async_tasks = {}
        pending = getattr(owner, "_async_pending", None)
        if pending:
            self.queued.append(("_default", pending))
            owner._async_pending = None
        self.token = uuid4().hex if self.queued else None
        self._completion_task: asyncio.Future[None] | None = None

    def fields(self) -> dict[str, Any]:
        return {"async_pending": True, "async_batch": self.token} if self.token else {}

    def _finish(self, transport: Any) -> None:
        if self.token is None or self._completion_task is not None:
            return

        async def complete() -> None:
            try:
                await transport.send({"type": "async_complete", "async_batch": self.token})
            except Exception:  # noqa: BLE001 — disconnected transport; no exception payload logging
                logger.debug("Background completion transport unavailable")

        self._completion_task = asyncio.ensure_future(complete())

    def discard(self, transport: Any) -> None:
        """Release an acknowledged batch whose owner disappeared before dispatch."""
        self.queued = []
        self._finish(transport)

    def dispatch(self, transport: Any, runner: Callable[..., Awaitable[None]]) -> None:
        if not self.queued:
            return
        tasks = {
            asyncio.ensure_future(runner(name, callback, args, kwargs))
            for name, (callback, args, kwargs) in self.queued
        }
        self.queued = []

        def settled(task: asyncio.Future[None]) -> None:
            if not task.cancelled() and task.exception() is not None:
                logger.warning("Background task runner failed")
            tasks.discard(task)
            if not tasks:
                # Only a token acknowledgement: safe even if the owner was
                # removed. No rendering or state mutation occurs here.
                self._finish(transport)

        for task in tuple(tasks):
            track_async_task(self.owner, task)
            task.add_done_callback(settled)
