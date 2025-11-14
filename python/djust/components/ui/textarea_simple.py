"""
TextArea component for djust.

Simple stateless textarea field with automatic Rust optimization.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustTextArea

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class TextArea(Component):
    """
    Multi-line textarea component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings
    - Label, placeholder, help text support
    - Validation states
    - Configurable rows

    Args:
        name: Textarea name attribute
        id: Textarea ID (defaults to name if not provided)
        label: Label text
        value: Initial value
        placeholder: Placeholder text
        help_text: Help text shown below textarea
        rows: Number of visible text rows
        required: Whether field is required
        disabled: Whether field is disabled
        readonly: Whether field is readonly
        validation_state: Validation state (None, 'valid', 'invalid')
        validation_message: Validation feedback message

    Example:
        >>> textarea = TextArea(
        ...     name="description",
        ...     label="Description",
        ...     placeholder="Enter description...",
        ...     rows=5,
        ...     required=True
        ... )
        >>> textarea.render()
        '<div class="mb-3">...'
    """

    _rust_impl_class = RustTextArea if _RUST_AVAILABLE else None

    template = """<div class="mb-3">{% if label %}
    <label for="{{ textarea_id }}" class="form-label">{{ label }}{% if required %} <span class="text-danger">*</span>{% endif %}</label>{% endif %}
    <textarea class="form-control{% if validation_state == "valid" %} is-valid{% endif %}{% if validation_state == "invalid" %} is-invalid{% endif %}"
              id="{{ textarea_id }}"
              name="{{ name }}"
              rows="{{ rows }}"{% if placeholder %} placeholder="{{ placeholder }}"{% endif %}{% if required %} required{% endif %}{% if disabled %} disabled{% endif %}{% if readonly %} readonly{% endif %}>{{ value }}</textarea>{% if help_text %}
    <div class="form-text">{{ help_text }}</div>{% endif %}{% if validation_message %}
    <div class="{% if validation_state == "valid" %}valid{% else %}invalid{% endif %}-feedback">{{ validation_message }}</div>{% endif %}
</div>"""

    def __init__(
        self,
        name: str,
        id: Optional[str] = None,
        label: Optional[str] = None,
        value: str = "",
        placeholder: Optional[str] = None,
        help_text: Optional[str] = None,
        rows: int = 3,
        required: bool = False,
        disabled: bool = False,
        readonly: bool = False,
        validation_state: Optional[str] = None,
        validation_message: Optional[str] = None,
    ):
        # Use name as ID if not provided
        textarea_id = id or name

        # Pass kwargs to parent to create Rust instance
        super().__init__(
            name=name,
            id=textarea_id,
            label=label,
            value=value,
            placeholder=placeholder,
            help_text=help_text,
            rows=rows,
            required=required,
            disabled=disabled,
            readonly=readonly,
            validation_state=validation_state,
            validation_message=validation_message,
        )

        # Set instance attributes for Python/hybrid rendering
        self.name = name
        self.textarea_id = textarea_id
        self.label = label
        self.value = value
        self.placeholder = placeholder
        self.help_text = help_text
        self.rows = rows
        self.required = required
        self.disabled = disabled
        self.readonly = readonly
        self.validation_state = validation_state
        self.validation_message = validation_message

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "name": self.name,
            "textarea_id": self.textarea_id,
            "label": self.label,
            "value": self.value,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "rows": self.rows,
            "required": self.required,
            "disabled": self.disabled,
            "readonly": self.readonly,
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
                f'    <label for="{self.textarea_id}" class="form-label">{self.label}{required_mark}</label>'
            )

        # Build textarea classes
        textarea_classes = ["form-control"]
        if self.validation_state == "valid":
            textarea_classes.append("is-valid")
        elif self.validation_state == "invalid":
            textarea_classes.append("is-invalid")

        # Build textarea attributes
        attrs = [
            f'class="{" ".join(textarea_classes)}"',
            f'id="{self.textarea_id}"',
            f'name="{self.name}"',
            f'rows="{self.rows}"',
        ]
        if self.placeholder:
            attrs.append(f'placeholder="{self.placeholder}"')
        if self.required:
            attrs.append("required")
        if self.disabled:
            attrs.append("disabled")
        if self.readonly:
            attrs.append("readonly")

        parts.append(f"    <textarea {' '.join(attrs)}>{self.value}</textarea>")

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
