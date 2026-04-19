"""CommandPalette component."""

import html
from djust import Component


class CommandPalette(Component):
    """Command palette/search overlay component.

    Args:
        content: results content (pre-rendered HTML)
        is_open: whether palette is open
        search_event: dj-input event for search
        close_event: dj-click event to close
        placeholder: search input placeholder"""

    def __init__(
        self,
        content: str = "",
        is_open: bool = False,
        search_event: str = "palette_search",
        close_event: str = "close_palette",
        placeholder: str = "Search commands...",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            is_open=is_open,
            search_event=search_event,
            close_event=close_event,
            placeholder=placeholder,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.is_open = is_open
        self.search_event = search_event
        self.close_event = close_event
        self.placeholder = placeholder
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the commandpalette HTML."""
        e_search = html.escape(self.search_event)
        e_close = html.escape(self.close_event)
        e_placeholder = html.escape(self.placeholder)
        open_attr = ' data-open="true"' if self.is_open else ""
        cls = "palette"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        return (
            f'<div class="palette-overlay" dj-click="{e_close}"{open_attr}></div>'
            f'<div class="{cls}"{open_attr}>'
            f'<div class="palette-search">'
            f'<input class="palette-input" type="text" placeholder="{e_placeholder}" '
            f'dj-input="{e_search}">'
            f'<button class="palette-close" dj-click="{e_close}">Esc</button>'
            f"</div>"
            f'<div class="palette-results">{self.content}</div>'
            f"</div>"
        )
