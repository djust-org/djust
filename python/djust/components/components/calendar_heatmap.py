"""Calendar Heatmap component — GitHub-style contribution heatmap."""

import html
from datetime import date, timedelta
from typing import Optional

from djust import Component


class CalendarHeatmap(Component):
    """Style-agnostic SVG calendar heatmap (GitHub contribution style).

    Usage in a LiveView::

        self.contributions = CalendarHeatmap(
            data={"2026-01-01": 3, "2026-01-02": 7, "2026-01-03": 1},
            year=2026,
        )

    In template::

        {{ contributions|safe }}

    Args:
        data: Dict mapping "YYYY-MM-DD" strings to numeric values
        year: Year to display (default: current year)
        title: Optional chart title
        color_empty: Color for zero-value cells (default: "#ebedf0")
        color_min: Color for low values (default: "#9be9a8")
        color_max: Color for high values (default: "#216e39")
        cell_size: Cell width/height in px (default: 12)
        cell_gap: Gap between cells in px (default: 2)
        show_month_labels: Show month labels (default: True)
        show_day_labels: Show day-of-week labels (default: True)
        custom_class: Additional CSS classes
    """

    LEVELS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]

    def __init__(
        self,
        data: dict = None,
        year: int = None,
        title: Optional[str] = None,
        color_empty: str = "#ebedf0",
        color_min: str = "#9be9a8",
        color_max: str = "#216e39",
        cell_size: int = 12,
        cell_gap: int = 2,
        show_month_labels: bool = True,
        show_day_labels: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        if year is None:
            year = date.today().year
        super().__init__(
            data=data,
            year=year,
            title=title,
            color_empty=color_empty,
            color_min=color_min,
            color_max=color_max,
            cell_size=cell_size,
            cell_gap=cell_gap,
            show_month_labels=show_month_labels,
            show_day_labels=show_day_labels,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data or {}
        self.year = year
        self.title = title
        self.color_empty = color_empty
        self.color_min = color_min
        self.color_max = color_max
        self.cell_size = cell_size
        self.cell_gap = cell_gap
        self.show_month_labels = show_month_labels
        self.show_day_labels = show_day_labels
        self.custom_class = custom_class

    def _get_color(self, value: float, max_val: float) -> str:
        """Map a value to a color from the 5-level palette."""
        if value <= 0:
            return self.color_empty
        if max_val <= 0:
            return self.color_empty
        ratio = value / max_val
        if ratio <= 0.25:
            return self.LEVELS[1]
        elif ratio <= 0.5:
            return self.LEVELS[2]
        elif ratio <= 0.75:
            return self.LEVELS[3]
        else:
            return self.LEVELS[4]

    def _render_custom(self) -> str:
        classes = ["dj-calendar-heatmap"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        cs = self.cell_size
        cg = self.cell_gap
        step = cs + cg

        # Build year grid
        start = date(self.year, 1, 1)
        end = date(self.year, 12, 31)

        # Find max value
        vals = []
        for v in self.data.values():
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                # Skip non-numeric values; heatmap scale ignores them.
                continue
        max_val = max(vals) if vals else 1

        label_left = 30 if self.show_day_labels else 0
        label_top = 16 if self.show_month_labels else 0
        title_h = 22 if self.title else 0

        # Calculate weeks
        first_dow = start.weekday()  # Monday=0
        num_days = (end - start).days + 1
        num_weeks = ((first_dow + num_days - 1) // 7) + 1

        w = label_left + num_weeks * step + 4
        h = title_h + label_top + 7 * step + 4

        parts = [
            f'<svg class="dj-calendar-heatmap__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{html.escape(self.title or f"{self.year} activity")}">'
        ]

        if self.title:
            parts.append(
                f'<text class="dj-calendar-heatmap__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{html.escape(self.title)}</text>"
            )

        # Day labels (Mon, Wed, Fri)
        if self.show_day_labels:
            day_names = ["Mon", "", "Wed", "", "Fri", "", ""]
            for di, name in enumerate(day_names):
                if name:
                    y = title_h + label_top + di * step + cs / 2
                    parts.append(
                        f'<text class="dj-calendar-heatmap__day-label" x="{label_left - 4}" '
                        f'y="{y:.1f}" text-anchor="end" dominant-baseline="central" '
                        f'font-size="9">{name}</text>'
                    )

        # Month labels
        month_names = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        month_positions = {}

        # Render cells
        current = start
        while current <= end:
            day_of_year = (current - start).days
            dow = current.weekday()  # Monday=0
            week = (first_dow + day_of_year) // 7

            x = label_left + week * step
            y = title_h + label_top + dow * step

            date_str = current.isoformat()
            try:
                val = float(self.data.get(date_str, 0))
            except (ValueError, TypeError):
                val = 0
            color = self._get_color(val, max_val)

            parts.append(
                f'<rect class="dj-calendar-heatmap__cell" x="{x}" y="{y}" '
                f'width="{cs}" height="{cs}" rx="2" fill="{color}">'
                f"<title>{date_str}: {val:g}</title></rect>"
            )

            # Track month starts for labels
            if current.day == 1:
                month_positions[current.month] = x

            current += timedelta(days=1)

        # Month labels
        if self.show_month_labels:
            for month, mx in month_positions.items():
                parts.append(
                    f'<text class="dj-calendar-heatmap__month-label" '
                    f'x="{mx}" y="{title_h + label_top - 4}" '
                    f'font-size="9">{month_names[month - 1]}</text>'
                )

        parts.append("</svg>")
        return f'<div class="{class_str}">{"".join(parts)}</div>'
