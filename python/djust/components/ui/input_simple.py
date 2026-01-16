"""
Input component for djust.

Simple stateless input field with automatic Rust optimization.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustInput

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Input(Component):
    """
    Input field component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
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

    _rust_impl_class = RustInput if _RUST_AVAILABLE else None

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

        # Pass kwargs to parent to create Rust instance
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

    def get_context_data(self):
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

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        parts = ['<div class="mb-3">']

        # Label
        if self.label:
            required_mark = ' <span class="text-danger">*</span>' if self.required else ""
            parts.append(
                f'    <label for="{self.input_id}" class="form-label">{self.label}{required_mark}</label>'
            )

        # Build input classes
        input_classes = ["form-control"]
        if self.size != "md":
            input_classes.append(f"form-control-{self.size}")
        if self.validation_state == "valid":
            input_classes.append("is-valid")
        elif self.validation_state == "invalid":
            input_classes.append("is-invalid")

        # Build input attributes
        attrs = [
            f'type="{self.input_type}"',
            f'class="{" ".join(input_classes)}"',
            f'id="{self.input_id}"',
            f'name="{self.name}"',
        ]
        if self.value:
            attrs.append(f'value="{self.value}"')
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.required:
            attrs.append("required")
        if self.disabled:
            attrs.append("disabled")
        if self.readonly:
            attrs.append("readonly")

        parts.append(f"    <input {' '.join(attrs)}>")

        # Help text
        if self.help_text:
            parts.append(f'    <div class="form-text">{self.help_text}</div>')

        # Validation feedback
        if self.validation_message:
            feedback_class = (
                "valid-feedback" if self.validation_state == "valid" else "invalid-feedback"
            )
            parts.append(f'    <div class="{feedback_class}">{self.validation_message}</div>')

        parts.append("</div>")

        return "\n".join(parts)
