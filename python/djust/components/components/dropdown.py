"""Dropdown component."""

import html
from djust import Component


class Dropdown(Component):
    """Dropdown menu component.

    Args:
        label: trigger button text
        content: pre-rendered HTML for dropdown menu (caller's responsibility)
        is_open: whether dropdown is open
        toggle_event: dj-click event name
        variant: style variant"""

    def __init__(
        self,
        label: str = "Menu",
        content: str = "",
        is_open: bool = False,
        toggle_event: str = "toggle_dropdown",
        variant: str = "default",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            label=label,
            content=content,
            is_open=is_open,
            toggle_event=toggle_event,
            variant=variant,
            custom_class=custom_class,
            **kwargs,
        )
        self.label = label
        self.content = content
        self.is_open = is_open
        self.toggle_event = toggle_event
        self.variant = variant
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the dropdown HTML."""
        cls = f"dj-dropdown dj-dropdown--{html.escape(self.variant)}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_label = html.escape(self.label)
        e_toggle = html.escape(self.toggle_event)
        menu_html = f'<div class="dj-dropdown__menu">{self.content}</div>' if self.is_open else ""
        open_attr = " data-open" if self.is_open else ""
        return (
            f'<div class="{cls}"{open_attr}>'
            f'<button class="dj-dropdown__trigger" dj-click="{e_toggle}">{e_label}</button>'
            f"{menu_html}</div>"
        )
