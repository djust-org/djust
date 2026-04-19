"""Progress bar component for task completion indicators."""

import html
from typing import Optional

from djust import Component


class Progress(Component):
    """Style-agnostic progress bar component using CSS custom properties.

    Displays a horizontal bar indicating completion percentage.

    Usage in a LiveView::

        # Basic progress
        self.upload = Progress(value=65, max=100)

        # With label and variant
        self.build = Progress(
            value=3,
            max=10,
            label="Step 3 of 10",
            variant="success",
            show_value=True,
        )

        # Small size
        self.mini = Progress(value=80, size="sm")

    In template::

        {{ upload|safe }}
        {{ build|safe }}

    CSS Custom Properties::

        --dj-progress-bg: track background color
        --dj-progress-bar-bg: filled bar color
        --dj-progress-radius: border radius (default: 9999px)
        --dj-progress-height: bar height (default varies by size)

        # Variant-specific bar colors
        --dj-progress-success-bg
        --dj-progress-info-bg
        --dj-progress-warning-bg
        --dj-progress-danger-bg

    Args:
        value: Current progress value
        max: Maximum value (default: 100)
        label: Optional label text displayed above the bar
        variant: Color variant (default, success, info, warning, danger)
        size: Size variant (sm, md, lg)
        show_value: Whether to display percentage text
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        value: int = 0,
        max: int = 100,
        label: Optional[str] = None,
        variant: str = "default",
        size: str = "md",
        show_value: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            value=value,
            max=max,
            label=label,
            variant=variant,
            size=size,
            show_value=show_value,
            custom_class=custom_class,
            **kwargs,
        )
        self.value = value
        self.max = max
        self.label = label
        self.variant = variant
        self.size = size
        self.show_value = show_value
        self.custom_class = custom_class

    @property
    def percentage(self) -> float:
        """Calculate the completion percentage, clamped to 0-100."""
        if self.max <= 0:
            return 0
        return min(100, max(0, (self.value / self.max) * 100))

    def _render_custom(self) -> str:
        """Render the progress bar HTML."""
        classes = ["dj-progress"]

        if self.variant != "default":
            classes.append(f"dj-progress-{self.variant}")

        if self.size != "md":
            classes.append(f"dj-progress-{self.size}")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)
        pct = self.percentage

        parts = []

        if self.label:
            parts.append(f'<div class="dj-progress-label">{html.escape(self.label)}</div>')

        bar_parts = []
        bar_parts.append(
            f'<div class="dj-progress-bar" role="progressbar" '
            f'aria-valuenow="{self.value}" aria-valuemin="0" aria-valuemax="{self.max}" '
            f'style="width:{pct:.1f}%"></div>'
        )
        bar_content = "".join(bar_parts)
        parts.append(f'<div class="dj-progress-track">{bar_content}</div>')

        if self.show_value:
            parts.append(f'<span class="dj-progress-value">{pct:.0f}%</span>')

        content = "".join(parts)
        return f'<div class="{class_str}">{content}</div>'
