"""
StatCard Component

Displays a statistic with label, value, icon, and optional trend indicator.
Used for dashboards, KPIs, and metric displays.
"""

from djust.components.base import Component
from django.utils.safestring import mark_safe


class StatCard(Component):
    """
    A card component for displaying statistics/metrics.

    Features:
    - Lucide icon support
    - Optional trend indicator (up/down arrows with percentage)
    - Responsive design
    - Full dark/light mode support

    Example:
        stat = StatCard(
            label="Total Properties",
            value="24",
            icon="home",
            trend="+12%",
            trend_direction="up"
        )
        # In template: {{ stat.render }}
    """

    def __init__(
        self,
        label: str,
        value: str,
        icon: str = None,
        trend: str = None,
        trend_direction: str = None,  # "up" or "down"
        color: str = "primary"  # "primary", "destructive", "muted"
    ):
        self.label = label
        self.value = value
        self.icon = icon
        self.trend = trend
        self.trend_direction = trend_direction
        self.color = color

    def render(self) -> str:
        """Render the stat card component."""

        # Icon HTML
        icon_html = ""
        if self.icon:
            icon_html = f'''
            <div class="inline-flex items-center justify-center rounded-lg bg-{self.color}/10 p-3 mb-3">
                <i data-lucide="{self.icon}" class="w-6 h-6 text-{self.color}"></i>
            </div>
            '''

        # Trend indicator HTML
        trend_html = ""
        if self.trend:
            trend_color = "text-green-500" if self.trend_direction == "up" else "text-red-500"
            trend_icon = "trending-up" if self.trend_direction == "up" else "trending-down"
            trend_html = f'''
            <div class="flex items-center gap-1 mt-2 text-sm {trend_color}">
                <i data-lucide="{trend_icon}" class="w-4 h-4"></i>
                <span>{self.trend}</span>
            </div>
            '''

        return mark_safe(f'''
        <div class="bg-card border border-border rounded-lg p-6 transition-all hover:border-{self.color}/50 hover:shadow-lg">
            {icon_html}
            <p class="text-sm font-medium text-muted-foreground mb-1">{self.label}</p>
            <p class="text-3xl font-bold text-card-foreground">{self.value}</p>
            {trend_html}
        </div>
        ''')
