"""Badge component for status and priority indicators."""

import html
from typing import Dict, Optional

from djust import Component


class Badge(Component):
    """Style-agnostic badge component using CSS custom properties.

    Uses CSS variables with sensible fallbacks so it works without a theme
    but automatically picks up theme tokens when available.

    Usage in a LiveView::

        # Manual variant
        self.badge = Badge("custom label", variant="info", size="md")

        # Auto-colored status badge
        self.status = Badge.status("running")  # → success variant
        self.task = Badge.status("pending")    # → warning variant

        # Auto-colored priority badge
        self.p0 = Badge.priority("P0")  # → danger variant
        self.p2 = Badge.priority("P2")  # → info variant

    In template::

        {{ badge|safe }}
        {{ status|safe }}

    CSS Custom Properties::

        --dj-badge-bg: background color (default: var(--muted))
        --dj-badge-fg: text color (default: var(--foreground))
        --dj-badge-radius: border radius (default: 0.25rem)
        --dj-badge-padding: internal padding (default: 0.25rem 0.5rem)
        --dj-badge-font-size: text size (default: 0.75rem)
        --dj-badge-font-weight: text weight (default: 500)

        # Variant-specific colors
        --dj-badge-success-bg, --dj-badge-success-fg
        --dj-badge-info-bg, --dj-badge-info-fg
        --dj-badge-warning-bg, --dj-badge-warning-fg
        --dj-badge-danger-bg, --dj-badge-danger-fg
        --dj-badge-muted-bg, --dj-badge-muted-fg

    Args:
        label: Text to display in badge
        variant: Color variant (default, success, info, warning, danger, muted)
        size: Size variant (sm, md, lg)
        custom_class: Additional CSS classes
    """

    # Default status → variant mapping (can be customized)
    DEFAULT_STATUS_MAP: Dict[str, str] = {
        # Success states
        "done": "success",
        "completed": "success",
        "passed": "success",
        "success": "success",
        "active": "success",
        "online": "success",
        "published": "success",
        # Info states
        "in_progress": "info",
        "running": "info",
        "processing": "info",
        "info": "info",
        # Warning states
        "pending": "warning",
        "starting": "warning",
        "warning": "warning",
        "draft": "warning",
        "review": "warning",
        # Danger/error states
        "failed": "danger",
        "error": "danger",
        "danger": "danger",
        "offline": "danger",
        "rejected": "danger",
        # Muted/neutral states
        "skipped": "muted",
        "cancelled": "muted",
        "archived": "muted",
        "disabled": "muted",
        "muted": "muted",
    }

    # Default priority → variant mapping (can be customized)
    DEFAULT_PRIORITY_MAP: Dict[str, str] = {
        "P0": "danger",
        "P1": "warning",
        "P2": "info",
        "P3": "muted",
    }

    def __init__(
        self,
        label: str,
        variant: str = "default",
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            label=label, variant=variant, size=size, custom_class=custom_class, **kwargs
        )
        self.label = label
        self.variant = variant
        self.size = size
        self.custom_class = custom_class

    @classmethod
    def status(
        cls,
        status: str,
        size: str = "md",
        custom_map: Optional[Dict[str, str]] = None,
    ) -> "Badge":
        """Create a status badge with auto-colored variant.

        Args:
            status: Status string (e.g., "running", "failed", "pending")
            size: Size variant (sm, md, lg)
            custom_map: Optional custom status→variant mapping

        Returns:
            Badge instance with appropriate variant
        """
        status_map = custom_map or cls.DEFAULT_STATUS_MAP
        variant = status_map.get(status.lower(), "default")
        return cls(status, variant=variant, size=size)

    @classmethod
    def priority(
        cls,
        priority: str,
        size: str = "md",
        custom_map: Optional[Dict[str, str]] = None,
    ) -> "Badge":
        """Create a priority badge with auto-colored variant.

        Args:
            priority: Priority string (e.g., "P0", "P1", "P2", "P3")
            size: Size variant (sm, md, lg)
            custom_map: Optional custom priority→variant mapping

        Returns:
            Badge instance with appropriate variant
        """
        priority_map = custom_map or cls.DEFAULT_PRIORITY_MAP
        variant = priority_map.get(priority.upper(), "default")
        return cls(priority, variant=variant, size=size)

    def _render_custom(self) -> str:
        """Render the badge HTML."""
        classes = ["dj-badge"]

        # Add variant class
        if self.variant != "default":
            classes.append(f"dj-badge-{self.variant}")

        # Add size class
        if self.size != "md":
            classes.append(f"dj-badge-{self.size}")

        # Add custom classes
        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)
        label_escaped = html.escape(self.label)

        return f'<span class="{class_str}">{label_escaped}</span>'
