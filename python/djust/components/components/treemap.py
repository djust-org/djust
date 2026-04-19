"""Treemap component — nested rectangles for hierarchical data."""

import html
from typing import Optional

from djust import Component


class Treemap(Component):
    """Style-agnostic SVG treemap using CSS custom properties.

    Usage in a LiveView::

        self.map = Treemap(
            data=[
                {"name": "JS", "size": 45},
                {"name": "Python", "size": 30},
                {"name": "Rust", "size": 15},
                {"name": "Go", "size": 10},
            ],
        )

    In template::

        {{ map|safe }}

    Args:
        data: List of dicts with label_key and value_key fields
        value_key: Key for numeric value (default: "size")
        label_key: Key for label text (default: "name")
        title: Optional chart title
        width: SVG width (default: 400)
        height: SVG height (default: 250)
        colors: List of fill colors (cycles)
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
        "#14b8a6",
        "#a855f7",
    ]

    def __init__(
        self,
        data: list = None,
        value_key: str = "size",
        label_key: str = "name",
        title: Optional[str] = None,
        width: int = 400,
        height: int = 250,
        colors: list = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            data=data,
            value_key=value_key,
            label_key=label_key,
            title=title,
            width=width,
            height=height,
            colors=colors,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data or []
        self.value_key = value_key
        self.label_key = label_key
        self.title = title
        self.width = width
        self.height = height
        self.colors = colors or self.COLORS
        self.custom_class = custom_class

    @staticmethod
    def _squarify(items, x, y, w, h):
        """Simple slice-and-dice treemap layout."""
        rects = []
        if not items or w <= 0 or h <= 0:
            return rects

        total = sum(v for _, v, _ in items)
        if total <= 0:
            return rects

        if w >= h:
            # Lay out horizontally
            cx = x
            for label, val, idx in items:
                frac = val / total
                rw = w * frac
                rects.append((cx, y, rw, h, label, val, idx))
                cx += rw
        else:
            # Lay out vertically
            cy = y
            for label, val, idx in items:
                frac = val / total
                rh = h * frac
                rects.append((x, cy, w, rh, label, val, idx))
                cy += rh

        return rects

    def _render_custom(self) -> str:
        classes = ["dj-treemap"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.data:
            return f'<div class="{class_str}"><svg></svg></div>'

        w, h = self.width, self.height
        title_h = 24 if self.title else 0
        chart_h = h - title_h

        items = []
        for i, d in enumerate(self.data):
            if not isinstance(d, dict):
                continue
            try:
                val = float(d.get(self.value_key, 0))
            except (ValueError, TypeError):
                val = 0
            if val > 0:
                label = str(d.get(self.label_key, ""))
                items.append((label, val, i))

        # Sort descending for better layout
        items.sort(key=lambda x: x[1], reverse=True)

        rects = self._squarify(items, 0, title_h, w, chart_h)

        parts = [
            f'<svg class="dj-treemap__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="{html.escape(self.title or "Treemap")}">'
        ]

        if self.title:
            parts.append(
                f'<text class="dj-treemap__title" x="{w / 2}" y="16" '
                f'text-anchor="middle" font-size="13" font-weight="600">'
                f"{html.escape(self.title)}</text>"
            )

        for rx, ry, rw, rh, label, val, idx in rects:
            color = html.escape(str(self.colors[idx % len(self.colors)]))
            e_label = html.escape(label)
            parts.append(
                f'<rect class="dj-treemap__cell" x="{rx:.1f}" y="{ry:.1f}" '
                f'width="{rw:.1f}" height="{rh:.1f}" fill="{color}" '
                f'stroke="#fff" stroke-width="2">'
                f"<title>{e_label}: {val:g}</title></rect>"
            )
            # Label if cell is large enough
            if rw > 30 and rh > 20:
                parts.append(
                    f'<text class="dj-treemap__label" '
                    f'x="{rx + rw / 2:.1f}" y="{ry + rh / 2:.1f}" '
                    f'text-anchor="middle" dominant-baseline="central" '
                    f'font-size="{min(11, rw / max(len(label), 1) * 1.2):.0f}" '
                    f'fill="#fff" font-weight="600">{e_label}</text>'
                )

        parts.append("</svg>")
        return f'<div class="{class_str}">{"".join(parts)}</div>'
