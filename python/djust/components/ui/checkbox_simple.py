"""
Checkbox component for djust.

Simple stateless checkbox with automatic Rust optimization.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustCheckbox

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Checkbox(Component):
    """
    Checkbox component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings
    - Label, help text support
    - Switch style option

    Args:
        name: Checkbox name attribute
        id: Checkbox ID (defaults to name if not provided)
        label: Label text
        checked: Whether checkbox is checked
        value: Checkbox value (default: "on")
        help_text: Help text shown below checkbox
        disabled: Whether checkbox is disabled
        switch: Render as switch instead of checkbox
        inline: Render inline (for use in groups)

    Example:
        >>> checkbox = Checkbox(
        ...     name="terms",
        ...     label="I agree to the terms and conditions",
        ...     required=True
        ... )
        >>> checkbox.render()
        '<div class="mb-3">...'
    """

    _rust_impl_class = RustCheckbox if _RUST_AVAILABLE else None

    template = """<div class="mb-3">
    <div class="form-check{% if switch %} form-check-switch{% endif %}{% if inline %} form-check-inline{% endif %}">
        <input class="form-check-input"
               type="checkbox"
               id="{{ checkbox_id }}"
               name="{{ name }}"
               value="{{ value }}"{% if checked %} checked{% endif %}{% if disabled %} disabled{% endif %}>
        <label class="form-check-label" for="{{ checkbox_id }}">
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
        value: str = "on",
        help_text: Optional[str] = None,
        disabled: bool = False,
        switch: bool = False,
        inline: bool = False,
    ):
        # Use name as ID if not provided
        checkbox_id = id or name

        # Pass kwargs to parent to create Rust instance
        super().__init__(
            name=name,
            label=label,
            id=checkbox_id,
            checked=checked,
            value=value,
            help_text=help_text,
            disabled=disabled,
            switch=switch,
            inline=inline,
        )

        # Set instance attributes for Python/hybrid rendering
        self.name = name
        self.label = label
        self.checkbox_id = checkbox_id
        self.checked = checked
        self.value = value
        self.help_text = help_text
        self.disabled = disabled
        self.switch = switch
        self.inline = inline

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "name": self.name,
            "label": self.label,
            "checkbox_id": self.checkbox_id,
            "checked": self.checked,
            "value": self.value,
            "help_text": self.help_text,
            "disabled": self.disabled,
            "switch": self.switch,
            "inline": self.inline,
        }

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        parts = ['<div class="mb-3">']

        # Build form-check classes
        check_classes = ["form-check"]
        if self.switch:
            check_classes.append("form-switch")
        if self.inline:
            check_classes.append("form-check-inline")

        parts.append(f'    <div class="{" ".join(check_classes)}">')

        # Build input attributes
        attrs = [
            'class="form-check-input"',
            'type="checkbox"',
            f'id="{self.checkbox_id}"',
            f'name="{self.name}"',
            f'value="{self.value}"',
        ]
        if self.checked:
            attrs.append("checked")
        if self.disabled:
            attrs.append("disabled")

        parts.append(f"        <input {' '.join(attrs)}>")
        parts.append(f'        <label class="form-check-label" for="{self.checkbox_id}">')
        parts.append(f"            {self.label}")
        parts.append("        </label>")
        parts.append("    </div>")

        # Help text
        if self.help_text:
            parts.append(f'    <div class="form-text">{self.help_text}</div>')

        parts.append("</div>")

        return "\n".join(parts)
