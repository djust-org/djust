"""Sheet component."""

import html
from djust import Component


class Sheet(Component):
    """Sheet/drawer overlay component.

    Args:
        content: sheet body (pre-rendered HTML)
        title: optional header title
        side: left, right
        is_open: whether sheet is open
        close_event: dj-click event name"""

    def __init__(
        self,
        content: str = "",
        title: str = "",
        side: str = "right",
        is_open: bool = False,
        close_event: str = "close_sheet",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            title=title,
            side=side,
            is_open=is_open,
            close_event=close_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.title = title
        self.side = side
        self.is_open = is_open
        self.close_event = close_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the sheet HTML."""
        e_side = html.escape(self.side)
        e_title = html.escape(self.title)
        e_close = html.escape(self.close_event)
        open_attr = ' data-open="true"' if self.is_open else ""
        cls = f"sheet sheet-{e_side}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        title_html = (
            f'<div class="sheet-header">'
            f'<h3 class="sheet-title">{e_title}</h3>'
            f'<button class="sheet-close" dj-click="{e_close}">&times;</button>'
            f"</div>"
            if self.title
            else f'<div class="sheet-header-close">'
            f'<button class="sheet-close" dj-click="{e_close}">&times;</button>'
            f"</div>"
        )
        return (
            f'<div class="sheet-overlay" dj-click="{e_close}"{open_attr}></div>'
            f'<div class="{cls}"{open_attr}>'
            f"{title_html}"
            f'<div class="sheet-body">{self.content}</div>'
            f"</div>"
        )
