"""
TextArea component for djust.

Simple stateless textarea field (Bootstrap 5).

One renderer (#4002): ``_render_custom`` draws the markup the retired Rust ``RustTextArea``
drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``, with one fix: an
explicit ``id`` is the textarea's ``id`` (the Rust instance never received it and used ``name``).
"""

from typing import Optional

from ..base import Component

from djust.components.utils import attr_text

_STATE_CLASS = {"valid": " is-valid", "invalid": " is-invalid"}


class TextArea(Component):
    """
    Multi-line textarea component (Bootstrap 5).

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
        >>> textarea = TextArea(name="description", label="Description", rows=5, required=True)
        >>> textarea.render()
        '<div class="mb-3">...'
    """

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

        # Declared for type checkers; the base class already set each from the kwargs.
        self.name = name
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
        self.textarea_id = textarea_id

    def _render_custom(self) -> str:
        textarea_id = attr_text(self.id)
        html = '<div class="mb-3">'
        if self.label is not None:
            html += f'\n    <label for="{textarea_id}" class="form-label">{attr_text(self.label)}'
            if self.required:
                html += ' <span class="text-danger">*</span>'
            html += "</label>"
        state = self.validation_state
        state_class = _STATE_CLASS.get(str(state), "") if state is not None else ""
        html += (
            f'\n    <textarea class="form-control{state_class}"'
            f'\n              id="{textarea_id}"'
            f'\n              name="{attr_text(self.name)}"'
            f'\n              rows="{attr_text(self.rows)}"'
        )
        if self.placeholder is not None:
            html += f' placeholder="{attr_text(self.placeholder)}"'
        if self.required:
            html += " required"
        if self.disabled:
            html += " disabled"
        if self.readonly:
            html += " readonly"
        html += f">{attr_text(self.value)}</textarea>"
        if self.help_text is not None:
            html += f'\n    <div class="form-text">{attr_text(self.help_text)}</div>'
        if self.validation_message is not None:
            feedback = "valid-feedback" if state == "valid" else "invalid-feedback"
            html += f'\n    <div class="{feedback}">{attr_text(self.validation_message)}</div>'
        return html + "\n</div>"
