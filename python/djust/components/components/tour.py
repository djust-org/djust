"""Tour / Onboarding Guide component — product tour with spotlight highlights."""

import html
from typing import Optional

from djust import Component


class Tour(Component):
    """Product tour with spotlight highlights and step navigation.

    Renders a guided tour overlay with spotlight on target elements,
    popover descriptions, and next/prev/skip navigation.
    Uses ``dj-hook="Tour"`` for client-side positioning.

    Usage in a LiveView::

        self.tour = Tour(
            steps=[
                {"target": "#sidebar", "title": "Navigation",
                 "content": "Use the sidebar to navigate."},
                {"target": "#create-btn", "title": "Create",
                 "content": "Click here to create a new item."},
            ],
            active=0,
        )

    In template::

        {{ tour|safe }}

    CSS Custom Properties::

        --dj-tour-overlay-bg: overlay/backdrop color
        --dj-tour-popover-bg: popover background
        --dj-tour-popover-fg: popover text color
        --dj-tour-popover-radius: popover border radius
        --dj-tour-popover-shadow: popover box shadow
        --dj-tour-highlight-color: spotlight border color
        --dj-tour-highlight-radius: spotlight border radius

    Args:
        steps: list of step dicts with target, title, content
        active: index of current step (default 0)
        event: djust event prefix for navigation (fires event_next, event_prev, event_skip)
        show_progress: show step progress indicator (default True)
        show_skip: show skip button (default True)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        steps: Optional[list] = None,
        active: int = 0,
        event: str = "tour",
        show_progress: bool = True,
        show_skip: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            steps=steps,
            active=active,
            event=event,
            show_progress=show_progress,
            show_skip=show_skip,
            custom_class=custom_class,
            **kwargs,
        )
        self.steps = steps or []
        self.active = active
        self.event = event
        self.show_progress = show_progress
        self.show_skip = show_skip
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-tour"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not isinstance(self.steps, list) or not self.steps:
            return ""

        try:
            idx = int(self.active)
        except (ValueError, TypeError):
            idx = 0
        total = len(self.steps)
        idx = max(0, min(idx, total - 1))

        step = self.steps[idx]
        if not isinstance(step, dict):
            return ""

        e_event = html.escape(self.event)
        e_target = html.escape(str(step.get("target", "")))
        e_title = html.escape(str(step.get("title", "")))
        e_content = html.escape(str(step.get("content", "")))

        # Progress dots
        progress_html = ""
        if self.show_progress:
            dots = []
            for i in range(total):
                dot_cls = "dj-tour__dot"
                if i == idx:
                    dot_cls += " dj-tour__dot--active"
                elif i < idx:
                    dot_cls += " dj-tour__dot--completed"
                dots.append(f'<span class="{dot_cls}"></span>')
            progress_html = f'<div class="dj-tour__progress">{"".join(dots)}</div>'

        # Navigation buttons
        prev_btn = ""
        if idx > 0:
            prev_btn = (
                f'<button class="dj-tour__prev" type="button" '
                f'dj-click="{e_event}" data-value="prev">Back</button>'
            )

        next_label = "Finish" if idx == total - 1 else "Next"
        next_action = "finish" if idx == total - 1 else "next"
        next_btn = (
            f'<button class="dj-tour__next" type="button" '
            f'dj-click="{e_event}" data-value="{next_action}">{next_label}</button>'
        )

        skip_btn = ""
        if self.show_skip and idx < total - 1:
            skip_btn = (
                f'<button class="dj-tour__skip" type="button" '
                f'dj-click="{e_event}" data-value="skip">Skip tour</button>'
            )

        step_label = f'<span class="dj-tour__step-label">Step {idx + 1} of {total}</span>'

        return (
            f'<div class="{class_str}" dj-hook="Tour" '
            f'data-target="{e_target}" data-step="{idx}" '
            f'data-total="{total}" data-event="{e_event}" role="dialog" aria-modal="true">'
            f'<div class="dj-tour__overlay"></div>'
            f'<div class="dj-tour__popover">'
            f'<div class="dj-tour__header">'
            f'<h4 class="dj-tour__title">{e_title}</h4>'
            f"{step_label}</div>"
            f'<div class="dj-tour__body">'
            f'<p class="dj-tour__content">{e_content}</p></div>'
            f"{progress_html}"
            f'<div class="dj-tour__footer">'
            f"{skip_btn}{prev_btn}{next_btn}</div></div></div>"
        )
