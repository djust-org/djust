"""Bottom sheet component for mobile-optimized drawers."""

import html

from djust import Component


class BottomSheet(Component):
    """Style-agnostic bottom sheet / drawer component.

    Mobile-optimized drawer from bottom with drag handle.

    Usage in a LiveView::

        self.sheet = BottomSheet(
            title="Filters",
            open=True,
            close_event="close_sheet",
        )

    In template::

        {{ sheet|safe }}

    Args:
        title: Sheet title text
        open: Whether the sheet is visible
        close_event: djust event for closing
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        title: str = "",
        open: bool = False,
        close_event: str = "close_sheet",
        custom_class: str = "",
        content: str = "",
        **kwargs,
    ):
        super().__init__(
            title=title,
            open=open,
            close_event=close_event,
            custom_class=custom_class,
            content=content,
            **kwargs,
        )
        self.title = title
        self.open = open
        self.close_event = close_event
        self.custom_class = custom_class
        self.content = content

    def _render_custom(self) -> str:
        if not self.open:
            return ""

        classes = ["dj-bottom-sheet"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_close = html.escape(self.close_event)
        e_title = html.escape(self.title)
        e_content = html.escape(self.content) if self.content else ""

        title_html = ""
        if self.title:
            title_html = f'<h3 class="dj-bottom-sheet__title">{e_title}</h3>'

        return (
            f'<div class="dj-bottom-sheet__backdrop" dj-click="{e_close}">'
            f'<div class="{class_str}" onclick="event.stopPropagation()">'
            f'<div class="dj-bottom-sheet__handle"><div class="dj-bottom-sheet__handle-bar"></div></div>'
            f'<div class="dj-bottom-sheet__header">'
            f"{title_html}"
            f'<button class="dj-bottom-sheet__close" dj-click="{e_close}">&times;</button>'
            f"</div>"
            f'<div class="dj-bottom-sheet__body">{e_content}</div>'
            f"</div></div>"
        )
