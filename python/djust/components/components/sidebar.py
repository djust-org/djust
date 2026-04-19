"""Sidebar component."""

import html

from djust import Component


class Sidebar(Component):
    """Sidebar navigation component.

    Args:
        items: list of dicts with keys: label, href, icon, active (bool)
        title: sidebar title
        collapsed: whether sidebar is collapsed
        content: pre-rendered HTML content (alternative to items)"""

    def __init__(
        self,
        items: list = None,
        title: str = "",
        collapsed: bool = False,
        content: str = "",
        toggle_event: str = "toggle_sidebar",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            title=title,
            collapsed=collapsed,
            content=content,
            toggle_event=toggle_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.title = title
        self.collapsed = collapsed
        self.content = content
        self.toggle_event = toggle_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the sidebar HTML."""
        collapsed_cls = " dj-sidebar--collapsed" if self.collapsed else ""
        cls = f"dj-sidebar{collapsed_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_toggle = html.escape(self.toggle_event)
        header_html = ""
        if self.title:
            e_title = html.escape(self.title)
            header_html = (
                f'<div class="dj-sidebar__header">'
                f'<span class="dj-sidebar__title">{e_title}</span>'
                f'<button class="dj-sidebar__toggle" dj-click="{e_toggle}">&#9776;</button>'
                f"</div>"
            )
        if self.items:
            items_html = ""
            for item in self.items:
                if not isinstance(item, dict):
                    continue
                label = html.escape(str(item.get("label", "")))
                href = html.escape(str(item.get("href", "#")))
                icon = item.get("icon", "")
                active = item.get("active", False)
                active_cls = " dj-sidebar__item--active" if active else ""
                icon_html = (
                    f'<span class="dj-sidebar__icon">{html.escape(str(icon))}</span>'
                    if icon
                    else ""
                )
                items_html += (
                    f'<li class="dj-sidebar__item">'
                    f'<a class="dj-sidebar__link{active_cls}" href="{href}">'
                    f'{icon_html}<span class="dj-sidebar__label">{label}</span>'
                    f"</a></li>"
                )
            menu_html = f'<ul class="dj-sidebar__menu">{items_html}</ul>'
        else:
            menu_html = self.content
        return f'<nav class="{cls}" role="navigation">{header_html}{menu_html}</nav>'
