"""Modal component."""

import html
from djust import Component


class Modal(Component):
    """Modal dialog overlay component."""

    def __init__(
        self,
        title: str = "",
        content: str = "",
        is_open: bool = False,
        size: str = "md",
        close_event: str = "close_modal",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            title=title,
            content=content,
            is_open=is_open,
            size=size,
            close_event=close_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.title = title
        self.content = content
        self.is_open = is_open
        self.size = size
        self.close_event = close_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the modal HTML."""
        if not self.is_open:
            return ""
        size_map = {
            "sm": "dj-modal--sm",
            "md": "dj-modal--md",
            "lg": "dj-modal--lg",
            "xl": "dj-modal--xl",
        }
        size_cls = size_map.get(self.size, "dj-modal--md")
        cls = f"dj-modal {size_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_title = html.escape(self.title)
        e_close = html.escape(self.close_event)
        return (
            f'<div class="dj-modal-backdrop" dj-click="{e_close}">'
            f'<div class="{cls}">'
            f'<div class="dj-modal__header">'
            f'<h3 class="dj-modal__title">{e_title}</h3>'
            f'<button class="dj-modal__close" dj-click="{e_close}">&times;</button>'
            f"</div>"
            f'<div class="dj-modal__body">{self.content}</div>'
            f"</div>"
            f"</div>"
        )
