"""Toast component for transient notification messages."""

import html
from typing import Optional

from djust import Component


class Toast(Component):
    """Style-agnostic toast notification component using CSS custom properties.

    Displays a brief message that can auto-dismiss after a duration.

    Usage in a LiveView::

        # Factory methods (recommended)
        self.saved = Toast.success("Changes saved!")
        self.oops = Toast.error("Something went wrong")
        self.heads_up = Toast.warning("Disk space low")
        self.fyi = Toast.info("New version available")

        # Manual
        self.toast = Toast(
            "Custom message",
            type="success",
            duration=5000,
            dismissible=True,
        )

    In template::

        {{ saved|safe }}
        {{ toast|safe }}

    CSS Custom Properties::

        --dj-toast-bg: background color
        --dj-toast-fg: text color
        --dj-toast-border: border color
        --dj-toast-radius: border radius (default: 0.375rem)
        --dj-toast-padding: internal padding (default: 0.75rem 1rem)
        --dj-toast-shadow: box shadow

        # Type-specific colors
        --dj-toast-info-bg, --dj-toast-info-fg, --dj-toast-info-border
        --dj-toast-success-bg, --dj-toast-success-fg, --dj-toast-success-border
        --dj-toast-warning-bg, --dj-toast-warning-fg, --dj-toast-warning-border
        --dj-toast-error-bg, --dj-toast-error-fg, --dj-toast-error-border

    Args:
        message: Toast text content
        type: Notification type (info, success, warning, error)
        duration: Auto-dismiss duration in ms (0 = no auto-dismiss)
        dismissible: Whether the toast has a dismiss button
        action: djust event handler name for dismiss (for dj-click)
        custom_class: Additional CSS classes
    """

    ALLOWED_TYPES = {"info", "success", "warning", "error"}

    def __init__(
        self,
        message: str,
        type: str = "info",
        duration: int = 3000,
        dismissible: bool = True,
        action: Optional[str] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            message=message,
            type=type,
            duration=duration,
            dismissible=dismissible,
            action=action,
            custom_class=custom_class,
            **kwargs,
        )
        self.message = message
        self.type = type
        self.duration = duration
        self.dismissible = dismissible
        self.action = action
        self.custom_class = custom_class

    @classmethod
    def info(cls, message: str, **kwargs) -> "Toast":
        """Create an info toast."""
        return cls(message, type="info", **kwargs)

    @classmethod
    def success(cls, message: str, **kwargs) -> "Toast":
        """Create a success toast."""
        return cls(message, type="success", **kwargs)

    @classmethod
    def warning(cls, message: str, **kwargs) -> "Toast":
        """Create a warning toast."""
        return cls(message, type="warning", **kwargs)

    @classmethod
    def error(cls, message: str, **kwargs) -> "Toast":
        """Create an error toast."""
        return cls(message, type="error", **kwargs)

    def _render_custom(self) -> str:
        """Render the toast HTML."""
        safe_type = self.type if self.type in self.ALLOWED_TYPES else "info"
        classes = ["dj-toast", f"dj-toast-{safe_type}"]

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        attrs = [f'class="{class_str}"', 'role="status"', 'aria-live="polite"']

        if self.duration > 0:
            attrs.append(f'data-duration="{self.duration}"')

        attrs_str = " ".join(attrs)

        parts = []
        parts.append(f'<span class="dj-toast-message">{html.escape(self.message)}</span>')

        if self.dismissible:
            dismiss_attrs = ['class="dj-toast-dismiss"', 'aria-label="Dismiss"']
            if self.action:
                dismiss_attrs.append(f'dj-click="{html.escape(self.action)}"')
            dismiss_str = " ".join(dismiss_attrs)
            parts.append(f"<button {dismiss_str}>&times;</button>")

        content = "".join(parts)
        return f"<div {attrs_str}>{content}</div>"
