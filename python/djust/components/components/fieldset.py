"""Fieldset component."""

import html
from djust import Component


class Fieldset(Component):
    """Fieldset with legend component.

    Args:
        content: fieldset content (pre-rendered HTML)
        legend: legend text
        disabled: whether fieldset is disabled"""

    def __init__(
        self,
        content: str = "",
        legend: str = "",
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            legend=legend,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.legend = legend
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the fieldset HTML."""
        cls = "fieldset"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        disabled_attr = " disabled" if self.disabled else ""
        legend_html = (
            f'<legend class="fieldset-legend">{html.escape(self.legend)}</legend>'
            if self.legend
            else ""
        )
        return (
            f'<fieldset class="{cls}"{disabled_attr}>'
            f"{legend_html}"
            f'<div class="fieldset-content">{self.content}</div>'
            f"</fieldset>"
        )
