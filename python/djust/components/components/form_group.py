"""FormGroup component."""

import html
from djust import Component


class FormGroup(Component):
    """Form group wrapper component.

    Args:
        content: form field content (pre-rendered HTML)
        label: label text
        error: error message
        helper: helper text
        required: whether field is required
        for_input: id of the associated input"""

    def __init__(
        self,
        content: str = "",
        label: str = "",
        error: str = "",
        helper: str = "",
        required: bool = False,
        for_input: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            label=label,
            error=error,
            helper=helper,
            required=required,
            for_input=for_input,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.label = label
        self.error = error
        self.helper = helper
        self.required = required
        self.for_input = for_input
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the formgroup HTML."""
        cls = "form-group"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        for_attr = f' for="{html.escape(self.for_input)}"' if self.for_input else ""
        required_html = '<span class="form-required"> *</span>' if self.required else ""
        label_html = (
            f'<label class="form-label"{for_attr}>{html.escape(self.label)}{required_html}</label>'
            if self.label
            else ""
        )
        error_html = (
            f'<span class="form-error-message">{html.escape(self.error)}</span>'
            if self.error
            else ""
        )
        helper_html = (
            f'<span class="form-helper">{html.escape(self.helper)}</span>' if self.helper else ""
        )
        return f'<div class="{cls}">{label_html}{self.content}{error_html}{helper_html}</div>'
