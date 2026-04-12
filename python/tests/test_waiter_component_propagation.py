"""Tests for LiveComponent → parent LiveView waiter propagation.

Follow-up to ADR-002 Phase 1b/1c. The Phase 1b ``wait_for_event``
primitive and the Phase 1c ``TutorialMixin`` both resolve waiters via
``_notify_waiters`` in ``WaiterMixin``. The notification is triggered
from the WebSocket consumer's ``handle_event`` dispatch path after a
successful handler call.

Originally, the notification was only wired to the **main LiveView**
event branch — the LiveComponent branch called the component's
handler but never propagated the event name to the parent view's
waiters. That meant a ``TutorialStep(wait_for="foo", ...)`` on a view
mixing in ``TutorialMixin`` would silently stall forever if the
matching ``foo`` handler lived on an embedded ``LiveComponent`` rather
than the view itself. The tutorials guide documented this as a known
limitation (``docs/website/guides/tutorials.md``).

This module tests the fix: the component branch now calls
``self.view_instance._notify_waiters(event_name, notify_kwargs)``
after the component handler runs, with ``component_id`` injected into
``notify_kwargs`` so predicates can disambiguate events fired from
different component instances.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from djust.components.base import LiveComponent
from djust.decorators import event_handler
from djust.mixins.waiters import WaiterMixin
from djust.rate_limit import ConnectionRateLimiter
from djust.websocket import LiveViewConsumer


class _WaitingView(WaiterMixin):
    """Minimal LiveView-like object that hosts a waiter registry and
    the component map the consumer's component branch reads."""

    def __init__(self) -> None:
        super().__init__()
        self._components: Dict[str, LiveComponent] = {}
        self._view_id = "root"

    def _get_all_child_views(self) -> Dict[str, Any]:
        return {}


class _ClickComponent(LiveComponent):
    """Component with one handler: ``item_clicked`` — the event we
    want to propagate to the parent view's waiters."""

    def __init__(self, component_id: str) -> None:
        super().__init__()
        self.component_id = component_id
        self.last_click: Optional[Dict[str, Any]] = None

    @event_handler
    def item_clicked(self, item_id: int = 0, **kwargs) -> None:
        self.last_click = {"item_id": item_id, **kwargs}


