"""
WaiterMixin — ``await self.wait_for_event(...)`` primitive.

ADR-002 Phase 1b. Lets a ``@background`` handler suspend until a
specific user-triggered event handler runs, with optional predicate
filtering and timeout. The core building block for :class:`TutorialMixin`
(Phase 1c) and for any server-driven flow that needs to pause mid-plan
until the user actually performs an action.

Usage::

    from djust.decorators import event_handler, background

    class Onboarding(LiveView):
        @event_handler
        @background
        async def start_tour(self, **kwargs):
            self.push_commands(JS.add_class("tour-highlight", to="#btn-new"))
            try:
                # Pause until the user clicks the button (fires the
                # create_project event handler) — or give up after 60s.
                result = await self.wait_for_event("create_project", timeout=60)
            except TimeoutError:
                self.push_commands(JS.remove_class("tour-highlight", to="#btn-new"))
                self.tour_abandoned = True
                return
            self.push_commands(JS.remove_class("tour-highlight", to="#btn-new"))
            # result is the kwargs dict that was passed to create_project
            self.project_name = result.get("name", "")

Design notes
------------

Waiters live on the view instance in a ``_waiters: Dict[str, List[_Waiter]]``
dict keyed by event name. Each ``_Waiter`` carries an ``asyncio.Event``
(for signalling) and a payload slot (filled when the waiter resolves).
Notify is O(#waiters for this event name) per handler call, which is
typically 0 or 1. Cleanup happens inline when a waiter resolves or
times out — no garbage collection pass needed.

The mixin is thread-safe only in the sense that asyncio's single-threaded
event loop guarantees ordered execution of the async code here. Do not
call ``wait_for_event`` from a non-async context — it must be awaited.

Predicates are called with the kwargs dict that was passed to the
handler. A predicate that raises is treated as False (the waiter
doesn't resolve for that kwargs call) and the raised exception is
logged via the ``djust.waiters`` logger. This preserves the invariant
that a bad predicate can't crash the event pipeline.

Waiters created during a handler call are **not** notified for that
same call — the notify pass runs AFTER the handler completes and
inspects the waiter dict at that moment, so a new waiter won't see
the current event. This prevents re-entrancy surprises where
``wait_for_event("X")`` inside an ``X`` handler would resolve
against itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("djust.waiters")


@dataclass
class _Waiter:
    """One pending ``wait_for_event`` caller."""

    event_name: str
    predicate: Optional[Callable[[Dict[str, Any]], bool]] = None
    future: asyncio.Future = field(default_factory=lambda: None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.future is None:
            loop = asyncio.get_event_loop()
            self.future = loop.create_future()


class WaiterMixin:
    """
    Adds ``wait_for_event`` to a LiveView.

    Allows a ``@background`` handler to suspend until a specific
    event handler is called by the user (or a specific predicate
    matches). Resolves with the kwargs dict passed to the matching
    handler.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._waiters: Dict[str, List[_Waiter]] = {}

    async def wait_for_event(
        self,
        name: str,
        *,
        timeout: Optional[float] = None,
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Dict[str, Any]:
        """
        Suspend until an ``@event_handler`` named ``name`` is called.

        Args:
            name: The name of the event handler to wait for. Must match
                the method name exactly (e.g. ``"create_project"``).
            timeout: Optional seconds to wait before giving up. When
                the timeout elapses, raises ``asyncio.TimeoutError``.
                ``None`` (the default) waits indefinitely.
            predicate: Optional callable taking the handler's kwargs
                dict and returning ``True`` to resolve the waiter or
                ``False`` to keep waiting. Useful for "wait until the
                user clicks *this specific* button" when multiple
                events might fire the same handler with different
                arguments. A predicate that raises is treated as
                ``False`` and the exception is logged.

        Returns:
            The kwargs dict passed to the matching handler.

        Raises:
            asyncio.TimeoutError: If ``timeout`` elapses before a
                matching event is seen.

        Example:
            >>> # Wait for the user to click create_project with any args
            >>> result = await self.wait_for_event("create_project")
            >>> print(result.get("name"))

            >>> # Wait for the user to submit a form with a specific project id
            >>> result = await self.wait_for_event(
            ...     "submit_form",
            ...     predicate=lambda kw: kw.get("project_id") == 42,
            ...     timeout=30,
            ... )

            >>> # Give up after 60 seconds if no click arrives
            >>> try:
            ...     await self.wait_for_event("accept_tour", timeout=60)
            ... except asyncio.TimeoutError:
            ...     self.user_abandoned = True
        """
        waiter = _Waiter(event_name=name, predicate=predicate)
        if not hasattr(self, "_waiters") or self._waiters is None:
            self._waiters = {}
        self._waiters.setdefault(name, []).append(waiter)

        try:
            if timeout is not None:
                return await asyncio.wait_for(waiter.future, timeout=timeout)
            return await waiter.future
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # Remove the waiter so it doesn't linger — notify would skip
            # it anyway once the future is done, but an explicit remove
            # keeps the registry tidy for long-running views.
            self._remove_waiter(waiter)
            raise

    def _notify_waiters(self, event_name: str, kwargs: Dict[str, Any]) -> None:
        """
        Resolve any pending waiters for ``event_name``.

        Called by the WebSocket consumer (or the SSE consumer, or the
        test client) immediately after an event handler completes.
        Walks the waiter list for the given event name, checks each
        waiter's predicate against the handler's kwargs, and resolves
        matching waiters by setting their future's result.

        Waiters with a predicate that returns False (or raises) stay
        in the registry for the next call. Waiters that resolve are
        removed inline.

        This method is a no-op if no waiters are registered for the
        given name — the common case on every handler call.
        """
        if not getattr(self, "_waiters", None):
            return
        waiters = self._waiters.get(event_name)
        if not waiters:
            return

        # Snapshot then filter, so predicate side effects can't
        # concurrently mutate the list during iteration.
        remaining: List[_Waiter] = []
        for waiter in waiters:
            if waiter.future.done():
                # Already resolved (e.g. by a previous notify call or
                # cancelled by a timeout in flight) — drop from registry.
                continue

            if waiter.predicate is not None:
                try:
                    matched = bool(waiter.predicate(kwargs))
                except Exception as exc:
                    logger.warning(
                        "wait_for_event predicate for %r raised %r — treating as no-match",
                        event_name,
                        exc,
                    )
                    matched = False
                if not matched:
                    remaining.append(waiter)
                    continue

            # Resolve the waiter with the handler's kwargs. Use
            # set_result inside a try so that a cancelled future
            # (raced with timeout) doesn't crash the notify pass.
            try:
                waiter.future.set_result(dict(kwargs))
            except asyncio.InvalidStateError:
                # Future was cancelled between our done() check and
                # set_result — just drop it.
                pass

        if remaining:
            self._waiters[event_name] = remaining
        else:
            self._waiters.pop(event_name, None)

    def _remove_waiter(self, waiter: _Waiter) -> None:
        """Remove a specific waiter from the registry."""
        if not getattr(self, "_waiters", None):
            return
        bucket = self._waiters.get(waiter.event_name)
        if not bucket:
            return
        try:
            bucket.remove(waiter)
        except ValueError:
            pass
        if not bucket:
            self._waiters.pop(waiter.event_name, None)

    def _cancel_all_waiters(self, reason: str = "view_unmount") -> None:
        """
        Cancel every pending waiter on this view.

        Called during view teardown so ``@background`` tasks awaiting
        a waiter unblock with ``CancelledError`` instead of leaking.
        """
        if not getattr(self, "_waiters", None):
            return
        for _, bucket in list(self._waiters.items()):
            for waiter in bucket:
                if not waiter.future.done():
                    waiter.future.cancel()
        self._waiters.clear()
