"""Gauge component."""

import html
from djust import Component


class Gauge(Component):
    """SVG donut/gauge chart component.

    Args:
        value: current value
        max_value: maximum value
        label: text label below gauge
        color: color variant (primary, success, warning, danger)
        size: sm, md, lg
        show_value: whether to show percentage text"""

    def __init__(
        self,
        value: float = 0,
        max_value: float = 100,
        label: str = "",
        color: str = "primary",
        size: str = "md",
        show_value: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            value=value,
            max_value=max_value,
            label=label,
            color=color,
            size=size,
            show_value=show_value,
            custom_class=custom_class,
            **kwargs,
        )
        self.value = value
        self.max_value = max_value
        self.label = label
        self.color = color
        self.size = size
        self.show_value = show_value
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the gauge HTML."""
        pct = min(max(self.value / (self.max_value or 100), 0), 1)
        sizes = {"sm": 64, "md": 96, "lg": 128}
        px = sizes.get(self.size, 96)
        r = (px - 12) / 2
        circ = 2 * 3.14159 * r
        dash = pct * circ
        gap = circ - dash
        cx = cy = px / 2
        e_color = html.escape(self.color)
        cls = f"gauge gauge-{e_color}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        val_html = (
            f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" '
            f'class="gauge-value-text">{int(pct * 100)}%</text>'
            if self.show_value
            else ""
        )
        e_label = html.escape(self.label)
        label_html = f'<div class="gauge-label">{e_label}</div>' if self.label else ""
        return (
            f'<div class="{cls}" style="width:{px}px;height:{px}px;">'
            f'<svg width="{px}" height="{px}" viewBox="0 0 {px} {px}">'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" class="gauge-track" '
            f'stroke-width="8" fill="none"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" class="gauge-fill gauge-fill-{e_color}" '
            f'stroke-width="8" fill="none" '
            f'stroke-dasharray="{dash:.1f} {gap:.1f}" '
            f'stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>'
            f"{val_html}"
            f"</svg>"
            f"{label_html}"
            f"</div>"
        )