def _make_consumer(view: _WaitingView) -> LiveViewConsumer:
    """Build a LiveViewConsumer instance with just enough state to
    exercise ``handle_event`` for the component branch. Avoids the
    real ``connect`` path which needs channels + an actual socket."""
    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.view_instance = view
    consumer._render_lock = asyncio.Lock()
    consumer._processing_user_event = False
    consumer._current_event_name = None
    consumer._current_event_ref = None
    consumer._rate_limiter = ConnectionRateLimiter(rate=10000, burst=10000)
    consumer.use_actors = False
    consumer.actor_handle = None
    # Methods the dispatch path can invoke — stubbed out so a test
    # failure shows the real assertion, not a ``send_json is missing``
    # AttributeError.
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    consumer._send_update = AsyncMock()
    return consumer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComponentEventPropagation:
    """``handle_event`` must call ``_notify_waiters`` on the parent
    view after a successful component event handler call, so
    ``wait_for_event`` on the parent resolves."""

    @pytest.mark.asyncio
    async def test_component_event_resolves_parent_waiter(self):
        view = _WaitingView()
        component = _ClickComponent(component_id="click-42")
        view._components[component.component_id] = component

        consumer = _make_consumer(view)

        async def wait_for_click():
            return await view.wait_for_event("item_clicked", timeout=2.0)

        waiter_task = asyncio.create_task(wait_for_click())
        await asyncio.sleep(0.01)  # Let the waiter register.

        await consumer.handle_event(
            {
                "event": "item_clicked",
                "params": {"component_id": "click-42", "item_id": 42},
            }
        )

        resolved = await waiter_task
        # Component handler fired
        assert component.last_click == {"item_id": 42}
        # Parent waiter received the event kwargs + injected component_id
        assert resolved["item_id"] == 42
        assert resolved["component_id"] == "click-42"

    @pytest.mark.asyncio
    async def test_component_id_injected_into_notify_kwargs(self):
        """The notify kwargs always include ``component_id`` so
        predicates can filter by which component fired the event."""
        view = _WaitingView()
        view._components["c-a"] = _ClickComponent("c-a")
        view._components["c-b"] = _ClickComponent("c-b")

        consumer = _make_consumer(view)

        # Wait for a click specifically from component "c-b"
        async def wait_for_b():
            return await view.wait_for_event(
                "item_clicked",
                predicate=lambda kw: kw.get("component_id") == "c-b",
                timeout=2.0,
            )

        waiter_task = asyncio.create_task(wait_for_b())
        await asyncio.sleep(0.01)

        # Click on c-a first — predicate should NOT match
        await consumer.handle_event(
            {
                "event": "item_clicked",
                "params": {"component_id": "c-a", "item_id": 1},
            }
        )
        # Waiter is still pending
        assert not waiter_task.done()

        # Click on c-b — predicate matches, waiter resolves
        await consumer.handle_event(
            {
                "event": "item_clicked",
                "params": {"component_id": "c-b", "item_id": 2},
            }
        )

        resolved = await waiter_task
        assert resolved["component_id"] == "c-b"
        assert resolved["item_id"] == 2

    @pytest.mark.asyncio
    async def test_multiple_parent_waiters_all_resolve(self):
        """Fan-out: multiple pending waiters for the same component
        event name should all resolve when the event fires. Matches
        the main LiveView branch's behavior."""
        view = _WaitingView()
        view._components["c1"] = _ClickComponent("c1")
        consumer = _make_consumer(view)

        async def waiter():
            return await view.wait_for_event("item_clicked", timeout=2.0)

        results: List[Dict[str, Any]] = []

        async def collect():
            results.append(await waiter())

        task1 = asyncio.create_task(collect())
        task2 = asyncio.create_task(collect())
        task3 = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await consumer.handle_event(
            {
                "event": "item_clicked",
                "params": {"component_id": "c1", "item_id": 99},
            }
        )

        await asyncio.gather(task1, task2, task3)
        assert len(results) == 3
        for r in results:
            assert r["item_id"] == 99
            assert r["component_id"] == "c1"

    @pytest.mark.asyncio
    async def test_parent_branch_still_works_for_non_component_events(self):
        """Regression guard: the original main-LiveView branch
        notification must still fire when ``component_id`` is absent.
        This is the pre-existing Phase 1b behavior — we didn't touch
        it, but a sloppy refactor could break it."""

        class _ViewWithHandler(WaiterMixin):
            def __init__(self) -> None:
                super().__init__()
                self._components: Dict[str, LiveComponent] = {}
                self._view_id = "root"
                self.saved: Optional[str] = None

            def _get_all_child_views(self) -> Dict[str, Any]:
                return {}

            @event_handler
            def save(self, name: str = "", **kwargs) -> None:
                self.saved = name

        view = _ViewWithHandler()
        consumer = _make_consumer(view)  # type: ignore[arg-type]

        async def wait_for_save():
            return await view.wait_for_event("save", timeout=2.0)

        task = asyncio.create_task(wait_for_save())
        await asyncio.sleep(0.01)

        await consumer.handle_event(
            {
                "event": "save",
                "params": {"name": "Q3 Planning"},
            }
        )

        resolved = await task
        assert view.saved == "Q3 Planning"
        assert resolved["name"] == "Q3 Planning"
        # No component_id was injected because this wasn't a component event
        assert "component_id" not in resolved

    @pytest.mark.asyncio
    async def test_waiter_notification_failure_does_not_break_handler(self, caplog):
        """If ``_notify_waiters`` raises for any reason, the
        exception is logged but the component handler's side effects
        are preserved. The consumer shouldn't pretend the event
        failed just because a waiter was buggy."""
        view = _WaitingView()
        view._components["c1"] = _ClickComponent("c1")

        # Monkey-patch _notify_waiters to raise
        def boom(event_name: str, kwargs: Dict[str, Any]) -> None:  # noqa: ARG001
            raise RuntimeError("simulated waiter bug")

        view._notify_waiters = boom  # type: ignore[method-assign]

        consumer = _make_consumer(view)

        with caplog.at_level("WARNING", logger="djust.websocket"):
            await consumer.handle_event(
                {
                    "event": "item_clicked",
                    "params": {"component_id": "c1", "item_id": 7},
                }
            )

        # Component handler still ran (side effect preserved)
        assert view._components["c1"].last_click == {"item_id": 7}
        # Warning was logged so operators can see the bug
        assert any(
            "Waiter notification for component event" in rec.message for rec in caplog.records
        )
