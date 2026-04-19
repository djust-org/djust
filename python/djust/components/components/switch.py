"""Switch/toggle component for boolean state."""

import html
from typing import Optional

from djust import Component


class Switch(Component):
    """Style-agnostic toggle switch component using CSS custom properties.

    Renders a toggle switch with accessible markup and djust event integration.

    Usage in a LiveView::

        # Basic switch
        self.notifications = Switch(
            name="notifications",
            label="Enable notifications",
            checked=True,
        )

        # Switch with djust event
        self.dark_mode = Switch(
            name="dark_mode",
            label="Dark mode",
            action="toggle_dark_mode",
        )

        # Disabled switch
        self.locked = Switch(
            name="locked",
            label="Admin only",
            disabled=True,
        )

    In template::

        {{ notifications|safe }}
        {{ dark_mode|safe }}

    Programmatic toggle::

        self.dark_mode.toggle()  # flips checked state

    CSS Custom Properties::

        --dj-switch-width: switch width (default: 2.5rem)
        --dj-switch-height: switch height (default: 1.25rem)
        --dj-switch-bg: unchecked background (default: var(--muted))
        --dj-switch-bg-checked: checked background (default: var(--primary))
        --dj-switch-thumb: thumb color (default: white)
        --dj-switch-radius: border radius (default: 9999px)
        --dj-switch-transition: transition timing (default: 0.2s)

    Args:
        name: Form field name
        checked: Initial checked state
        label: Label text displayed next to the switch
        disabled: Whether the switch is disabled
        action: djust event handler name (for dj-change)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        name: str = "",
        checked: bool = False,
        label: Optional[str] = None,
        disabled: bool = False,
        action: Optional[str] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            checked=checked,
            label=label,
            disabled=disabled,
            action=action,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.checked = checked
        self.label = label
        self.disabled = disabled
        self.action = action
        self.custom_class = custom_class

    def toggle(self):
        """Toggle the checked state."""
        self.checked = not self.checked

    def _render_custom(self) -> str:
        """Render the switch HTML."""
        classes = ["dj-switch"]

        if self.checked:
            classes.append("dj-switch-checked")

        if self.disabled:
            classes.append("dj-switch-disabled")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        # Build input attributes
        input_attrs = ['type="checkbox"', 'class="dj-switch-input"']

        if self.name:
            input_attrs.append(f'name="{html.escape(self.name)}"')

        if self.checked:
            input_attrs.append("checked")

        if self.disabled:
            input_attrs.append("disabled")

        if self.action and not self.disabled:
            input_attrs.append(f'dj-change="{html.escape(self.action)}"')

        input_str = " ".join(input_attrs)

        parts = [
            f"<input {input_str}>",
            '<span class="dj-switch-slider"></span>',
        ]

        if self.label:
            parts.append(f'<span class="dj-switch-label">{html.escape(self.label)}</span>')

        content = "".join(parts)
        return f'<label class="{class_str}">{content}</label>'
