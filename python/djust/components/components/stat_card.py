"""StatCard component for KPI/metric display."""

import html
from typing import Optional

from djust import Component


class StatCard(Component):
    """Style-agnostic stat card component using CSS custom properties.

    Displays a key metric with label, value, and optional trend indicator.

    Usage in a LiveView::

        self.revenue = StatCard(
            label="Revenue",
            value="$12,345",
            trend="up",
            trend_value="+12%",
            icon="💰",
        )

        self.users = StatCard(
            label="Active Users",
            value="1,234",
            trend="down",
            trend_value="-3%",
        )

        self.uptime = StatCard(
            label="Uptime",
            value="99.9%",
            trend="flat",
        )

    In template::

        {{ revenue|safe }}
        {{ users|safe }}

    CSS Custom Properties::

        --dj-stat-card-bg: background color
        --dj-stat-card-border: border color
        --dj-stat-card-radius: border radius
        --dj-stat-card-padding: internal padding
        --dj-stat-card-value-size: value font size (default: 1.5rem)
        --dj-stat-card-label-color: label text color
        --dj-stat-card-trend-up: trend up color (default: green)
        --dj-stat-card-trend-down: trend down color (default: red)

    Args:
        label: Metric label text
        value: Metric value (string for flexible formatting)
        trend: Trend direction (up, down, flat, or None)
        trend_value: Trend change text (e.g., "+12%", "-3%")
        icon: Optional icon text (emoji or HTML)
        variant: Style variant (default, bordered, elevated)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        label: str,
        value: str,
        trend: Optional[str] = None,
        trend_value: Optional[str] = None,
        icon: Optional[str] = None,
        variant: str = "default",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            label=label,
            value=value,
            trend=trend,
            trend_value=trend_value,
            icon=icon,
            variant=variant,
            custom_class=custom_class,
            **kwargs,
        )
        self.label = label
        self.value = value
        self.trend = trend
        self.trend_value = trend_value
        self.icon = icon
        self.variant = variant
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the stat card HTML."""
        classes = ["dj-stat-card"]

        if self.variant != "default":
            classes.append(f"dj-stat-card-{self.variant}")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        parts = []

        if self.icon:
            parts.append(f'<div class="dj-stat-card-icon">{self.icon}</div>')

        parts.append(f'<div class="dj-stat-card-label">{html.escape(self.label)}</div>')
        parts.append(f'<div class="dj-stat-card-value">{html.escape(self.value)}</div>')

        if self.trend:
            trend_classes = ["dj-stat-card-trend", f"dj-stat-card-trend-{self.trend}"]
            trend_class_str = " ".join(trend_classes)
            trend_text = html.escape(self.trend_value) if self.trend_value else ""
            parts.append(f'<div class="{trend_class_str}">{trend_text}</div>')

        content = "".join(parts)
        return f'<div class="{class_str}">{content}</div>'
