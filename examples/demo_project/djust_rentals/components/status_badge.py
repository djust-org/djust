"""
StatusBadge Component

Displays status indicators with automatic color coding.
Used for property status, maintenance priority, lease status, etc.
"""

from djust.components.base import Component
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from typing import Optional


class StatusBadge(Component):
    """
    A badge component for displaying status indicators.

    Features:
    - Automatic color coding based on status
    - Optional icon support
    - Consistent styling
    - Full dark/light mode support

    Example:
        badge = StatusBadge(status="active")  # Auto-colors: green
        badge = StatusBadge(status="urgent", variant="priority")  # Auto-colors: red
        badge = StatusBadge(status="custom", color="blue", label="Custom Label")

        # In template: {{ badge.render }}
    """

    # Predefined status color mappings
    STATUS_COLORS = {
        # Property statuses
        'available': 'green',
        'occupied': 'blue',
        'maintenance': 'yellow',
        'vacant': 'gray',

        # Lease statuses
        'active': 'green',
        'expired': 'red',
        'terminated': 'gray',
        'upcoming': 'blue',

        # Maintenance priorities
        'low': 'gray',
        'medium': 'blue',
        'high': 'yellow',
        'urgent': 'red',

        # Maintenance statuses
        'open': 'blue',
        'in_progress': 'yellow',
        'completed': 'green',
        'cancelled': 'gray',

        # Payment statuses
        'pending': 'yellow',
        'paid': 'green',
        'overdue': 'red',
        'failed': 'red',
    }

    # Tailwind color classes for badges
    COLOR_CLASSES = {
        'green': 'bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20',
        'blue': 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20',
        'yellow': 'bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/20',
        'red': 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20',
        'gray': 'bg-muted text-muted-foreground border-border',
    }

    def __init__(
        self,
        status: str,
        label: Optional[str] = None,
        icon: Optional[str] = None,
        color: Optional[str] = None  # Override auto-color
    ):
        super().__init__()
        self.status = status.lower()
        self.label = label or status.replace('_', ' ').title()
        self.icon = icon

        # Determine color (auto or manual)
        if color:
            self.color = color
        else:
            self.color = self.STATUS_COLORS.get(self.status, 'gray')

    def render(self) -> str:
        """Render the status badge component."""

        # Get color classes
        color_class = self.COLOR_CLASSES.get(self.color, self.COLOR_CLASSES['gray'])

        # Icon HTML (icon is framework-controlled, safe to use in f-string)
        icon_html = ""
        if self.icon:
            icon_html = f'<i data-lucide="{self.icon}" class="w-3 h-3"></i>'

        # Use format_html to properly escape the label (which may contain user input)
        return format_html(
            '<span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border {}">'
            '{}'
            '{}'
            '</span>',
            color_class,
            mark_safe(icon_html),  # icon_html is safe (framework-controlled)
            self.label  # label is auto-escaped by format_html
        )
