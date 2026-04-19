"""AspectRatio component."""

import html
from djust import Component


class AspectRatio(Component):
    """Aspect ratio container component.

    Args:
        content: contained content (pre-rendered HTML)
        ratio: CSS aspect-ratio value (e.g. '16/9')"""

    def __init__(
        self,
        content: str = "",
        ratio: str = "16/9",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            ratio=ratio,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.ratio = ratio
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the aspectratio HTML."""
        cls = "dj-aspect-ratio"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_ratio = html.escape(self.ratio)
        return f'<div class="{cls}" style="aspect-ratio: {e_ratio};">{self.content}</div>'
