"""InputGroup component."""

import html
from djust import Component


class InputGroup(Component):
    """Input group wrapper component (prefix/suffix addons).

    Args:
        content: input and addons (pre-rendered HTML)
        size: sm, md, lg
        error: error message"""

    def __init__(
        self,
        content: str = "",
        size: str = "md",
        error: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            size=size,
            error=error,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.size = size
        self.error = error
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the inputgroup HTML."""
        size_cls = f" input-group-{html.escape(self.size)}" if self.size != "md" else ""
        error_cls = " input-group-error" if self.error else ""
        cls = f"input-group{size_cls}{error_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        error_html = (
            f'<span class="form-error-message">{html.escape(self.error)}</span>'
            if self.error
            else ""
        )
        return f'<div class="{cls}">{self.content}</div>{error_html}'
