"""
Checkbox component for djust.

Simple stateless checkbox.
"""

from typing import Any, Optional
from ..base import Component


class Checkbox(Component):
    """
    Checkbox component (Bootstrap 5).

    Features:
    - Rendered through its template (there is no native Rust implementation)
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

        # Pass kwargs to the parent Component
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

    def get_context_data(self) -> dict[str, Any]:
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
