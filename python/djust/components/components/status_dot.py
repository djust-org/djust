"""StatusDot component for animated status indicators."""

import html

from typing import Dict, Optional

from djust import Component


# Sentinel value to distinguish "not provided" from "explicitly None"
_NOT_PROVIDED = object()


class StatusDot(Component):
    """Style-agnostic animated status indicator dot using CSS custom properties.

    Displays a small colored dot with optional pulse animation for active states.

    Usage in a LiveView::

        # Basic usage with auto-coloring
        self.agent_status = StatusDot("running")     # green, pulsing
        self.task_status = StatusDot("completed")    # blue, static
        self.failed_status = StatusDot("failed", size="lg")  # red, static

        # Manual variant control
        self.custom = StatusDot("active", variant="success", animate="pulse")

    In template::

        {{ agent_status|safe }}

    CSS Custom Properties::

        --dj-status-dot-size: dot diameter (default: 0.5rem for md)
        --dj-status-dot-success: success color (default: #10b981)
        --dj-status-dot-info: info color (default: #3b82f6)
        --dj-status-dot-warning: warning color (default: #f59e0b)
        --dj-status-dot-danger: danger color (default: #ef4444)
        --dj-status-dot-muted: muted color (default: #6b7280)

    CSS Animations::

        @keyframes dj-status-dot-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(1.1); }
        }

        @keyframes dj-status-dot-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

    Args:
        status: Status string (e.g., "running", "completed", "failed")
        variant: Color variant (success, info, warning, danger, muted)
        size: Size variant (sm, md, lg)
        animate: Animation type (pulse, spin, fade, None)
        tooltip: Optional tooltip text (requires title attribute support)
        custom_class: Additional CSS classes
    """

    # Default status → variant mapping
    DEFAULT_STATUS_MAP: Dict[str, str] = {
        # Success states
        "running": "success",
        "active": "success",
        "online": "success",
        "passed": "success",
        "success": "success",
        # Info states
        "completed": "info",
        "done": "info",
        "idle": "info",
        "info": "info",
        # Warning states
        "starting": "warning",
        "pending": "warning",
        "warning": "warning",
        "paused": "warning",
        # Danger states
        "failed": "danger",
        "error": "danger",
        "offline": "danger",
        "danger": "danger",
        # Muted states
        "stopped": "muted",
        "skipped": "muted",
        "cancelled": "muted",
        "disabled": "muted",
    }

    # Default status → animation mapping
    DEFAULT_ANIMATION_MAP: Dict[str, Optional[str]] = {
        "running": "pulse",
        "starting": "pulse",
        "processing": "pulse",
        "loading": "spin",
        # All others: None (static)
    }

    def __init__(
        self,
        status: str,
        variant: Optional[str] = None,
        size: str = "md",
        animate: Optional[str] = _NOT_PROVIDED,
        tooltip: Optional[str] = None,
        custom_class: str = "",
        custom_status_map: Optional[Dict[str, str]] = None,
        custom_animation_map: Optional[Dict[str, Optional[str]]] = None,
        **kwargs,
    ):
        super().__init__(
            status=status,
            variant=variant,
            size=size,
            animate=animate if animate is not _NOT_PROVIDED else None,
            tooltip=tooltip,
            custom_class=custom_class,
            **kwargs,
        )
        self.status = status
        self.size = size
        self.tooltip = tooltip
        self.custom_class = custom_class

        # Resolve variant from status if not explicitly provided
        status_map = custom_status_map or self.DEFAULT_STATUS_MAP
        self.variant = variant or status_map.get(status.lower(), "muted")

        # Resolve animation from status if not explicitly provided
        # If animate is _NOT_PROVIDED, use the animation map
        # If animate is explicitly None or a string, use that value
        animation_map = custom_animation_map or self.DEFAULT_ANIMATION_MAP
        if animate is _NOT_PROVIDED:
            self.animate = animation_map.get(status.lower())
        else:
            self.animate = animate

    def _render_custom(self) -> str:
        """Render the status dot HTML."""
        classes = ["dj-status-dot"]

        # Add variant class
        classes.append(f"dj-status-dot-{self.variant}")

        # Add size class
        if self.size != "md":
            classes.append(f"dj-status-dot-{self.size}")

        # Add animation class
        if self.animate:
            classes.append(f"dj-status-dot-{self.animate}")

        # Add custom classes
        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        # Add tooltip if provided
        title_attr = f' title="{self.tooltip}"' if self.tooltip else ""

        return f'<span class="{class_str}"{title_attr}></span>'
