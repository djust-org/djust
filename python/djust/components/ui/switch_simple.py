"""
Switch component for djust.

Simple stateless toggle switch with automatic Rust optimization.
This is a specialized checkbox styled as a Bootstrap form-switch.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustSwitch

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Switch(Component):
    """
    Switch toggle component (Bootstrap 5).

    A toggle switch styled component that behaves like a checkbox
    but with a modern switch appearance.

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings
    - Label, help text support
    - Bootstrap 5 form-switch styles

    Args:
        name: Switch name attribute
        label: Label text
        id: Switch ID (defaults to name if not provided)
        checked: Whether switch is checked/on
        disabled: Whether switch is disabled
        help_text: Help text shown below switch
        value: Switch value (default: "on")
        inline: Render inline (for use in groups)

    Example:
        >>> switch = Switch(
        ...     name="notifications",
        ...     label="Enable notifications",
        ...     checked=True
        ... )
        >>> switch.render()
        '<div class="mb-3">...'

        >>> # With help text
        >>> switch = Switch(
        ...     name="dark_mode",
        ...     label="Dark Mode",
        ...     help_text="Enable dark theme for the interface"
        ... )

        >>> # Disabled switch
        >>> switch = Switch(
        ...     name="premium",
        ...     label="Premium Features",
        ...     checked=False,
        ...     disabled=True
        ... )
    """

    _rust_impl_class = RustSwitch if _RUST_AVAILABLE else None

    template = """<div class="mb-3">
    <div class="form-check form-switch{% if inline %} form-check-inline{% endif %}">
        <input class="form-check-input"
               type="checkbox"
               role="switch"
               id="{{ id }}"
               name="{{ name }}"
               value="{{ value }}"{% if checked %} checked{% endif %}{% if disabled %} disabled{% endif %}>
        <label class="form-check-label" for="{{ id }}">
            {{ label }}
        </label>
    </div>{% if help_text %}
    <div class="form-text">{{ help_text }}</div>{% endif %}
</div>"""

    def __init__(
        self,
        name: str,
        label: str,
        id: Optional[str] = None,
        checked: bool = False,
        disabled: bool = False,
        help_text: Optional[str] = None,
        value: str = "on",
        inline: bool = False,
    ):
        # Pass kwargs to parent to create Rust instance
        super().__init__(
            name=name,
            label=label,
            id=id or name,  # Use name as default if id not provided
            checked=checked,
            disabled=disabled,
            help_text=help_text,
            value=value,
            inline=inline,
        )

        # Set instance attributes for Python/hybrid rendering
        self.name = name
        self.label = label
        # Note: self.id comes from base class property
        self.checked = checked
        self.disabled = disabled
        self.help_text = help_text
        self.value = value
        self.inline = inline

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "name": self.name,
            "label": self.label,
            "id": self.id,  # Use base class id property
            "checked": self.checked,
            "disabled": self.disabled,
            "help_text": self.help_text,
            "value": self.value,
            "inline": self.inline,
        }

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        parts = ['<div class="mb-3">']

        # Build form-check classes
        check_classes = ["form-check", "form-switch"]
        if self.inline:
            check_classes.append("form-check-inline")

        parts.append(f'    <div class="{" ".join(check_classes)}">')

        # Build input attributes
        attrs = [
            'class="form-check-input"',
            'type="checkbox"',
            'role="switch"',
            f'id="{self.id}"',  # Use base class id property
            f'name="{self.name}"',
            f'value="{self.value}"',
        ]
        if self.checked:
            attrs.append("checked")
        if self.disabled:
            attrs.append("disabled")

        parts.append(f"        <input {' '.join(attrs)}>")
        parts.append(
            f'        <label class="form-check-label" for="{self.id}">'
        )  # Use base class id property
        parts.append(f"            {self.label}")
        parts.append("        </label>")
        parts.append("    </div>")

        # Help text
        if self.help_text:
            parts.append(f'    <div class="form-text">{self.help_text}</div>')

        parts.append("</div>")

        return "\n".join(parts)
