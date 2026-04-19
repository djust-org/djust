"""Notification Badge component for count indicators on icons/buttons."""

import html

from djust import Component


class NotificationBadge(Component):
    """Style-agnostic notification badge using CSS custom properties.

    Displays a small count badge or dot indicator, typically on icons/buttons.

    Usage in a LiveView::

        # Count badge
        self.unread = NotificationBadge(count=5)

        # Max count with overflow
        self.overflow = NotificationBadge(count=150, max_count=99)  # shows "99+"

        # Dot-only mode (no count)
        self.alert = NotificationBadge(dot=True, pulse=True)

    In template::

        {{ unread|safe }}
        {{ alert|safe }}

    CSS Custom Properties::

        --dj-notification-badge-bg: background color (default: #ef4444)
        --dj-notification-badge-fg: text color (default: white)
        --dj-notification-badge-size: dot size (default: 0.5rem)
        --dj-notification-badge-font-size: text size (default: 0.625rem)

    Args:
        count: Number to display
        max_count: Maximum count before showing "N+" (default: 99)
        dot: Show as a dot with no text (default: False)
        pulse: Animate with pulse (default: False)
        size: Size variant (sm, md, lg)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        count: int = 0,
        max_count: int = 99,
        dot: bool = False,
        pulse: bool = False,
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            count=count,
            max_count=max_count,
            dot=dot,
            pulse=pulse,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.count = count
        self.max_count = max_count
        self.dot = dot
        self.pulse = pulse
        self.size = size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the notification badge HTML."""
        classes = ["dj-notification-badge", f"dj-notification-badge--{self.size}"]

        if self.pulse:
            classes.append("dj-notification-badge--pulse")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        if self.dot:
            return f'<span class="{class_str} dj-notification-badge--dot"></span>'

        if self.count <= 0:
            return ""

        display = f"{self.max_count}+" if self.count > self.max_count else str(self.count)
        return f'<span class="{class_str}">{display}</span>'
