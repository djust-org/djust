"""
Input component for djust.

Simple stateless input field.
"""

from typing import Any, Optional
from ..base import Component


class Input(Component):
    """
    Input field component (Bootstrap 5).

    Features:
    - Rendered through its template (there is no native Rust input)
    - Python fallback with f-strings
    - Label, placeholder, help text support
    - Validation states

    Args:
        name: Input name attribute
        id: Input ID (defaults to name if not provided)
        label: Label text
        type: Input type (text, email, password, number, etc.)
        value: Initial value
        placeholder: Placeholder text
        help_text: Help text shown below input
        required: Whether field is required
        disabled: Whether field is disabled
        readonly: Whether field is readonly
        size: Input size (sm, md, lg)
        validation_state: Validation state (None, 'valid', 'invalid')
        validation_message: Validation feedback message

    Example:
        >>> input_field = Input(
        ...     name="email",
        ...     label="Email Address",
        ...     type="email",
        ...     placeholder="you@example.com",
        ...     required=True
        ... )
        >>> input_field.render()
        '<div class="mb-3">...'
    """

    template = """<div class="mb-3">{% if label %}
    <label for="{{ input_id }}" class="form-label">{{ label }}{% if required %} <span class="text-danger">*</span>{% endif %}</label>{% endif %}
    <input type="{{ input_type }}"
           class="form-control{% if size != "md" %} form-control-{{ size }}{% endif %}{% if validation_state == "valid" %} is-valid{% endif %}{% if validation_state == "invalid" %} is-invalid{% endif %}"
           id="{{ input_id }}"
           name="{{ name }}"{% if value %} value="{{ value }}"{% endif %}{% if placeholder %} placeholder="{{ placeholder }}"{% endif %}{% if required %} required{% endif %}{% if disabled %} disabled{% endif %}{% if readonly %} readonly{% endif %}>{% if help_text %}
    <div class="form-text">{{ help_text }}</div>{% endif %}{% if validation_message %}
    <div class="{% if validation_state == "valid" %}valid{% else %}invalid{% endif %}-feedback">{{ validation_message }}</div>{% endif %}
</div>"""

    def __init__(
        self,
        name: str,
        id: Optional[str] = None,
        label: Optional[str] = None,
        type: str = "text",
        value: str = "",
        placeholder: Optional[str] = None,
        help_text: Optional[str] = None,
        required: bool = False,
        disabled: bool = False,
        readonly: bool = False,
        size: str = "md",
        validation_state: Optional[str] = None,
        validation_message: Optional[str] = None,
    ):
        # Use name as ID if not provided
        input_id = id or name

        # Pass kwargs to the parent Component
        super().__init__(
            name=name,
            id=input_id,
            label=label,
            type=type,
            value=value,
            placeholder=placeholder,
            help_text=help_text,
            required=required,
            disabled=disabled,
            readonly=readonly,
            size=size,
            validation_state=validation_state,
            validation_message=validation_message,
        )

        # Set instance attributes for Python/hybrid rendering
        self.name = name
        self.input_id = input_id
        self.label = label
        self.input_type = type
        self.value = value
        self.placeholder = placeholder
        self.help_text = help_text
        self.required = required
        self.disabled = disabled
        self.readonly = readonly
        self.size = size
        self.validation_state = validation_state
        self.validation_message = validation_message

    def get_context_data(self) -> dict[str, Any]:
        """Return context for template rendering."""
        return {
            "name": self.name,
            "input_id": self.input_id,
            "label": self.label,
            "input_type": self.input_type,
            "value": self.value,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "required": self.required,
            "disabled": self.disabled,
            "readonly": self.readonly,
            "size": self.size,
            "validation_state": self.validation_state,
            "validation_message": self.validation_message,
        }
