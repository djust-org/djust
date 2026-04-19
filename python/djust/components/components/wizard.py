"""Wizard / multi-step form component."""

import html

from djust import Component


class Wizard(Component):
    """Style-agnostic multi-step form wizard using CSS custom properties.

    Splits a form across numbered steps with per-step validation.

    Usage in a LiveView::

        self.wizard = Wizard(
            steps=[
                {"id": "info", "label": "Info"},
                {"id": "payment", "label": "Payment"},
                {"id": "confirm", "label": "Confirm"},
            ],
            active="info",
            event="set_step",
        )

    In template::

        {{ wizard|safe }}

    Args:
        steps: List of step dicts with id and label keys
        active: ID of the currently active step
        event: djust event for step navigation
        show_numbers: Show step numbers (default: True)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        steps: list = None,
        active: str = "",
        event: str = "set_step",
        show_numbers: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            steps=steps,
            active=active,
            event=event,
            show_numbers=show_numbers,
            custom_class=custom_class,
            **kwargs,
        )
        self.steps = steps or []
        self.active = active
        self.event = event
        self.show_numbers = show_numbers
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-wizard"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_event = html.escape(self.event)

        # Find active index
        active_idx = 0
        for i, step in enumerate(self.steps):
            if step.get("id") == self.active:
                active_idx = i
                break

        # Step indicators
        indicators = []
        for i, step in enumerate(self.steps):
            step_id = html.escape(str(step.get("id", "")))
            step_label = html.escape(str(step.get("label", "")))
            step_cls = "dj-wizard__step"
            if i < active_idx:
                step_cls += " dj-wizard__step--completed"
            elif i == active_idx:
                step_cls += " dj-wizard__step--active"

            number_html = ""
            if self.show_numbers:
                number_html = f'<span class="dj-wizard__number">{i + 1}</span>'

            indicators.append(
                f'<button class="{step_cls}" '
                f'dj-click="{e_event}" data-value="{step_id}">'
                f"{number_html}"
                f'<span class="dj-wizard__label">{step_label}</span>'
                f"</button>"
            )

        # Connector lines between steps
        nav_items = []
        for i, ind in enumerate(indicators):
            nav_items.append(ind)
            if i < len(indicators) - 1:
                conn_cls = "dj-wizard__connector"
                if i < active_idx:
                    conn_cls += " dj-wizard__connector--completed"
                nav_items.append(f'<div class="{conn_cls}"></div>')

        nav = f'<nav class="dj-wizard__nav" role="tablist">{"".join(nav_items)}</nav>'

        return f'<div class="{class_str}">{nav}</div>'
