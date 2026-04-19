"""Bar Chart component — pure SVG bar chart with hover tooltips."""

import html
from typing import Optional

from djust import Component


class BarChart(Component):
    """Style-agnostic SVG bar chart using CSS custom properties.

    Usage in a LiveView::

        self.chart = BarChart(
            data=[45, 80, 55, 90, 30],
            labels=["Mon", "Tue", "Wed", "Thu", "Fri"],
            title="Weekly Sales",
        )

    In template::

        {{ chart|safe }}

    CSS Custom Properties::

        --dj-bar-chart-bar-fill: bar fill color
        --dj-bar-chart-bar-hover: bar hover fill color
        --dj-bar-chart-text: text color
        --dj-bar-chart-grid: grid line color
        --dj-bar-chart-bg: background color

    Args:
        data: List of numeric values
        labels: List of category labels
        title: Optional chart title
        width: SVG width (default: 400)
        height: SVG height (default: 250)
        color: Bar fill color
        show_values: Show value labels above bars
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        data: list = None,
        labels: list = None,
        title: Optional[str] = None,
        width: int = 400,
        height: int = 250,
        color: str = "",
        show_values: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            data=data,
            labels=labels,
            title=title,
            width=width,
            height=height,
            color=color,
            show_values=show_values,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data or []
        self.labels = labels or []
        self.title = title
        self.width = width
        self.height = height
        self.color = color
        self.show_values = show_values
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-bar-chart"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.data:
            return f'<div class="{class_str}"><svg></svg></div>'

        w, h = self.width, self.height
        pad_top = 30 if self.title else 10
        pad_bottom = 30
        pad_left = 40
        pad_right = 10
        chart_w = w - pad_left - pad_right
        chart_h = h - pad_top - pad_bottom

        max_val = max(self.data) if self.data else 1
        if max_val <= 0:
            max_val = 1

        n = len(self.data)
        bar_gap = 4
        bar_w = max(1, (chart_w - (n - 1) * bar_gap) / n)

        parts = [
            f'<svg class="dj-bar-chart__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{html.escape(self.title or "Bar chart")}">'
        ]

        if self.title:
            parts.append(
                f'<text class="dj-bar-chart__title" x="{w / 2}" y="18" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{html.escape(self.title)}</text>"
            )

        color_attr = f' fill="{html.escape(self.color)}"' if self.color else ""

        for i, val in enumerate(self.data):
            try:
                val = float(val)
            except (ValueError, TypeError):
                val = 0
            bar_h = (val / max_val) * chart_h if max_val > 0 else 0
            x = pad_left + i * (bar_w + bar_gap)
            y = pad_top + chart_h - bar_h

            parts.append(
                f'<rect class="dj-bar-chart__bar" x="{x:.1f}" y="{y:.1f}" '
                f'width="{bar_w:.1f}" height="{bar_h:.1f}"{color_attr}>'
                f"<title>{html.escape(str(self.labels[i]) if i < len(self.labels) else '')}: "
                f"{val}</title></rect>"
            )

            if self.show_values:
                parts.append(
                    f'<text class="dj-bar-chart__value" x="{x + bar_w / 2:.1f}" '
                    f'y="{y - 4:.1f}" text-anchor="middle" font-size="10">'
                    f"{val:g}</text>"
                )

            if i < len(self.labels):
                parts.append(
                    f'<text class="dj-bar-chart__label" x="{x + bar_w / 2:.1f}" '
                    f'y="{pad_top + chart_h + 16:.1f}" text-anchor="middle" font-size="10">'
                    f"{html.escape(str(self.labels[i]))}</text>"
                )

        parts.append("</svg>")
        return f'<div class="{class_str}">{"".join(parts)}</div>'
