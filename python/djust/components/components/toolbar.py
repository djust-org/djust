"""Toolbar component."""

import html
from djust import Component


class Toolbar(Component):
    """Toolbar component with button groups.

    Args:
        content: toolbar buttons/controls (pre-rendered HTML)
        variant: default, compact
        align: left, center, right"""

    def __init__(
        self,
        content: str = "",
        variant: str = "default",
        align: str = "left",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            variant=variant,
            align=align,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.variant = variant
        self.align = align
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the toolbar HTML."""
        cls = f"dj-toolbar dj-toolbar--{html.escape(self.align)}"
        if self.variant != "default":
            cls += f" dj-toolbar--{html.escape(self.variant)}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        return f'<div class="{cls}" role="toolbar">{self.content}</div>'
