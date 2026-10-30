"""Dropdown menu component with structured items and keyboard navigation."""

import html

from django.utils.html import conditional_escape
from djust import Component
from typing import Any, Optional


class DropdownMenu(Component):
    """Style-agnostic dropdown menu component.

    Structured menu with keyboard navigation support.

    Usage in a LiveView::

        self.menu = DropdownMenu(
            label="Actions",
            items=[
                {"label": "Edit", "event": "edit_item"},
                {"divider": True},
                {"label": "Delete", "event": "delete_item", "danger": True},
            ],
            open=True,
        )

    In template::

        {{ menu|safe }}

    Args:
        label: Trigger button label
        items: List of menu item dicts (see ``render_menu_item`` for the keys:
            links, checkbox/radio items, headings, dividers)
        open: Whether the menu is expanded
        toggle_event: djust event for toggling open/close
        align: Menu alignment (left, right)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        label: str = "Menu",
        items: Optional[list] = None,
        open: bool = False,
        toggle_event: str = "toggle_menu",
        align: str = "left",
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            label=label,
            items=items,
            open=open,
            toggle_event=toggle_event,
            align=align,
            custom_class=custom_class,
            **kwargs,
        )
        self.label = label
        self.items = items or []
        self.open = open
        self.toggle_event = toggle_event
        self.align = align
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-dropdown-menu"]
        if self.open:
            classes.append("dj-dropdown-menu--open")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_label = html.escape(self.label)

        trigger = (
            f'<button type="button" class="dj-dropdown-menu__trigger" '
            f"{self.event_attrs(self.toggle_event)} "
            f'aria-expanded="{"true" if self.open else "false"}" '
            f'aria-haspopup="menu">{e_label}</button>'
        )

        if not self.open:
            return f'<div class="{class_str}">{trigger}</div>'

        from djust.components.templatetags.djust_components import render_menu_item

        def _event_attrs(event: str, value: Any) -> str:
            return self.event_attrs(event, value=value)

        menu_items = [render_menu_item(item, event_attrs=_event_attrs) for item in self.items]

        menu = (
            f'<div class="dj-dropdown-menu__content dj-dropdown-menu--{conditional_escape(self.align)}" '
            f'role="menu">{"".join(menu_items)}</div>'
        )

        return f'<div class="{class_str}">{trigger}{menu}</div>'
