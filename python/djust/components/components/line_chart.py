"""Line Chart component — SVG line/area chart with multiple series."""

import html
from typing import Optional

from djust import Component


class LineChart(Component):
    """Style-agnostic SVG line chart using CSS custom properties.

    Usage in a LiveView::

        self.chart = LineChart(
            series=[
                {"name": "Revenue", "data": [10, 30, 25, 45, 35]},
                {"name": "Costs", "data": [5, 15, 20, 25, 30]},
            ],
            labels=["Jan", "Feb", "Mar", "Apr", "May"],
        )

    In template::

        {{ chart|safe }}

    Args:
        series: List of series dicts with name, data, and optional color
        labels: List of x-axis labels
        title: Optional chart title
        width: SVG width (default: 400)
        height: SVG height (default: 250)
        area: Fill area under lines (default: False)
        show_dots: Show data point dots (default: True)
        show_legend: Show series legend (default: True)
        custom_class: Additional CSS classes
    """

    COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6", "#06b6d4"]

    def __init__(
        self,
        series: list = None,
        labels: list = None,
        title: Optional[str] = None,
        width: int = 400,
        height: int = 250,
        area: bool = False,
        show_dots: bool = True,
        show_legend: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            series=series,
            labels=labels,
            title=title,
            width=width,
            height=height,
            area=area,
            show_dots=show_dots,
            show_legend=show_legend,
            custom_class=custom_class,
            **kwargs,
        )
        self.series = series or []
        self.labels = labels or []
        self.title = title
        self.width = width
        self.height = height
        self.area = area
        self.show_dots = show_dots
        self.show_legend = show_legend
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-line-chart"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.series:
            return f'<div class="{class_str}"><svg></svg></div>'

        w, h = self.width, self.height
        pad_top = 30 if self.title else 10
        pad_bottom = 40 if self.show_legend else 30
        pad_left = 40
        pad_right = 10
        chart_w = w - pad_left - pad_right
        chart_h = h - pad_top - pad_bottom

        # Determine global min/max
        all_vals = []
        for s in self.series:
            if isinstance(s, dict):
                for v in s.get("data", []):
                    try:
                        all_vals.append(float(v))
                    except (ValueError, TypeError):
                        # Skip non-numeric values; chart scales ignore them.
                        continue
        max_val = max(all_vals) if all_vals else 1
        min_val = min(all_vals) if all_vals else 0
        val_range = max_val - min_val if max_val != min_val else 1

        parts = [
            f'<svg class="dj-line-chart__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{html.escape(self.title or "Line chart")}">'
        ]

        if self.title:
            parts.append(
                f'<text class="dj-line-chart__title" x="{w / 2}" y="18" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{html.escape(self.title)}</text>"
            )

        for si, s in enumerate(self.series):
            if not isinstance(s, dict):
                continue
            data = s.get("data", [])
            color = html.escape(str(s.get("color", self.COLORS[si % len(self.COLORS)])))
            name = html.escape(str(s.get("name", f"Series {si + 1}")))

            if not data:
                continue

            n = len(data)
            points = []
            for i, v in enumerate(data):
                try:
                    v = float(v)
                except (ValueError, TypeError):
                    v = 0
                x = pad_left + (i / max(n - 1, 1)) * chart_w
                y = pad_top + chart_h - ((v - min_val) / val_range) * chart_h
                points.append((x, y, v))

            path = " ".join(
                f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _) in enumerate(points)
            )

            if self.area and points:
                area_path = (
                    path
                    + f" L{points[-1][0]:.1f},{pad_top + chart_h:.1f}"
                    + f" L{points[0][0]:.1f},{pad_top + chart_h:.1f} Z"
                )
                parts.append(
                    f'<path class="dj-line-chart__area" d="{area_path}" '
                    f'fill="{color}" opacity="0.15"/>'
                )

            parts.append(
                f'<path class="dj-line-chart__line" d="{path}" '
                f'fill="none" stroke="{color}" stroke-width="2"/>'
            )

            if self.show_dots:
                for x, y, v in points:
                    parts.append(
                        f'<circle class="dj-line-chart__dot" cx="{x:.1f}" cy="{y:.1f}" '
                        f'r="3" fill="{color}">'
                        f"<title>{name}: {v:g}</title></circle>"
                    )

        # X-axis labels
        if self.labels:
            n = len(self.labels)
            for i, lbl in enumerate(self.labels):
                x = pad_left + (i / max(n - 1, 1)) * chart_w
                parts.append(
                    f'<text class="dj-line-chart__label" x="{x:.1f}" '
                    f'y="{pad_top + chart_h + 16:.1f}" text-anchor="middle" font-size="10">'
                    f"{html.escape(str(lbl))}</text>"
                )

        # Legend
        if self.show_legend and self.series:
            lx = pad_left
            ly = h - 8
            for si, s in enumerate(self.series):
                if not isinstance(s, dict):
                    continue
                color = html.escape(str(s.get("color", self.COLORS[si % len(self.COLORS)])))
                name = html.escape(str(s.get("name", f"Series {si + 1}")))
                parts.append(
                    f'<rect x="{lx}" y="{ly - 6}" width="10" height="10" rx="2" fill="{color}"/>'
                )
                parts.append(f'<text x="{lx + 14}" y="{ly + 3}" font-size="10">{name}</text>')
                lx += len(name) * 7 + 24

        parts.append("</svg>")
        return f'<div class="{class_str}">{"".join(parts)}</div>'
