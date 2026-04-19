"""Pie / Donut Chart component — SVG pie/donut with labels and hover."""

import html
import math
from typing import Optional

from djust import Component


class PieChart(Component):
    """Style-agnostic SVG pie/donut chart using CSS custom properties.

    Usage in a LiveView::

        self.chart = PieChart(
            segments=[
                {"label": "Desktop", "value": 60, "color": "#3b82f6"},
                {"label": "Mobile", "value": 30, "color": "#22c55e"},
                {"label": "Tablet", "value": 10, "color": "#f59e0b"},
            ],
        )

    In template::

        {{ chart|safe }}

    Args:
        segments: List of segment dicts with label, value, and optional color
        title: Optional chart title
        width: SVG width (default: 300)
        height: SVG height (default: 300)
        donut: Render as donut chart (default: False)
        inner_radius: Inner radius ratio for donut (0-1, default: 0.6)
        show_labels: Show percentage labels (default: True)
        show_legend: Show legend below chart (default: True)
        custom_class: Additional CSS classes
    """

    COLORS = [
        "#3b82f6",
        "#ef4444",
        "#22c55e",
        "#f59e0b",
        "#8b5cf6",
        "#06b6d4",
        "#ec4899",
        "#f97316",
    ]

    def __init__(
        self,
        segments: list = None,
        title: Optional[str] = None,
        width: int = 300,
        height: int = 300,
        donut: bool = False,
        inner_radius: float = 0.6,
        show_labels: bool = True,
        show_legend: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            segments=segments,
            title=title,
            width=width,
            height=height,
            donut=donut,
            inner_radius=inner_radius,
            show_labels=show_labels,
            show_legend=show_legend,
            custom_class=custom_class,
            **kwargs,
        )
        self.segments = segments or []
        self.title = title
        self.width = width
        self.height = height
        self.donut = donut
        self.inner_radius = inner_radius
        self.show_labels = show_labels
        self.show_legend = show_legend
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-pie-chart"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.segments:
            return f'<div class="{class_str}"><svg></svg></div>'

        w, h = self.width, self.height
        title_offset = 24 if self.title else 0
        legend_offset = 24 if self.show_legend else 0
        cx = w / 2
        cy = title_offset + (h - title_offset - legend_offset) / 2
        r = min(cx, (h - title_offset - legend_offset) / 2) - 10
        ir = r * self.inner_radius if self.donut else 0

        total = sum(float(s.get("value", 0)) for s in self.segments if isinstance(s, dict))
        if total <= 0:
            return f'<div class="{class_str}"><svg></svg></div>'

        parts = [
            f'<svg class="dj-pie-chart__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{html.escape(self.title or "Pie chart")}">'
        ]

        if self.title:
            parts.append(
                f'<text class="dj-pie-chart__title" x="{cx}" y="18" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{html.escape(self.title)}</text>"
            )

        angle = -math.pi / 2  # start at 12 o'clock

        for si, seg in enumerate(self.segments):
            if not isinstance(seg, dict):
                continue
            val = float(seg.get("value", 0))
            if val <= 0:
                continue
            color = html.escape(str(seg.get("color", self.COLORS[si % len(self.COLORS)])))
            label = html.escape(str(seg.get("label", "")))
            pct = val / total

            sweep = pct * 2 * math.pi
            x1 = cx + r * math.cos(angle)
            y1 = cy + r * math.sin(angle)
            x2 = cx + r * math.cos(angle + sweep)
            y2 = cy + r * math.sin(angle + sweep)
            large = 1 if sweep > math.pi else 0

            if self.donut:
                ix1 = cx + ir * math.cos(angle)
                iy1 = cy + ir * math.sin(angle)
                ix2 = cx + ir * math.cos(angle + sweep)
                iy2 = cy + ir * math.sin(angle + sweep)
                d = (
                    f"M{x1:.2f},{y1:.2f} A{r},{r} 0 {large},1 {x2:.2f},{y2:.2f} "
                    f"L{ix2:.2f},{iy2:.2f} A{ir},{ir} 0 {large},0 {ix1:.2f},{iy1:.2f} Z"
                )
            else:
                d = f"M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large},1 {x2:.2f},{y2:.2f} Z"

            parts.append(
                f'<path class="dj-pie-chart__segment" d="{d}" fill="{color}">'
                f"<title>{label}: {val:g} ({pct * 100:.1f}%)</title></path>"
            )

            if self.show_labels and pct >= 0.05:
                mid_angle = angle + sweep / 2
                lr = r * 0.7 if not self.donut else (r + ir) / 2
                lx = cx + lr * math.cos(mid_angle)
                ly = cy + lr * math.sin(mid_angle)
                parts.append(
                    f'<text class="dj-pie-chart__pct" x="{lx:.1f}" y="{ly:.1f}" '
                    f'text-anchor="middle" dominant-baseline="central" '
                    f'font-size="10" fill="#fff" font-weight="600">'
                    f"{pct * 100:.0f}%</text>"
                )

            angle += sweep

        # Legend
        if self.show_legend:
            lx = 10
            ly = h - 8
            for si, seg in enumerate(self.segments):
                if not isinstance(seg, dict):
                    continue
                color = html.escape(str(seg.get("color", self.COLORS[si % len(self.COLORS)])))
                label = html.escape(str(seg.get("label", "")))
                parts.append(
                    f'<rect x="{lx}" y="{ly - 6}" width="10" height="10" rx="2" fill="{color}"/>'
                )
                parts.append(f'<text x="{lx + 14}" y="{ly + 3}" font-size="10">{label}</text>')
                lx += len(label) * 7 + 24

        parts.append("</svg>")
        return f'<div class="{class_str}">{"".join(parts)}</div>'
