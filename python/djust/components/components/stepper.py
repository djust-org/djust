"""Stepper component."""

import html

from djust import Component


class Stepper(Component):
    """Step indicator/wizard progress component.

    Args:
        steps: list of dicts with keys: label, complete (bool)
        active: 0-based index of current step
        event: dj-click event name for step navigation"""

    def __init__(
        self,
        steps: list = None,
        active: int = 0,
        event: str = "set_step",
        custom_class: str = "",
        **kwargs,
    ):
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
        """Render the stepper HTML."""
        steps = self.steps or []
        if not steps:
            return '<div class="stepper"></div>'
        cls = "stepper"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_event = html.escape(self.event)
        parts = []
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                lbl = html.escape(str(step.get("label", "")))
                complete = step.get("complete", False)
            else:
                lbl = html.escape(str(step))
                complete = False
            step_cls = "stepper-step"
            if i == self.active:
                step_cls += " stepper-step-active"
            if complete:
                step_cls += " stepper-step-complete"
            parts.append(
                f'<button class="{step_cls}" dj-click="{e_event}" data-value="{i}">'
                f'<span class="stepper-number">{i + 1}</span>'
                f'<span class="stepper-label">{lbl}</span>'
                f"</button>"
            )
        return f'<div class="{cls}">{"".join(parts)}</div>'
