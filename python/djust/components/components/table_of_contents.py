"""TableOfContents component."""

import html

from djust import Component


class TableOfContents(Component):
    """Table of contents navigation component.

    Args:
        items: list of dicts with keys: id, label, level
        title: TOC heading
        active: currently active section id
        event: dj-click event name"""

    def __init__(
        self,
        items: list = None,
        title: str = "Contents",
        active: str = "",
        event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            title=title,
            active=active,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.title = title
        self.active = active
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the tableofcontents HTML."""
        if not self.items:
            return ""
        cls = "toc"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_title = html.escape(self.title)
        e_event = html.escape(self.event) if self.event else ""
        items_html = ""
        for item in self.items:
            if not isinstance(item, dict):
                continue
            iid = html.escape(str(item.get("id", "")))
            lbl = html.escape(str(item.get("label", "")))
            level = int(item.get("level", 1))
            active_cls = " toc-item-active" if str(item.get("id", "")) == self.active else ""
            event_attr = f' dj-click="{e_event}" data-value="{iid}"' if e_event else ""
            items_html += f'<a href="#{iid}" class="toc-item toc-level-{level}{active_cls}"{event_attr}>{lbl}</a>'
        title_html = f'<div class="toc-title">{e_title}</div>' if self.title else ""
        return f'<nav class="{cls}">{title_html}<div class="toc-list">{items_html}</div></nav>'
