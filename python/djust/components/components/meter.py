"""Meter / stacked progress component for multi-segment horizontal bars."""

import html
from django.utils.html import conditional_escape
from djust.components.utils import attr_text

from djust import Component
from typing import Any, Optional
from djust.components.utils import safe_css_color
from djust.components.utils import style_attrs


class Meter(Component):
    """Style-agnostic meter / stacked progress component.

    Multiple colored segments in a horizontal bar.

    Usage in a LiveView::

        self.usage = Meter(
            segments=[
                {"value": 40, "color": "hsl(var(--primary))", "label": "Used"},
                {"value": 20, "color": "hsl(var(--warning))", "label": "Reserved"},
            ],
            total=100,
        )

    In template::

        {{ usage|safe }}

    Args:
        segments: List of segment dicts with value, color, label
        total: Total value (default: 100)
        label: Overall meter label
        show_legend: Show color legend below bar (default: True)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        segments: Optional[list] = None,
        total: int = 100,
        label: str = "",
        show_legend: bool = True,
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            segments=segments,
            total=total,
            label=label,
            show_legend=show_legend,
            custom_class=custom_class,
            **kwargs,
        )
        self.segments = segments or []
        self.total = total
        self.label = label
        self.show_legend = show_legend
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-meter"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        label_html = ""
        if self.label:
            label_html = f'<div class="dj-meter__label">{html.escape(self.label)}</div>'

        # Render bar segments
        bar_parts = []
        for seg in self.segments:
            val = seg.get("value", 0)
            if self.total > 0:
                pct = min(100, max(0, (val / self.total) * 100))
            else:
                pct = 0
            color = html.escape(safe_css_color(seg.get("color", ""), ""))
            seg_label = html.escape(str(seg.get("label", "")))
            # The width is a class, as `{% progress %}` draws it (#3969, #3979). A
            # segment's own colour has no class to stand for it, so it is the one
            # thing still written as a style, and only when one is given.
            style_attr = style_attrs(background_color=color, css="background:{}")
            bar_parts.append(
                f'<div class="dj-meter__segment dj-progress-w-{round(pct)}"{style_attr} '
                f'role="meter" aria-valuenow="{attr_text(val)}" '
                f'aria-valuemin="0" aria-valuemax="{attr_text(self.total)}" '
                f'aria-label="{seg_label}"></div>'
            )

        bar = f'<div class="dj-meter__bar">{"".join(bar_parts)}</div>'

        legend_html = ""
        if self.show_legend and self.segments:
            legend_items = []
            for seg in self.segments:
                color = html.escape(safe_css_color(seg.get("color", ""), ""))
                seg_label = html.escape(str(seg.get("label", "")))
                val = seg.get("value", 0)
                # No `style` attribute at all without a colour (#3979).
                swatch_style = style_attrs(background_color=color, css="background:{}")
                legend_items.append(
                    f'<div class="dj-meter__legend-item">'
                    f'<span class="dj-meter__legend-swatch"{swatch_style}></span>'
                    f'<span class="dj-meter__legend-label">{seg_label}</span>'
                    f'<span class="dj-meter__legend-value">{conditional_escape(val)}</span>'
                    f"</div>"
                )
            legend_html = f'<div class="dj-meter__legend">{"".join(legend_items)}</div>'

        return f'<div class="{class_str}">{label_html}{bar}{legend_html}</div>'
