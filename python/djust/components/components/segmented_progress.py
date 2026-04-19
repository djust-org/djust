"""Segmented Progress component for multi-step progress indicators."""

import html
from typing import Dict, List, Optional, Union

from djust import Component


class SegmentedProgress(Component):
    """Style-agnostic segmented progress bar with labeled steps.

    Displays a multi-step progress indicator with numbered steps and labels.

    Usage in a LiveView::

        self.wizard = SegmentedProgress(
            steps=["Account", "Profile", "Review"],
            current=2,
        )

        # With dict steps
        self.flow = SegmentedProgress(
            steps=[{"label": "Cart"}, {"label": "Shipping"}, {"label": "Payment"}],
            current=1,
        )

    In template::

        {{ wizard|safe }}

    CSS Custom Properties::

        --dj-segmented-progress-active-bg: active step color
        --dj-segmented-progress-completed-bg: completed step color
        --dj-segmented-progress-pending-bg: pending step color

    Args:
        steps: List of step labels (strings) or dicts with "label" key
        current: Current step number (1-indexed)
        size: Size variant (sm, md, lg)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        steps: Optional[List[Union[str, Dict]]] = None,
        current: int = 0,
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            steps=steps or [],
            current=current,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.steps = steps or []
        self.current = current
        self.size = size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the segmented progress HTML."""
        classes = ["dj-segmented-progress", f"dj-segmented-progress--{self.size}"]

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        segments = []
        for i, step in enumerate(self.steps):
            if isinstance(step, dict):
                label = html.escape(str(step.get("label", "")))
            else:
                label = html.escape(str(step))
            step_num = i + 1
            if step_num < self.current:
                state = "completed"
            elif step_num == self.current:
                state = "active"
            else:
                state = "pending"
            segments.append(
                f'<div class="dj-segmented-progress__step dj-segmented-progress__step--{state}">'
                f'<div class="dj-segmented-progress__indicator">{step_num}</div>'
                f'<div class="dj-segmented-progress__label">{label}</div>'
                f"</div>"
            )

        parts = []
        for i, seg in enumerate(segments):
            parts.append(seg)
            if i < len(segments) - 1:
                step_num = i + 1
                line_state = "completed" if step_num < self.current else "pending"
                parts.append(
                    f'<div class="dj-segmented-progress__connector '
                    f'dj-segmented-progress__connector--{line_state}"></div>'
                )

        return f'<div class="{class_str}">{"".join(parts)}</div>'
