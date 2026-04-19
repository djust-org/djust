"""ScrollArea component."""

import html
from djust import Component


class ScrollArea(Component):
    """Scrollable area container component.

    Args:
        content: scrollable content (pre-rendered HTML)
        max_height: CSS max-height value
        label: accessible label"""

    def __init__(
        self,
        content: str = "",
        max_height: str = "400px",
        label: str = "Scrollable content",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            max_height=max_height,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.max_height = max_height
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the scrollarea HTML."""
        cls = "dj-scroll-area"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_label = html.escape(self.label)
        e_max = html.escape(self.max_height)
        return (
            f'<div class="{cls}" role="region" aria-label="{e_label}" '
            f'style="max-height: {e_max}; overflow-y: auto;">'
            f"{self.content}</div>"
        )
