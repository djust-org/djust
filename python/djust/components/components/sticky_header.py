"""StickyHeader component."""

import html
from django.utils.html import conditional_escape
from djust import Component
from typing import Any
from djust.components.utils import safe_css_length, safe_css_number


class StickyHeader(Component):
    """Sticky header component.

    Args:
        content: header content (pre-rendered HTML)
        offset: CSS top offset
        z_index: CSS z-index value"""

    def __init__(
        self,
        content: str = "",
        offset: str = "0",
        z_index: str = "10",
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            content=content,
            offset=offset,
            z_index=z_index,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.offset = offset
        self.z_index = z_index
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the stickyheader HTML."""
        cls = "dj-sticky-header"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_offset = html.escape(safe_css_length(self.offset, "0"))
        e_z = html.escape(safe_css_number(self.z_index, "10", integer=True))
        return (
            f'<div class="{cls}" style="position: sticky; top: {e_offset}; z-index: {e_z};">'
            f"{conditional_escape(self.content)}</div>"
        )
