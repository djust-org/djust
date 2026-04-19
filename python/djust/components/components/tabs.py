"""Tabs component."""

import html

from djust import Component


class Tabs(Component):
    """Tab navigation component.

    Args:
        tabs: list of dicts with keys: id, label
        active: id of active tab
        content: pre-rendered HTML for active pane (caller's responsibility)
        event: dj-click event name"""

    def __init__(
        self,
        tabs: list = None,
        active: str = "",
        content: str = "",
        event: str = "set_tab",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            tabs=tabs,
            active=active,
            content=content,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.tabs = tabs or []
        self.active = active
        self.content = content
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the tabs HTML."""
        tabs = self.tabs or []
        cls = "dj-tabs"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_event = html.escape(self.event)
        nav_items = []
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            tid = html.escape(str(tab.get("id", "")))
            label = html.escape(str(tab.get("label", "")))
            active_cls = " dj-tab--active" if str(tab.get("id", "")) == self.active else ""
            nav_items.append(
                f'<button class="dj-tab{active_cls}" '
                f'dj-click="{e_event}" data-value="{tid}">{label}</button>'
            )
        nav = f'<nav class="dj-tabs__nav">{"".join(nav_items)}</nav>'
        pane = f'<div class="dj-tabs__pane">{self.content}</div>' if self.content else ""
        return f'<div class="{cls}">{nav}{pane}</div>'
