"""
``TutorialStep`` dataclass — one step in a guided tour.

See :mod:`djust.tutorials` for the overall design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from djust.js import JSChain


@dataclass
class TutorialStep:
    """
    Declarative description of one step in a guided tour.

    A tour is a list of these. :class:`TutorialMixin` runs them in
    order: for each step it adds ``highlight_class`` to ``target``,
    fires a ``tour:narrate`` event carrying ``message`` (which the
    ``{% tutorial_bubble %}`` template tag picks up and renders),
    then either waits for a user event (``wait_for``) or auto-advances
    after ``timeout`` seconds.

    Attributes:
        target: CSS selector of the element to highlight. Required.
        message: Text shown in the narration bubble. Required (use
            the empty string to skip narration for a step).
        position: Where the bubble should appear relative to the
            target. One of ``"top"``, ``"bottom"`` (default),
            ``"left"``, ``"right"``. Rendered via the bubble's
            ``data-position`` attribute and styled by the shipped
            default CSS (or the app's override).
        wait_for: Optional name of an ``@event_handler`` to suspend
            on. The mixin calls ``wait_for_event(wait_for)`` (see
            Phase 1b) and advances only when that handler runs. If
            ``None`` (default), the step auto-advances after
            ``timeout`` seconds — set ``timeout`` for this case.
        timeout: Seconds before the step auto-advances (when
            ``wait_for`` is ``None``) or gives up on a pending
            ``wait_for`` waiter. Default ``None`` = wait indefinitely
            when ``wait_for`` is set, advance immediately when it
            isn't (useful for terminal "the tour is done" steps).
        on_enter: Optional :class:`djust.js.JSChain` to push *in
            addition to* the default highlight + narrate chain when
            this step begins. Use for custom per-step setup (scroll
            into view, set an extra attribute, etc.).
        on_exit: Optional :class:`djust.js.JSChain` to push *in
            addition to* the default cleanup chain when this step
            ends (advance or cancel). Use for custom per-step
            teardown.
        highlight_class: CSS class added to the target during the
            step. Default ``"tour-highlight"``. Apps can override
            per-step to use a different visual treatment.
        narrate_event: Name of the CustomEvent dispatched with the
            message. Default ``"tour:narrate"``. The shipped
            ``{% tutorial_bubble %}`` template tag listens for this
            name; apps that render a custom bubble can pick a
            different event name and bind their own listener.

    Example::

        TutorialStep(
            target="#project-form [name=title]",
            message="Give it a title — anything works.",
            wait_for="form_input_title",
            on_enter=JS.focus("#project-form [name=title]"),
            timeout=120.0,
        )
    """

    target: str
    message: str
    position: Literal["top", "bottom", "left", "right"] = "bottom"
    wait_for: Optional[str] = None
    timeout: Optional[float] = None
    on_enter: Optional["JSChain"] = None
    on_exit: Optional["JSChain"] = None
    highlight_class: str = "tour-highlight"
    narrate_event: str = "tour:narrate"

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("TutorialStep.target is required (CSS selector)")
        if self.position not in ("top", "bottom", "left", "right"):
            raise ValueError(
                f"TutorialStep.position must be one of top/bottom/left/right, got {self.position!r}"
            )
