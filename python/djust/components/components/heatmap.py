"""Heatmap component — color-coded grid visualization."""

import html
from typing import Optional

from djust import Component

from djust.components.utils import interpolate_color


class Heatmap(Component):
    """Style-agnostic SVG heatmap using CSS custom properties.

    Usage in a LiveView::

        self.map = Heatmap(
            data=[[1, 2, 3], [4, 5, 6], [7, 8, 9]],
            x_labels=["Mon", "Tue", "Wed"],
            y_labels=["Morning", "Afternoon", "Evening"],
        )

    In template::

        {{ map|safe }}

    Args:
        data: 2D list (rows of columns) of numeric values
        x_labels: Column header labels
        y_labels: Row header labels
        title: Optional chart title
        color_min: Color for minimum value (default: "#f0f9ff")
        color_max: Color for maximum value (default: "#1e40af")
        cell_size: Cell width/height in px (default: 36)
        show_values: Show numeric values in cells (default: True)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        data: list = None,
        x_labels: list = None,
        y_labels: list = None,
        title: Optional[str] = None,
        color_min: str = "#f0f9ff",
        color_max: str = "#1e40af",
        cell_size: int = 36,
        show_values: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            data=data,
            x_labels=x_labels,
            y_labels=y_labels,
            title=title,
            color_min=color_min,
            color_max=color_max,
            cell_size=cell_size,
            show_values=show_values,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data or []
        self.x_labels = x_labels or []
        self.y_labels = y_labels or []
        self.title = title
        self.color_min = color_min
        self.color_max = color_max
        self.cell_size = cell_size
        self.show_values = show_values
        self.custom_class = custom_class

    _interpolate_color = staticmethod(interpolate_color)

    def _render_custom(self) -> str:
        classes = ["dj-heatmap"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.data:
            return f'<div class="{class_str}"><svg></svg></div>'

        cs = self.cell_size
        rows = len(self.data)
        cols = max((len(row) for row in self.data if isinstance(row, list)), default=0)
        if cols == 0:
            return f'<div class="{class_str}"><svg></svg></div>'

        label_left = 60 if self.y_labels else 0
        label_top = 20 if self.x_labels else 0
        title_h = 24 if self.title else 0
        w = label_left + cols * cs + 4
        h = title_h + label_top + rows * cs + 4

        # Find min/max
        all_vals = []
        for row in self.data:
            if not isinstance(row, list):
                continue
            for v in row:
                try:
                    all_vals.append(float(v))
                except (ValueError, TypeError):
                    pass
        min_val = min(all_vals) if all_vals else 0
        max_val = max(all_vals) if all_vals else 1
        val_range = max_val - min_val if max_val != min_val else 1

        parts = [
            f'<svg class="dj-heatmap__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{html.escape(self.title or "Heatmap")}">'
        ]

        if self.title:
            parts.append(
                f'<text class="dj-heatmap__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{html.escape(self.title)}</text>"
            )

        # X labels
        for ci, lbl in enumerate(self.x_labels[:cols]):
            x = label_left + ci * cs + cs / 2
            y = title_h + label_top - 4
            parts.append(
                f'<text class="dj-heatmap__xlabel" x="{x:.1f}" y="{y:.1f}" '
                f'text-anchor="middle" font-size="10">'
                f"{html.escape(str(lbl))}</text>"
            )

        # Y labels
        for ri, lbl in enumerate(self.y_labels[:rows]):
            x = label_left - 4
            y = title_h + label_top + ri * cs + cs / 2
            parts.append(
                f'<text class="dj-heatmap__ylabel" x="{x:.1f}" y="{y:.1f}" '
                f'text-anchor="end" dominant-baseline="central" font-size="10">'
                f"{html.escape(str(lbl))}</text>"
            )

        # Cells
        for ri, row in enumerate(self.data):
            if not isinstance(row, list):
                continue
            for ci, v in enumerate(row):
                try:
                    val = float(v)
                except (ValueError, TypeError):
                    val = 0
                t = (val - min_val) / val_range
                color = self._interpolate_color(self.color_min, self.color_max, t)
                x = label_left + ci * cs
                y_pos = title_h + label_top + ri * cs

                parts.append(
                    f'<rect class="dj-heatmap__cell" x="{x}" y="{y_pos}" '
                    f'width="{cs}" height="{cs}" fill="{color}" stroke="#fff" stroke-width="1">'
                    f"<title>{val:g}</title></rect>"
                )

                if self.show_values:
                    text_color = "#fff" if t > 0.5 else "#1e293b"
                    parts.append(
                        f'<text class="dj-heatmap__value" x="{x + cs / 2:.1f}" '
                        f'y="{y_pos + cs / 2:.1f}" text-anchor="middle" '
                        f'dominant-baseline="central" font-size="10" fill="{text_color}">'
                        f"{val:g}</text>"
                    )

        parts.append("</svg>")
        return f'<div class="{class_str}">{"".join(parts)}</div>'
