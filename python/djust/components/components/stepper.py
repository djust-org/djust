"""Stepper component."""

import html

from djust import Component
from typing import Any, Optional


class Stepper(Component):
    """Step indicator/wizard progress component.

    Args:
        steps: list of dicts with keys: label, complete (bool)
        active: 0-based index of current step
        event: dj-click event name for step navigation

    Renders an ordered list of buttons: the current step is ``aria-current="step"``
    and a completed one is announced as such."""

    def __init__(
        self,
        steps: Optional[list] = None,
        active: int = 0,
        event: str = "set_step",
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            steps=steps,
            active=active,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.steps = steps or []
        self.active = active
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the stepper HTML.

        The same structure as ``{% stepper %}`` (``render_stepper``, #3970): an ordered list of
        buttons, ``aria-current="step"`` on the current one and the completed state in text. The
        buttons' event attributes are ``event_attrs``' (ADR-033), which is why this is not a call
        to that helper.
        """
        steps = self.steps or []
        cls = "stepper"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        parts = []
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                lbl = html.escape(str(step.get("label", "")))
                complete = bool(step.get("complete", False))
            else:
                lbl = html.escape(str(step))
                complete = False
            current = i == self.active
            step_cls = "stepper-step"
            if current:
                step_cls += " stepper-step-active"
            if complete:
                step_cls += " stepper-step-complete"
            aria = ' aria-current="step"' if current else ""
            done = ' <span class="sr-only"> (completed)</span>' if complete and not current else ""
            parts.append(
                f'<li class="{step_cls}"><button type="button" class="stepper-step-button"{aria} '
                f"{self.event_attrs(self.event, value=i)}>"
                f'<span class="stepper-number">{i + 1}</span> '
                f'<span class="stepper-label">{lbl}</span>{done}'
                f"</button></li>"
            )
        return f'<ol class="{cls}">{"".join(parts)}</ol>'
