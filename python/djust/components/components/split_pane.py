"""SplitPane component."""

import html
from djust import Component


class SplitPane(Component):
    """Split pane/resizable layout component.

    Args:
        left: left/top pane content (pre-rendered HTML)
        right: right/bottom pane content (pre-rendered HTML)
        direction: horizontal, vertical
        initial: initial split percentage"""

    def __init__(
        self,
        left: str = "",
        right: str = "",
        direction: str = "horizontal",
        initial: int = 50,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            left=left,
            right=right,
            direction=direction,
            initial=initial,
            custom_class=custom_class,
            **kwargs,
        )
        self.left = left
        self.right = right
        self.direction = direction
        self.initial = initial
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the splitpane HTML."""
        e_dir = html.escape(self.direction)
        cls = f"split-pane split-pane-{e_dir}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        size_prop = "width" if self.direction == "horizontal" else "height"
        return (
            f'<div class="{cls}">'
            f'<div class="sp-pane sp-pane-1" style="{size_prop}:{self.initial}%">{self.left}</div>'
            f'<div class="sp-handle sp-handle-{e_dir}"></div>'
            f'<div class="sp-pane sp-pane-2" style="flex:1">{self.right}</div>'
            f"</div>"
        )
