"""ContextMenu component."""

import html
from djust import Component


class ContextMenu(Component):
    """Context menu (right-click menu) component.

    Args:
        label: trigger area text
        content: menu items (pre-rendered HTML)"""

    def __init__(
        self,
        label: str = "Right-click area",
        content: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            label=label,
            content=content,
            custom_class=custom_class,
            **kwargs,
        )
        self.label = label
        self.content = content
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the contextmenu HTML."""
        cls = "ctx-wrapper"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_label = html.escape(self.label)
        return (
            f'<div class="{cls}">'
            f'<div class="ctx-trigger">{e_label}</div>'
            f'<div class="ctx-menu" role="menu">{self.content}</div>'
            f"</div>"
        )
