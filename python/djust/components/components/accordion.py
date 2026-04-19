"""Accordion component."""

import html

from djust import Component


class Accordion(Component):
    """Accordion/collapsible sections component.

    Args:
        items: list of dicts with keys: id, title, content
        active: id of currently open item
        event: dj-click event name"""

    def __init__(
        self,
        items: list = None,
        active: str = "",
        event: str = "accordion_toggle",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            active=active,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.active = active
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the accordion HTML."""
        items = self.items or []
        cls = "dj-accordion"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_event = html.escape(self.event)
        parts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            iid = html.escape(str(item.get("id", "")))
            title = html.escape(str(item.get("title", "")))
            content = item.get("content", "")
            is_open = str(item.get("id", "")) == self.active
            open_cls = " dj-accordion-item--open" if is_open else ""
            content_html = f'<div class="dj-accordion__content">{content}</div>' if is_open else ""
            parts.append(
                f'<div class="dj-accordion-item{open_cls}">'
                f'<button class="dj-accordion__trigger" dj-click="{e_event}" data-value="{iid}">'
                f"<span>{title}</span>"
                f'<span class="dj-accordion__chevron">&#9662;</span>'
                f"</button>"
                f"{content_html}</div>"
            )
        return f'<div class="{cls}">{"".join(parts)}</div>'
