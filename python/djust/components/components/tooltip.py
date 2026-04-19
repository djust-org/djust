"""Tooltip component."""

import html
from djust import Component


class Tooltip(Component):
    """Tooltip component.

    Args:
        text: tooltip text
        content: wrapped content (pre-rendered HTML)
        position: top, bottom, left, right"""

    def __init__(
        self,
        text: str = "",
        content: str = "",
        position: str = "top",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            text=text,
            content=content,
            position=position,
            custom_class=custom_class,
            **kwargs,
        )
        self.text = text
        self.content = content
        self.position = position
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the tooltip HTML."""
        cls = f"dj-tooltip dj-tooltip--{html.escape(self.position)}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_text = html.escape(self.text)
        return (
            f'<span class="{cls}">'
            f"{self.content}"
            f'<span class="dj-tooltip__text">{e_text}</span>'
            f"</span>"
        )
