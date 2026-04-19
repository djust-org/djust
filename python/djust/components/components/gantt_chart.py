"""Gantt Chart component — timeline bar chart for project management."""

import html
from typing import Optional

from djust import Component


class GanttChart(Component):
    """SVG Gantt chart for project management timelines.

    Usage in a LiveView::

        self.gantt = GanttChart(
            tasks=[
                {"name": "Design", "start": 0, "duration": 3, "color": "#3b82f6"},
                {"name": "Develop", "start": 2, "duration": 5, "color": "#22c55e"},
                {"name": "Test", "start": 6, "duration": 2, "color": "#f59e0b"},
            ],
        )

    In template::

        {{ gantt|safe }}

    Args:
        tasks: List of dicts with ``name``, ``start`` (unit offset), ``duration`` (units),
               optional ``color``, ``progress`` (0-1)
        title: Optional chart title
        unit_label: Label for time units (default: "Day")
        units: Number of time units to display (auto-calculated if omitted)
        row_height: Height per row in px (default: 32)
        width: SVG width (default: 600)
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
        tasks: Optional[list] = None,
        title: Optional[str] = None,
        unit_label: str = "Day",
        units: Optional[int] = None,
        row_height: int = 32,
        width: int = 600,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            tasks=tasks,
            title=title,
            unit_label=unit_label,
            units=units,
            row_height=row_height,
            width=width,
            custom_class=custom_class,
            **kwargs,
        )
        self.tasks = tasks or []
        self.title = title
        self.unit_label = unit_label
        self.units = units
        self.row_height = row_height
        self.width = width
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-gantt"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.tasks:
            return f'<div class="{class_str}"><svg></svg></div>'

        parsed = []
        for i, t in enumerate(self.tasks):
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", f"Task {i + 1}"))
            try:
                start = float(t.get("start", 0))
            except (ValueError, TypeError):
                start = 0
            try:
                dur = float(t.get("duration", 1))
            except (ValueError, TypeError):
                dur = 1
            color = str(t.get("color", self.COLORS[i % len(self.COLORS)]))
            try:
                progress = float(t.get("progress", 0))
            except (ValueError, TypeError):
                progress = 0
            progress = max(0, min(1, progress))
            parsed.append((name, start, dur, color, progress))

        if not parsed:
            return f'<div class="{class_str}"><svg></svg></div>'

        max_end = max(s + d for _, s, d, _, _ in parsed)
        total_units = self.units or int(max_end) + 1
        if total_units <= 0:
            total_units = 1

        label_width = 120
        title_h = 24 if self.title else 0
        header_h = 24
        rh = self.row_height
        w = self.width
        h = title_h + header_h + len(parsed) * rh + 4
        chart_w = w - label_width
        unit_w = chart_w / total_units

        parts = [
            f'<svg class="dj-gantt__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{html.escape(self.title or "Gantt chart")}">'
        ]

        if self.title:
            parts.append(
                f'<text class="dj-gantt__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{html.escape(self.title)}</text>"
            )

        for u in range(total_units):
            x = label_width + u * unit_w + unit_w / 2
            y = title_h + 16
            parts.append(
                f'<text class="dj-gantt__header" x="{x:.1f}" y="{y:.1f}" '
                f'text-anchor="middle" font-size="9" fill="#6b7280">{u + 1}</text>'
            )

        for u in range(total_units + 1):
            x = label_width + u * unit_w
            y1 = title_h + header_h
            y2 = h
            parts.append(
                f'<line x1="{x:.1f}" y1="{y1}" x2="{x:.1f}" y2="{y2}" '
                f'stroke="#e5e7eb" stroke-width="0.5"/>'
            )

        for idx, (name, start, dur, color, progress) in enumerate(parsed):
            y = title_h + header_h + idx * rh
            bar_x = label_width + start * unit_w
            bar_w = dur * unit_w
            bar_y = y + 6
            bar_h = rh - 12

            e_name = html.escape(name)
            e_color = html.escape(color)

            parts.append(
                f'<text class="dj-gantt__label" x="{label_width - 8}" '
                f'y="{y + rh / 2:.1f}" text-anchor="end" '
                f'dominant-baseline="central" font-size="11">{e_name}</text>'
            )

            parts.append(
                f'<rect class="dj-gantt__bar" x="{bar_x:.1f}" y="{bar_y:.1f}" '
                f'width="{bar_w:.1f}" height="{bar_h:.1f}" rx="3" '
                f'fill="{e_color}" opacity="0.25">'
                f"<title>{e_name}: {html.escape(self.unit_label)} {start:.0f}-{start + dur:.0f}</title></rect>"
            )

            if progress > 0:
                pw = bar_w * progress
                parts.append(
                    f'<rect class="dj-gantt__progress" x="{bar_x:.1f}" y="{bar_y:.1f}" '
                    f'width="{pw:.1f}" height="{bar_h:.1f}" rx="3" '
                    f'fill="{e_color}"/>'
                )

        parts.append("</svg>")
        return f'<div class="{class_str}">{"".join(parts)}</div>'
