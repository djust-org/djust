"""
Select component for djust.

Simple stateless select dropdown with automatic Rust optimization.
"""

from typing import List, Dict, Optional, Union
from ..base import Component

try:
    from djust._rust import RustSelect

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Select(Component):
    """
    Select dropdown component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings
    - Label, help text support
    - Validation states

    Args:
        name: Select name attribute
        options: List of option dicts with 'value' and 'label' keys, or list of strings
        id: Select ID (defaults to name if not provided)
        label: Label text
        value: Selected value
        help_text: Help text shown below select
        required: Whether field is required
        disabled: Whether field is disabled
        size: Select size (sm, md, lg)
        multiple: Allow multiple selections
        validation_state: Validation state (None, 'valid', 'invalid')
        validation_message: Validation feedback message

    Example:
        >>> select = Select(
        ...     name="country",
        ...     label="Country",
        ...     options=[
        ...         {'value': 'us', 'label': 'United States'},
        ...         {'value': 'uk', 'label': 'United Kingdom'},
        ...         {'value': 'ca', 'label': 'Canada'},
        ...     ],
        ...     required=True
        ... )
        >>> select.render()
        '<div class="mb-3">...'
    """

    _rust_impl_class = RustSelect if _RUST_AVAILABLE else None

    # Note: Using Python fallback for simplicity with dynamic lists

    def __init__(
        self,
        name: str,
        options: Union[List[str], List[Dict[str, str]]],
        id: Optional[str] = None,
        label: Optional[str] = None,
        value: Optional[str] = None,
        help_text: Optional[str] = None,
        required: bool = False,
        disabled: bool = False,
        size: str = "md",
        multiple: bool = False,
        validation_state: Optional[str] = None,
        validation_message: Optional[str] = None,
    ):
        # Use name as ID if not provided
        select_id = id or name

        # Normalize options to list of dicts
        normalized_options = []
        for opt in options:
            if isinstance(opt, str):
                normalized_options.append({"value": opt, "label": opt})
            else:
                normalized_options.append(opt)

        # Pass kwargs to parent
        super().__init__(
            name=name,
            options=normalized_options,
            id=select_id,
            label=label,
            value=value,
            help_text=help_text,
            required=required,
            disabled=disabled,
            size=size,
            multiple=multiple,
            validation_state=validation_state,
            validation_message=validation_message,
        )

        # Set instance attributes for Python rendering
        self.name = name
        self.options = normalized_options
        self.select_id = select_id
        self.label = label
        self.value = value
        self.help_text = help_text
        self.required = required
        self.disabled = disabled
        self.size = size
        self.multiple = multiple
        self.validation_state = validation_state
        self.validation_message = validation_message

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "name": self.name,
            "options": self.options,
            "select_id": self.select_id,
            "label": self.label,
            "value": self.value,
            "help_text": self.help_text,
            "required": self.required,
            "disabled": self.disabled,
            "size": self.size,
            "multiple": self.multiple,
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
                f'    <label for="{self.select_id}" class="form-label">{self.label}{required_mark}</label>'
            )

        # Build select classes
        select_classes = ["form-select"]
        if self.size != "md":
            select_classes.append(f"form-select-{self.size}")
        if self.validation_state == "valid":
            select_classes.append("is-valid")
        elif self.validation_state == "invalid":
            select_classes.append("is-invalid")

        # Build select attributes
        attrs = [
            f'class="{" ".join(select_classes)}"',
            f'id="{self.select_id}"',
            f'name="{self.name}"',
        ]
        if self.required:
            attrs.append("required")
        if self.disabled:
            attrs.append("disabled")
        if self.multiple:
            attrs.append("multiple")

        parts.append(f"    <select {' '.join(attrs)}>")

        # Options
        for opt in self.options:
            opt_value = opt["value"]
            opt_label = opt["label"]
            selected = " selected" if opt_value == self.value else ""
            parts.append(f'        <option value="{opt_value}"{selected}>{opt_label}</option>')

        parts.append("    </select>")

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
