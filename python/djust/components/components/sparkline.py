"""Sparkline component — lightweight inline SVG chart."""

import html

from djust import Component


class Sparkline(Component):
    """Style-agnostic inline sparkline using SVG.

    Usage in a LiveView::

        self.trend = Sparkline(data=[3, 7, 4, 8, 2, 6])
        self.bars = Sparkline(data=[3, 7, 4, 8, 2, 6], variant="bar")

    In template::

        {{ trend|safe }}

    Args:
        data: List of numeric values
        variant: Chart type — "line" (default), "bar", "area"
        width: SVG width (default: 100)
        height: SVG height (default: 24)
        color: Stroke/fill color
        stroke_width: Line stroke width (default: 1.5)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        data: list = None,
        variant: str = "line",
        width: int = 100,
        height: int = 24,
        color: str = "",
        stroke_width: float = 1.5,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            data=data,
            variant=variant,
            width=width,
            height=height,
            color=color,
            stroke_width=stroke_width,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data or []
        self.variant = variant
        self.width = width
        self.height = height
        self.color = color
        self.stroke_width = stroke_width
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-sparkline"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.data:
            return f'<span class="{class_str}"><svg></svg></span>'

        w, h = self.width, self.height
        pad = 2
        chart_w = w - pad * 2
        chart_h = h - pad * 2

        vals = []
        for v in self.data:
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                vals.append(0)

        max_val = max(vals) if vals else 1
        min_val = min(vals) if vals else 0
        val_range = max_val - min_val if max_val != min_val else 1

        color_attr = html.escape(self.color) if self.color else ""

        parts = [
            f'<svg class="dj-sparkline__svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="Sparkline">'
        ]

        if self.variant == "bar":
            n = len(vals)
            bar_gap = 1
            bar_w = max(1, (chart_w - (n - 1) * bar_gap) / n)
            for i, v in enumerate(vals):
                bar_h = max(1, ((v - min_val) / val_range) * chart_h)
                x = pad + i * (bar_w + bar_gap)
                y = pad + chart_h - bar_h
                fill = f' fill="{color_attr}"' if color_attr else ""
                parts.append(
                    f'<rect class="dj-sparkline__bar" x="{x:.1f}" y="{y:.1f}" '
                    f'width="{bar_w:.1f}" height="{bar_h:.1f}"{fill}/>'
                )
        else:
            # Line or area
            n = len(vals)
            points = []
            for i, v in enumerate(vals):
                x = pad + (i / max(n - 1, 1)) * chart_w
                y = pad + chart_h - ((v - min_val) / val_range) * chart_h
                points.append((x, y))

            path = " ".join(
                f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points)
            )

            if self.variant == "area" and points:
                area_path = (
                    path
                    + f" L{points[-1][0]:.1f},{pad + chart_h:.1f}"
                    + f" L{points[0][0]:.1f},{pad + chart_h:.1f} Z"
                )
                fill = f' fill="{color_attr}"' if color_attr else ""
                parts.append(
                    f'<path class="dj-sparkline__area" d="{area_path}"{fill} opacity="0.2"/>'
                )

            stroke = f' stroke="{color_attr}"' if color_attr else ""
            parts.append(
                f'<path class="dj-sparkline__line" d="{path}" '
                f'fill="none"{stroke} stroke-width="{self.stroke_width}"/>'
            )

        parts.append("</svg>")
        return f'<span class="{class_str}">{"".join(parts)}</span>'
