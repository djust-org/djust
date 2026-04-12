"""Tests for ``TutorialMixin`` — ADR-002 Phase 1c.

Covers the declarative guided-tour state machine: step progression,
highlight/narrate push_commands chains, skip/cancel handling,
wait_for_event integration, auto-advance timeouts, and cleanup on
cancellation or view teardown.

``TutorialStep`` is tested for its validation + defaults.
``TutorialMixin`` is tested end-to-end via a minimal ``FakeView`` that
composes ``WaiterMixin`` + ``PushEventMixin`` + ``TutorialMixin`` —
no Django, no WebSocket, no state backend. The integration with the
actual dispatch path (handle_event calling _notify_waiters) is covered
implicitly via the waiter tests from Phase 1b.

Note on ``@event_handler`` + ``@background`` decorators: in the real
LiveView the handlers run inside the WebSocket consumer's dispatch
loop. For unit tests we bypass the decorators and call the underlying
implementation directly via a helper that strips the background wrapper.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from djust.js import JS
from djust.mixins.push_events import PushEventMixin
from djust.mixins.waiters import WaiterMixin
from djust.tutorials import TutorialMixin, TutorialStep


class _View(WaiterMixin, PushEventMixin, TutorialMixin):
    """Minimal host for TutorialMixin unit tests.

    Implements a stub ``start_async`` that runs the coroutine inline
    instead of scheduling it. The real :class:`AsyncWorkMixin` schedules
    work via ``asyncio.create_task``, but for unit tests we want
    deterministic synchronous execution of the mixin's state machine.
    """

    tutorial_steps = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._async_inline_tasks: list[asyncio.Task] = []

    def start_async(self, callback, *args, name: str = None, **kwargs):
        """Stub for AsyncWorkMixin.start_async — runs callback as a task.

        The @background decorator calls ``self.start_async(_async_callback,
        name=...)`` with a zero-arg callback that wraps the handler. We
        create_task it so tests can ``await`` the resulting coroutine via
        the task list or wait for the full tour to finish via asyncio.
        """
        task = asyncio.create_task(_maybe_coro(callback(*args, **kwargs)))
        self._async_inline_tasks.append(task)
        return task

    async def _await_all_async(self) -> None:
        """Wait for all inline async tasks to complete."""
        while self._async_inline_tasks:
            task = self._async_inline_tasks.pop(0)
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _maybe_coro(result):
    """If ``result`` is a coroutine, await it; otherwise return it."""
    if inspect.iscoroutine(result):
        return await result
    return result


async def _call(method, *args, **kwargs):
    """
    Call an ``@event_handler``-decorated method. If the method is
    ``@background`` it returns immediately after scheduling via
    ``start_async`` (stubbed on ``_View``) — callers await the full
    tour completion via ``_View._await_all_async()``.
    """
    result = method(*args, **kwargs)
    if inspect.iscoroutine(result):
        return await result
    return result


async def _run_tour_to_completion(view) -> None:
    """Start the tour and wait for all scheduled async tasks to finish."""
    await _call(view.start_tutorial)
    await view._await_all_async()


# ---------------------------------------------------------------------------
# TutorialStep
# ---------------------------------------------------------------------------


class TestTutorialStep:
    def test_minimal_step(self):
        step = TutorialStep(target="#btn", message="Click here.")
        assert step.target == "#btn"
        assert step.message == "Click here."
        assert step.position == "bottom"
        assert step.wait_for is None
        assert step.timeout is None
        assert step.on_enter is None
        assert step.on_exit is None
        assert step.highlight_class == "tour-highlight"
        assert step.narrate_event == "tour:narrate"

    def test_custom_position(self):
        step = TutorialStep(target="#btn", message="Hi", position="top")
        assert step.position == "top"

    def test_invalid_position_raises(self):
        with pytest.raises(ValueError, match="position must be one of"):
            TutorialStep(target="#btn", message="Hi", position="diagonal")

    def test_empty_target_raises(self):
        with pytest.raises(ValueError, match="target is required"):
            TutorialStep(target="", message="Hi")

    def test_empty_message_allowed(self):
        """Empty message is legal — lets apps build silent steps that
        only execute on_enter/on_exit chains without popping the bubble."""
        step = TutorialStep(target="#btn", message="")
        assert step.message == ""

    def test_wait_for_and_timeout(self):
        step = TutorialStep(
            target="#form",
            message="Submit the form.",
            wait_for="submit_form",
            timeout=30.0,
        )
        assert step.wait_for == "submit_form"
        assert step.timeout == 30.0

    def test_on_enter_on_exit_chains(self):
        enter = JS.focus("#btn")
        exit = JS.dispatch("tour:step-done")
        step = TutorialStep(
            target="#btn",
            message="Click.",
            on_enter=enter,
            on_exit=exit,
        )
        assert step.on_enter is enter
        assert step.on_exit is exit


# ---------------------------------------------------------------------------
# TutorialMixin — basic lifecycle
# ---------------------------------------------------------------------------


class TestTutorialLifecycle:
    @pytest.mark.asyncio
    async def test_not_running_initially(self):
        view = _View()
        assert view.tutorial_running is False
        assert view.tutorial_current_step == -1
        assert view.tutorial_total_steps == 0

    @pytest.mark.asyncio
    async def test_empty_steps_no_op(self, caplog):
        view = _View()
        # tutorial_steps is empty; start should return without running
        await _run_tour_to_completion(view)
        assert view.tutorial_running is False
        assert view.tutorial_current_step == -1

    @pytest.mark.asyncio
    async def test_single_auto_advance_step_runs_and_finishes(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(target="#a", message="Hi.", timeout=0.05),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)
        # After the run, state is reset
        assert view.tutorial_running is False
        assert view.tutorial_current_step == -1

    @pytest.mark.asyncio
    async def test_setup_and_cleanup_chains_pushed(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(target="#step1", message="First.", timeout=0.05),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        assert len(events) == 2
        # Setup: add_class + dispatch + focus
        setup_name, setup_payload = events[0]
        assert setup_name == "djust:exec"
        setup_ops = setup_payload["ops"]
        assert setup_ops[0][0] == "add_class"
        assert setup_ops[0][1]["to"] == "#step1"
        assert setup_ops[0][1]["names"] == "tour-highlight"
        assert setup_ops[1][0] == "dispatch"
        assert setup_ops[1][1]["event"] == "tour:narrate"
        assert setup_ops[1][1]["detail"]["text"] == "First."
        assert setup_ops[1][1]["detail"]["target"] == "#step1"
        assert setup_ops[1][1]["detail"]["step"] == 0
        assert setup_ops[1][1]["detail"]["total"] == 1
        assert setup_ops[2][0] == "focus"

        # Cleanup: remove_class
        cleanup_name, cleanup_payload = events[1]
        cleanup_ops = cleanup_payload["ops"]
        assert cleanup_ops[0][0] == "remove_class"
        assert cleanup_ops[0][1]["to"] == "#step1"

    @pytest.mark.asyncio
    async def test_multi_step_progresses_in_order(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(target="#a", message="First.", timeout=0.02),
                TutorialStep(target="#b", message="Second.", timeout=0.02),
                TutorialStep(target="#c", message="Third.", timeout=0.02),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        # 3 steps * 2 events per step (setup + cleanup)
        assert len(events) == 6

        # Every setup event should reference the right target in order
        setups = [e for e in events if e[1]["ops"][0][0] == "add_class"]
        targets = [s[1]["ops"][0][1]["to"] for s in setups]
        assert targets == ["#a", "#b", "#c"]

    @pytest.mark.asyncio
    async def test_idempotent_start_while_running(self, caplog):
        class V(_View):
            tutorial_steps = [
                TutorialStep(target="#a", message="Hi.", timeout=0.1),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        # Start once
        await _call(view.start_tutorial)  # schedules background tour
        await asyncio.sleep(0.02)
        assert view.tutorial_running is True
        # Second start while running is a no-op — doesn't restart
        await _call(view.start_tutorial)
        # Wait for the original tour to finish
        await view._await_all_async()
        assert view.tutorial_running is False


# ---------------------------------------------------------------------------
# wait_for_event integration
# ---------------------------------------------------------------------------


class TestWaitForEventIntegration:
    @pytest.mark.asyncio
    async def test_step_waits_for_user_action(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#btn",
                    message="Click me.",
                    wait_for="user_click",
                    timeout=2.0,
                ),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)

        await _call(view.start_tutorial)  # schedules background tour
        # Let the tour start and enter step 0
        await asyncio.sleep(0.02)
        assert view.tutorial_current_step == 0
        # Notify the waiter — step advances, tour finishes
        view._notify_waiters("user_click", {"clicked_at": 42})
        await view._await_all_async()

        assert view.tutorial_running is False

    @pytest.mark.asyncio
    async def test_wait_for_timeout_advances_silently(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#btn",
                    message="Click me.",
                    wait_for="never_fires",
                    timeout=0.1,
                ),
                TutorialStep(target="#other", message="Done.", timeout=0.02),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        # Tour should have completed both steps despite step 0 timing out
        assert view.tutorial_running is False
        events = view._drain_push_events()
        # 2 steps * 2 events each
        assert len(events) == 4

    @pytest.mark.asyncio
    async def test_wait_for_without_timeout_waits_indefinitely(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#btn",
                    message="Click me.",
                    wait_for="user_click",
                ),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)

        await _call(view.start_tutorial)  # schedules background tour
        await asyncio.sleep(0.05)
        # Still running — no timeout
        assert view.tutorial_running is True
        # Fire the event, tour finishes
        view._notify_waiters("user_click", {})
        await view._await_all_async()
        assert view.tutorial_running is False


# ---------------------------------------------------------------------------
# Skip and cancel
# ---------------------------------------------------------------------------


class TestSkipAndCancel:
    @pytest.mark.asyncio
    async def test_skip_advances_to_next_step(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#a",
                    message="First.",
                    wait_for="never",
                    timeout=5.0,
                ),
                TutorialStep(target="#b", message="Second.", timeout=0.02),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _call(view.start_tutorial)  # schedules background tour
        await asyncio.sleep(0.02)

        # Skip the blocking first step
        view.skip_tutorial()
        await view._await_all_async()

        assert view.tutorial_running is False
        events = view._drain_push_events()
        # Both steps should have setup + cleanup
        assert len(events) >= 4

    @pytest.mark.asyncio
    async def test_cancel_aborts_tour(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#a",
                    message="First.",
                    wait_for="never",
                    timeout=5.0,
                ),
                TutorialStep(target="#b", message="Second.", timeout=0.02),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _call(view.start_tutorial)  # schedules background tour
        await asyncio.sleep(0.02)

        view.cancel_tutorial()
        await view._await_all_async()

        assert view.tutorial_running is False
        # The second step should NOT have run — only the first step's
        # setup + cleanup should be in the queue
        events = view._drain_push_events()
        setups = [e for e in events if e[1]["ops"][0][0] == "add_class"]
        assert len(setups) == 1
        assert setups[0][1]["ops"][0][1]["to"] == "#a"

    @pytest.mark.asyncio
    async def test_skip_when_not_running_is_noop(self):
        view = _View()
        view.skip_tutorial()  # Should not raise
        assert view.tutorial_running is False

    @pytest.mark.asyncio
    async def test_cancel_when_not_running_is_noop(self):
        view = _View()
        view.cancel_tutorial()  # Should not raise
        assert view.tutorial_running is False


# ---------------------------------------------------------------------------
# on_enter / on_exit chains
# ---------------------------------------------------------------------------


class TestOnEnterOnExit:
    @pytest.mark.asyncio
    async def test_on_enter_pushed_after_setup(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#btn",
                    message="Click.",
                    timeout=0.02,
                    on_enter=JS.dispatch("custom:setup"),
                ),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        # Setup chain, on_enter chain, cleanup chain
        assert len(events) == 3
        # on_enter should appear between setup (add_class) and cleanup (remove_class)
        on_enter_event = events[1]
        assert on_enter_event[1]["ops"][0][0] == "dispatch"
        assert on_enter_event[1]["ops"][0][1]["event"] == "custom:setup"

    @pytest.mark.asyncio
    async def test_on_exit_pushed_before_cleanup(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#btn",
                    message="Click.",
                    timeout=0.02,
                    on_exit=JS.dispatch("custom:teardown"),
                ),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        # Setup chain, on_exit chain, cleanup chain
        assert len(events) == 3
        on_exit_event = events[1]
        assert on_exit_event[1]["ops"][0][0] == "dispatch"
        assert on_exit_event[1]["ops"][0][1]["event"] == "custom:teardown"


# ---------------------------------------------------------------------------
# Custom highlight class
# ---------------------------------------------------------------------------


class TestHighlightClass:
    @pytest.mark.asyncio
    async def test_default_highlight_class(self):
        class V(_View):
            tutorial_steps = [TutorialStep(target="#a", message="Hi.", timeout=0.02)]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        # First op is add_class with default "tour-highlight"
        assert events[0][1]["ops"][0][1]["names"] == "tour-highlight"

    @pytest.mark.asyncio
    async def test_custom_highlight_class(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#a",
                    message="Hi.",
                    timeout=0.02,
                    highlight_class="my-highlight",
                ),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        # add_class uses the custom class
        assert events[0][1]["ops"][0][1]["names"] == "my-highlight"
        # remove_class also uses the custom class
        assert events[1][1]["ops"][0][1]["names"] == "my-highlight"


# ---------------------------------------------------------------------------
# Narration event name
# ---------------------------------------------------------------------------


class TestNarrateEvent:
    @pytest.mark.asyncio
    async def test_default_narrate_event(self):
        class V(_View):
            tutorial_steps = [TutorialStep(target="#a", message="Hi.", timeout=0.02)]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        dispatch_op = events[0][1]["ops"][1]
        assert dispatch_op[0] == "dispatch"
        assert dispatch_op[1]["event"] == "tour:narrate"

    @pytest.mark.asyncio
    async def test_custom_narrate_event(self):
        class V(_View):
            tutorial_steps = [
                TutorialStep(
                    target="#a",
                    message="Hi.",
                    timeout=0.02,
                    narrate_event="my:custom-narrate",
                ),
            ]

        view = V()
        view.tutorial_total_steps = len(V.tutorial_steps)
        await _run_tour_to_completion(view)

        events = view._drain_push_events()
        dispatch_op = events[0][1]["ops"][1]
        assert dispatch_op[1]["event"] == "my:custom-narrate"
