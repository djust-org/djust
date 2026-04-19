"""Button component for actions and navigation."""

import html
from typing import Dict, Optional

from djust import Component


class Button(Component):
    """Style-agnostic button component using CSS custom properties.

    Integrates with djust event system and supports various visual styles.

    Usage in a LiveView::

        # Primary action button
        self.submit = Button(
            label="Save",
            variant="primary",
            action="save_form",
            data={"form_id": "123"},
        )

        # Button with icon
        self.delete = Button(
            label="Delete",
            variant="danger",
            action="delete_item",
            icon="🗑️",
            icon_position="left",
        )

        # Loading state
        self.loading = Button(
            label="Processing...",
            variant="primary",
            loading=True,
        )

        # Disabled button
        self.disabled = Button(
            label="Submit",
            disabled=True,
        )

    In template::

        {{ submit|safe }}
        {{ delete|safe }}

    CSS Custom Properties::

        --dj-btn-bg: background color
        --dj-btn-fg: foreground/text color
        --dj-btn-border: border color
        --dj-btn-radius: border radius
        --dj-btn-padding: internal padding
        --dj-btn-font-size: text size
        --dj-btn-font-weight: text weight

        # Variant-specific colors
        --dj-btn-primary-bg, --dj-btn-primary-fg, --dj-btn-primary-border
        --dj-btn-secondary-bg, --dj-btn-secondary-fg, --dj-btn-secondary-border
        --dj-btn-danger-bg, --dj-btn-danger-fg, --dj-btn-danger-border
        --dj-btn-success-bg, --dj-btn-success-fg, --dj-btn-success-border
        --dj-btn-ghost-bg, --dj-btn-ghost-fg, --dj-btn-ghost-border
        --dj-btn-link-fg, --dj-btn-text-fg

    Args:
        label: Button text
        variant: Style variant (primary, secondary, danger, success, ghost, link, text)
        action: djust event handler name (for dj-click)
        data: Data attributes dictionary
        onclick: JavaScript onclick handler (use sparingly)
        icon: Icon text (emoji or HTML)
        icon_position: Icon placement (left, right)
        size: Size variant (sm, md, lg)
        disabled: Disabled state
        loading: Loading state (shows spinner, disables button)
        type: HTML button type (button, submit, reset)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        label: str,
        variant: str = "primary",
        action: Optional[str] = None,
        data: Optional[Dict[str, any]] = None,
        onclick: Optional[str] = None,
        icon: Optional[str] = None,
        icon_position: str = "left",
        size: str = "md",
        disabled: bool = False,
        loading: bool = False,
        type: str = "button",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            label=label,
            variant=variant,
            action=action,
            data=data,
            onclick=onclick,
            icon=icon,
            icon_position=icon_position,
            size=size,
            disabled=disabled,
            loading=loading,
            type=type,
            custom_class=custom_class,
            **kwargs,
        )
        self.label = label
        self.variant = variant
        self.action = action
        self.data = data or {}
        self.onclick = onclick
        self.icon = icon
        self.icon_position = icon_position
        self.size = size
        self.disabled = disabled
        self.loading = loading
        self.type = type
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the button HTML."""
        classes = ["dj-btn"]

        # Add variant class
        if self.variant != "primary":
            classes.append(f"dj-btn-{self.variant}")

        # Add size class
        if self.size != "md":
            classes.append(f"dj-btn-{self.size}")

        # Add state classes
        if self.loading:
            classes.append("dj-btn-loading")

        # Add custom classes
        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        # Build attributes
        attrs = [f'class="{class_str}"', f'type="{self.type}"']

        # Disable button if disabled or loading
        if self.disabled or self.loading:
            attrs.append("disabled")

        # Add dj-click action
        if self.action and not self.disabled and not self.loading:
            attrs.append(f'dj-click="{html.escape(self.action)}"')

        # Add data attributes
        for k, v in self.data.items():
            attrs.append(f'data-{html.escape(k)}="{html.escape(str(v))}"')

        # Add onclick (use sparingly)
        if self.onclick and not self.disabled and not self.loading:
            attrs.append(f'onclick="{html.escape(self.onclick)}"')

        attrs_str = " ".join(attrs)

        # Build button content
        content_parts = []

        # Loading spinner
        if self.loading:
            content_parts.append('<span class="dj-btn-spinner"></span>')

        # Icon (left position)
        if self.icon and self.icon_position == "left":
            content_parts.append(f'<span class="dj-btn-icon dj-btn-icon-left">{self.icon}</span>')

        # Label
        content_parts.append(f'<span class="dj-btn-label">{html.escape(self.label)}</span>')

        # Icon (right position)
        if self.icon and self.icon_position == "right":
            content_parts.append(f'<span class="dj-btn-icon dj-btn-icon-right">{self.icon}</span>')

        content = "".join(content_parts)

        return f"<button {attrs_str}>{content}</button>"
