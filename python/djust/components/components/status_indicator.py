"""Status Indicator component for service health displays."""

import html
from typing import Optional

from djust import Component


class StatusIndicator(Component):
    """Style-agnostic status indicator with colored dot and optional label.

    Displays a colored dot with optional label text. Maps status names to colors:
    online=green, degraded=yellow, offline=red, maintenance=blue.

    Usage in a LiveView::

        self.api_status = StatusIndicator(status="online", label="API")
        self.db_status = StatusIndicator(status="degraded", label="Database", pulse=True)
        self.cache = StatusIndicator(status="offline", label="Cache")

    In template::

        {{ api_status|safe }}

    CSS Custom Properties::

        --dj-status-indicator-green: online color (default: #10b981)
        --dj-status-indicator-yellow: degraded color (default: #f59e0b)
        --dj-status-indicator-red: offline color (default: #ef4444)
        --dj-status-indicator-blue: maintenance color (default: #3b82f6)

    Args:
        status: Status string (online, degraded, offline, maintenance)
        label: Optional label text
        pulse: Whether to animate with pulse (default: False)
        size: Size variant (sm, md, lg)
        custom_class: Additional CSS classes
    """

    STATUS_COLORS = {
        "online": "green",
        "degraded": "yellow",
        "offline": "red",
        "maintenance": "blue",
    }

    def __init__(
        self,
        status: str = "offline",
        label: Optional[str] = None,
        pulse: bool = False,
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            status=status,
            label=label,
            pulse=pulse,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.status = status
        self.label = label
        self.pulse = pulse
        self.size = size
        self.custom_class = custom_class

    @property
    def color(self) -> str:
        """Map status to color name."""
        return self.STATUS_COLORS.get(self.status, "gray")

    def _render_custom(self) -> str:
        """Render the status indicator HTML."""
        classes = [
            "dj-status-indicator",
            f"dj-status-indicator--{self.size}",
            f"dj-status-indicator--{self.color}",
        ]

        if self.pulse:
            classes.append("dj-status-indicator--pulse")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        dot_html = '<span class="dj-status-indicator__dot"></span>'
        label_html = ""
        if self.label:
            label_html = (
                f'<span class="dj-status-indicator__label">{html.escape(self.label)}</span>'
            )

        return f'<span class="{class_str}">{dot_html}{label_html}</span>'
