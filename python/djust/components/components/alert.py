"""Alert component for dismissible notification messages."""

import html
from typing import Any, Optional

from django.utils.html import conditional_escape
from djust import Component


class Alert(Component):
    """Alert component for contextual feedback messages.

    Displays contextual feedback messages with optional dismiss functionality.

    Styled by ``djust_components/components.css``, which ``{% theme_head %}``
    links (#2993), on the active theme's tokens: a tinted panel with a
    border in the variant's colour and the text on ``--foreground``. The
    classes it renders are ``dj-alert``, ``dj-alert-<variant>``,
    ``dj-alert-dismissible``, ``dj-alert-icon``, ``dj-alert-message`` and
    ``dj-alert-dismiss``.

    Usage in a LiveView::

        # Manual variant
        self.alert = Alert("Something happened", variant="info")

        # Factory methods
        self.success = Alert.success("Item saved!")
        self.error = Alert.danger("Something went wrong")
        self.warn = Alert.warning("Disk space low", dismissible=True)

        # Dismissible with djust event
        self.notice = Alert(
            "Session expiring soon",
            variant="warning",
            dismissible=True,
            action="dismiss_notice",
        )

    In template::

        {{ alert|safe }}
        {{ success|safe }}

    CSS custom properties the stylesheet reads before the theme tokens, to
    restyle alerts without touching the theme::

        --dj-alert-bg: background color
        --dj-alert-fg: text color
        --dj-alert-border: border color
        --dj-alert-radius: border radius (default: the theme's --radius-md)
        --dj-alert-padding: internal padding (default: 0.75rem 1rem)

        # Variant-specific colors
        --dj-alert-info-bg, --dj-alert-info-fg, --dj-alert-info-border
        --dj-alert-success-bg, --dj-alert-success-fg, --dj-alert-success-border
        --dj-alert-warning-bg, --dj-alert-warning-fg, --dj-alert-warning-border
        --dj-alert-danger-bg, --dj-alert-danger-fg, --dj-alert-danger-border

    Args:
        message: Alert text content
        variant: Color variant (info, success, warning, danger)
        dismissible: Whether the alert can be dismissed
        action: djust event handler name for dismiss (for dj-click)
        icon: Optional icon text (emoji or HTML)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        message: str,
        variant: str = "info",
        dismissible: bool = False,
        action: Optional[str] = None,
        icon: Optional[str] = None,
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message=message,
            variant=variant,
            dismissible=dismissible,
            action=action,
            icon=icon,
            custom_class=custom_class,
            **kwargs,
        )
        self.message = message
        self.variant = variant
        self.dismissible = dismissible
        self.action = action
        self.icon = icon
        self.custom_class = custom_class

    @classmethod
    def info(cls, message: str, **kwargs: Any) -> "Alert":
        """Create an info alert."""
        return cls(message, variant="info", **kwargs)

    @classmethod
    def success(cls, message: str, **kwargs: Any) -> "Alert":
        """Create a success alert."""
        return cls(message, variant="success", **kwargs)

    @classmethod
    def warning(cls, message: str, **kwargs: Any) -> "Alert":
        """Create a warning alert."""
        return cls(message, variant="warning", **kwargs)

    @classmethod
    def danger(cls, message: str, **kwargs: Any) -> "Alert":
        """Create a danger alert."""
        return cls(message, variant="danger", **kwargs)

    def _render_custom(self) -> str:
        """Render the alert HTML."""
        classes = ["dj-alert", f"dj-alert-{conditional_escape(self.variant)}"]

        if self.dismissible:
            classes.append("dj-alert-dismissible")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)
        attrs = [f'class="{class_str}"', 'role="alert"']
        attrs_str = " ".join(attrs)

        parts = []

        if self.icon:
            parts.append(f'<span class="dj-alert-icon">{conditional_escape(self.icon)}</span>')

        parts.append(f'<span class="dj-alert-message">{html.escape(self.message)}</span>')

        if self.dismissible:
            dismiss_attrs = ['class="dj-alert-dismiss"', 'aria-label="Dismiss"']
            ea = self.event_attrs(self.action)
            if ea:
                dismiss_attrs.append(ea)
            dismiss_str = " ".join(dismiss_attrs)
            parts.append(f"<button {dismiss_str}>&times;</button>")

        content = "".join(parts)
        return f"<div {attrs_str}>{content}</div>"
