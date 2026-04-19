"""Dropdown menu component with structured items and keyboard navigation."""

import html

from djust import Component


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
        items: List of menu item dicts
        open: Whether the menu is expanded
        toggle_event: djust event for toggling open/close
        align: Menu alignment (left, right)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        label: str = "Menu",
        items: list = None,
        open: bool = False,
        toggle_event: str = "toggle_menu",
        align: str = "left",
        custom_class: str = "",
        **kwargs,
    ):
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
        e_toggle = html.escape(self.toggle_event)

        trigger = (
            f'<button class="dj-dropdown-menu__trigger" '
            f'dj-click="{e_toggle}" '
            f'aria-expanded="{"true" if self.open else "false"}" '
            f'aria-haspopup="true">{e_label}</button>'
        )

        if not self.open:
            return f'<div class="{class_str}">{trigger}</div>'

        menu_items = []
        for item in self.items:
            if item.get("divider"):
                menu_items.append('<hr class="dj-dropdown-menu__divider" role="separator">')
                continue

            item_cls = "dj-dropdown-menu__item"
            if item.get("danger"):
                item_cls += " dj-dropdown-menu__item--danger"
            if item.get("disabled"):
                item_cls += " dj-dropdown-menu__item--disabled"

            e_item_label = html.escape(str(item.get("label", "")))
            e_event = html.escape(str(item.get("event", "")))

            disabled_attr = " disabled" if item.get("disabled") else ""
            event_attr = f' dj-click="{e_event}"' if e_event else ""

            icon_html = ""
            if item.get("icon"):
                icon_html = (
                    f'<span class="dj-dropdown-menu__icon">{html.escape(str(item["icon"]))}</span>'
                )

            menu_items.append(
                f'<button class="{item_cls}" role="menuitem"'
                f"{event_attr}{disabled_attr}>"
                f"{icon_html}{e_item_label}</button>"
            )

        menu = (
            f'<div class="dj-dropdown-menu__content dj-dropdown-menu--{self.align}" '
            f'role="menu">{"".join(menu_items)}</div>'
        )

        return f'<div class="{class_str}">{trigger}{menu}</div>'
