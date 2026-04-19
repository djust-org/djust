"""Progress Circle component for circular SVG progress indicators."""

import html

from djust import Component


class ProgressCircle(Component):
    """Style-agnostic circular progress indicator using SVG stroke-dasharray.

    Usage in a LiveView::

        self.upload = ProgressCircle(value=65, size="md")
        self.build = ProgressCircle(value=100, size="lg", color="success")

    In template::

        {{ upload|safe }}

    CSS Custom Properties::

        --dj-progress-circle-track: track color (default: #e5e7eb)
        --dj-progress-circle-fill: fill color (default: #3b82f6)
        --dj-progress-circle-value-color: text color (default: #111827)

    Args:
        value: Progress percentage (0-100)
        size: Size variant (sm, md, lg)
        color: Color variant (primary, success, warning, danger)
        show_value: Whether to display percentage text (default: True)
        custom_class: Additional CSS classes
    """

    SIZES = {"sm": 48, "md": 80, "lg": 120}
    STROKE_WIDTHS = {"sm": 4, "md": 6, "lg": 8}
    FONT_SIZES = {"sm": "0.625rem", "md": "1rem", "lg": "1.5rem"}

    def __init__(
        self,
        value: int = 0,
        size: str = "md",
        color: str = "primary",
        show_value: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            value=value,
            size=size,
            color=color,
            show_value=show_value,
            custom_class=custom_class,
            **kwargs,
        )
        self.value = max(0, min(100, value))
        self.size = size
        self.color = color
        self.show_value = show_value
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the circular progress SVG HTML."""
        classes = [
            "dj-progress-circle",
            f"dj-progress-circle--{self.size}",
            f"dj-progress-circle--{self.color}",
        ]

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        dim = self.SIZES.get(self.size, 80)
        stroke_w = self.STROKE_WIDTHS.get(self.size, 6)
        radius = (dim - stroke_w) / 2
        circumference = 2 * 3.14159265 * radius
        dash_offset = circumference * (1 - self.value / 100)

        value_html = ""
        if self.show_value:
            fs = self.FONT_SIZES.get(self.size, "1rem")
            value_html = (
                f'<text x="{dim / 2}" y="{dim / 2}" '
                f'class="dj-progress-circle__value" '
                f'text-anchor="middle" dominant-baseline="central" '
                f'style="font-size:{fs}">'
                f"{self.value}%</text>"
            )

        return (
            f'<div class="{class_str}" role="progressbar" '
            f'aria-valuenow="{self.value}" aria-valuemin="0" aria-valuemax="100">'
            f'<svg width="{dim}" height="{dim}" viewBox="0 0 {dim} {dim}">'
            f'<circle class="dj-progress-circle__track" '
            f'cx="{dim / 2}" cy="{dim / 2}" r="{radius}" '
            f'fill="none" stroke-width="{stroke_w}"/>'
            f'<circle class="dj-progress-circle__fill" '
            f'cx="{dim / 2}" cy="{dim / 2}" r="{radius}" '
            f'fill="none" stroke-width="{stroke_w}" '
            f'stroke-dasharray="{circumference:.2f}" '
            f'stroke-dashoffset="{dash_offset:.2f}" '
            f'stroke-linecap="round" '
            f'transform="rotate(-90 {dim / 2} {dim / 2})"/>'
            f"{value_html}"
            f"</svg></div>"
        )
