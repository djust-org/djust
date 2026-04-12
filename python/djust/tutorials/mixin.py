"""
``TutorialMixin`` — declarative guided-tour state machine.

ADR-002 Phase 1c. See :mod:`djust.tutorials` for the overall design.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, List, Optional

from djust.decorators import background, event_handler
from djust.js import JS

from .step import TutorialStep

if TYPE_CHECKING:
    pass

logger = logging.getLogger("djust.tutorials")


class TutorialMixin:
    """
    Mixes a declarative guided-tour state machine into a LiveView.

    Usage::

        from djust import LiveView
        from djust.tutorials import TutorialMixin, TutorialStep

        class OnboardingView(TutorialMixin, LiveView):
            template_name = "onboarding.html"
            tutorial_steps = [
                TutorialStep(
                    target="#nav-dashboard",
                    message="This is your dashboard.",
                    timeout=4.0,
                ),
                TutorialStep(
                    target="#btn-new-project",
                    message="Click here to create your first project.",
                    wait_for="create_project",
                ),
            ]

    And in the template::

        {% load djust_tutorials %}
        <button dj-click="start_tutorial">Take the tour</button>
        {% tutorial_bubble %}

    The mixin exposes four event handlers:

    - ``start_tutorial()`` — kick off the tour as a background task.
      Calls ``push_commands`` to highlight + narrate each step and
      either ``wait_for_event`` or ``asyncio.sleep`` between steps.
    - ``skip_tutorial()`` — advance past the current step immediately.
      Triggers the current step's ``on_exit`` cleanup and moves to
      the next step. Used by "Next" buttons or skip links.
    - ``cancel_tutorial()`` — abort the tour entirely. Triggers the
      current step's cleanup and exits the loop.
    - ``restart_tutorial()`` — cancel any running tour and start a
      fresh one from step 0.

    And tracks three pieces of view-level state:

    - ``tutorial_running: bool`` — True while the tour is active.
    - ``tutorial_current_step: int`` — index of the current step
      (0-based), or ``-1`` if the tour isn't running.
    - ``tutorial_total_steps: int`` — length of ``tutorial_steps``
      for progress display.

    Apps can override any of these or add additional state without
    interfering with the mixin's behavior.
    """

    # Class-level default — override in subclasses with your tour.
    # Stored with a ``_`` prefix so ContextMixin's MRO walker doesn't
    # pick it up and try to serialize TutorialStep objects (#694).
    # The public ``tutorial_steps`` property provides read access.
    _tutorial_steps: List[TutorialStep] = []

    # Default highlight class if a step doesn't specify one. Apps can
    # override this at the class level to change the default look.
    default_highlight_class: str = "tour-highlight"

    def __init_subclass__(cls, **kwargs) -> None:
        """Move ``tutorial_steps`` to ``_tutorial_steps`` at class creation.

        If a subclass defines ``tutorial_steps`` (the documented public
        name), migrate it to ``_tutorial_steps`` so the MRO walker in
        ``ContextMixin`` never sees non-serializable ``TutorialStep``
        objects in the template context (#694).
        """
        super().__init_subclass__(**kwargs)
        if "tutorial_steps" in cls.__dict__:
            cls._tutorial_steps = cls.__dict__["tutorial_steps"]
            # Remove the public name from class dict so it doesn't
            # appear in the MRO walker's class-level scan.
            try:
                delattr(cls, "tutorial_steps")
            except AttributeError:
                pass

    @property
    def tutorial_steps(self) -> List[TutorialStep]:
        """Read-only access to the step list."""
        return self._tutorial_steps

    # Instance state — initialized in __init__ via super().
    # These use _ prefix to avoid triggering VDOM re-renders when the
    # tour background task changes them. Templates that need these values
    # should access them via get_context_data (which the mixin populates).
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tutorial_running: bool = False
        self._tutorial_current_step: int = -1
        self._tutorial_total_steps: int = len(self._tutorial_steps)

    @property
    def tutorial_running(self) -> bool:
        return self._tutorial_running

    @tutorial_running.setter
    def tutorial_running(self, value: bool) -> None:
        self._tutorial_running = value

    @property
    def tutorial_current_step(self) -> int:
        return self._tutorial_current_step

    @tutorial_current_step.setter
    def tutorial_current_step(self, value: int) -> None:
        self._tutorial_current_step = value

    @property
    def tutorial_total_steps(self) -> int:
        return self._tutorial_total_steps

    @tutorial_total_steps.setter
    def tutorial_total_steps(self, value: int) -> None:
        self._tutorial_total_steps = value
        # Internal: track the active step's target selector + class so
        # cancel/skip paths know what to clean up.
        self._tutorial_active_target: Optional[str] = None
        self._tutorial_active_class: Optional[str] = None
        # Signalled when the user skips or cancels, to unblock the
        # current step's wait_for_event / sleep.
        self._tutorial_skip_signal: Optional[asyncio.Event] = None
        self._tutorial_cancel_signal: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @event_handler
    @background
    async def start_tutorial(self, **kwargs) -> None:
        """
        Begin the tour. Runs as a ``@background`` task so the handler
        returns quickly and subsequent steps don't block the event
        dispatch loop.

        Idempotent — calling while a tour is already running is a
        no-op. Use :meth:`restart_tutorial` to abort and restart.
        """
        if self.tutorial_running:
            logger.debug("start_tutorial called but tutorial is already running")
            return
        if not self.tutorial_steps:
            logger.warning(
                "start_tutorial called on %s but tutorial_steps is empty",
                type(self).__name__,
            )
            return

        self.tutorial_running = True
        self.tutorial_current_step = -1
        self._tutorial_skip_signal = asyncio.Event()
        self._tutorial_cancel_signal = asyncio.Event()

        try:
            for idx, step in enumerate(self.tutorial_steps):
                if self._tutorial_cancel_signal.is_set():
                    break
                self.tutorial_current_step = idx
                await self._run_step(step)
        except asyncio.CancelledError:
            # View tore down (disconnect). Let the exception bubble.
            logger.debug("Tutorial cancelled via asyncio.CancelledError")
            raise
        except Exception as exc:
            logger.warning("Tutorial run failed at step %s: %s", self.tutorial_current_step, exc)
        finally:
            self._cleanup_active_step()
            self.tutorial_running = False
            self.tutorial_current_step = -1
            self._tutorial_skip_signal = None
            self._tutorial_cancel_signal = None
            # Hide the bubble when the tour ends (whether completed,
            # cancelled, or errored). The bubble listens for tour:hide.
            try:
                self.push_commands(JS.dispatch("tour:hide"))
                await self._flush_pending_push_events()
            except Exception as exc:
                logger.debug("Tutorial hide push failed: %s", exc)

    @event_handler
    def skip_tutorial(self, **kwargs) -> None:
        """
        Advance past the current step immediately.

        If no tour is running, this is a no-op. Otherwise signal the
        current step's waiter to unblock so the loop advances to the
        next step.
        """
        if not self.tutorial_running:
            return
        if self._tutorial_skip_signal is not None:
            self._tutorial_skip_signal.set()

    @event_handler
    def cancel_tutorial(self, **kwargs) -> None:
        """
        Abort the tour entirely, or dismiss the bubble if the tour
        already ended.

        Signals both the skip and cancel events so the current step
        unblocks and the loop exits on the next iteration. Also always
        hides the bubble via push_commands so the Close button works
        even after the tour completes naturally.
        """
        # Always hide the bubble — the Close button should work even
        # after the tour ends naturally.
        self.push_commands(JS.dispatch("tour:hide"))
        if not self.tutorial_running:
            return
        if self._tutorial_cancel_signal is not None:
            self._tutorial_cancel_signal.set()
        if self._tutorial_skip_signal is not None:
            self._tutorial_skip_signal.set()

    @event_handler
    async def restart_tutorial(self, **kwargs) -> None:
        """Cancel any running tour and start fresh from step 0."""
        if self.tutorial_running:
            self.cancel_tutorial()
            # Give the loop a tick to notice the cancel
            await asyncio.sleep(0.01)
        await self.start_tutorial()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_step(self, step: TutorialStep) -> None:
        """
        Execute one tour step: highlight, narrate, wait, clean up.

        Order of operations:
          1. Push the default highlight + narrate chain (add class,
             dispatch narrate event, optional focus for accessibility)
          2. Push the step's ``on_enter`` chain if set
          3. Wait for the user action (wait_for + optional timeout)
             OR sleep for ``timeout`` seconds (auto-advance steps)
             OR exit immediately if no wait_for and no timeout
          4. Push the step's ``on_exit`` chain if set
          5. Push the default cleanup chain (remove class, dismiss bubble)
        """
        self._tutorial_active_target = step.target
        self._tutorial_active_class = step.highlight_class or self.default_highlight_class

        # 1. Default setup chain — highlight + narrate + focus for a11y
        setup = (
            JS.add_class(self._tutorial_active_class, to=step.target)
            .dispatch(
                step.narrate_event,
                detail={
                    "text": step.message,
                    "target": step.target,
                    "position": step.position,
                    "step": self.tutorial_current_step,
                    "total": self.tutorial_total_steps,
                },
            )
            .focus(step.target)
        )
        self.push_commands(setup)

        # 2. Custom per-step setup
        if step.on_enter is not None:
            self.push_commands(step.on_enter)

        # Flush push events immediately so the client sees the
        # highlight + narrate before we block on the wait phase.
        # Without this, push_commands inside a @background task queue
        # up but never reach the client until the entire tour finishes.
        await self._flush_pending_push_events()

        # 3. Wait phase
        await self._wait_for_step(step)

        # 4. Custom per-step teardown
        if step.on_exit is not None:
            self.push_commands(step.on_exit)

        # 5. Default cleanup chain
        cleanup = JS.remove_class(self._tutorial_active_class, to=step.target)
        self.push_commands(cleanup)

        # Flush cleanup commands before advancing to the next step
        await self._flush_pending_push_events()

        self._tutorial_active_target = None
        self._tutorial_active_class = None

    async def _wait_for_step(self, step: TutorialStep) -> None:
        """
        Suspend between setup and cleanup per the step's configuration.

        Four scenarios:
          - ``wait_for=X, timeout=None``: wait for event X indefinitely
            (or until skip/cancel)
          - ``wait_for=X, timeout=T``: wait for event X up to T seconds
            (or until skip/cancel) — on timeout, advance silently
          - ``wait_for=None, timeout=T``: sleep for T seconds (or until
            skip/cancel), then auto-advance
          - ``wait_for=None, timeout=None``: don't wait at all, advance
            immediately (useful for terminal "tour done" steps that
            just flash a message)

        Skip and cancel signals are checked alongside the wait via
        ``asyncio.wait(..., return_when=FIRST_COMPLETED)`` so either
        user action unblocks the step.
        """
        if step.wait_for is None and step.timeout is None:
            return

        if step.wait_for is None:
            # Pure auto-advance — race timeout against skip/cancel
            await self._race_with_skip(asyncio.sleep(step.timeout))
            return

        # wait_for is set — call the Phase 1b primitive
        waiter = self.wait_for_event(step.wait_for, timeout=step.timeout)
        try:
            await self._race_with_skip(waiter)
        except asyncio.TimeoutError:
            # Timeout on a wait_for step advances silently — the tour
            # continues to the next step rather than aborting. Apps
            # that want "abort on timeout" can check
            # tutorial_current_step after the tour ends.
            logger.debug(
                "Tutorial step %s wait_for %r timed out — advancing",
                self.tutorial_current_step,
                step.wait_for,
            )

    async def _race_with_skip(self, coro) -> None:
        """
        Run ``coro`` but return early if the skip or cancel signal
        fires first. Propagates ``TimeoutError`` from the wrapped
        coroutine.
        """
        if self._tutorial_skip_signal is None:
            # Shouldn't happen since _run_step is called from inside
            # start_tutorial which initializes the signal, but guard
            # defensively.
            await coro
            return

        skip_task = asyncio.create_task(self._tutorial_skip_signal.wait())
        wait_task = asyncio.create_task(self._await_coro(coro))
        try:
            done, pending = await asyncio.wait(
                {skip_task, wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            # Re-raise any exception (notably TimeoutError) from the wait
            if wait_task in done:
                exc = wait_task.exception()
                if exc is not None:
                    raise exc
        finally:
            # Reset skip signal so the next step starts fresh
            if self._tutorial_skip_signal is not None:
                self._tutorial_skip_signal.clear()

    @staticmethod
    async def _await_coro(coro):
        """Helper: wrap a coroutine so asyncio.wait can observe it."""
        return await coro

    def _cleanup_active_step(self) -> None:
        """
        Remove any highlight class still applied to the active step's
        target. Called from the ``start_tutorial`` finally block so
        cancel/exception paths don't leave stale highlights.
        """
        if not self._tutorial_active_target or not self._tutorial_active_class:
            return
        try:
            self.push_commands(
                JS.remove_class(
                    self._tutorial_active_class,
                    to=self._tutorial_active_target,
                )
            )
        except Exception as exc:
            logger.debug("Tutorial cleanup push failed: %s", exc)
        self._tutorial_active_target = None
        self._tutorial_active_class = None
