"""SplitButton component."""

import html

from djust import Component


class SplitButton(Component):
    """Split button with primary action and dropdown menu.

    Args:
        label: primary button text
        event: dj-click event for primary action
        options: list of dicts with keys: label, event
        variant: primary, secondary, danger, success
        size: sm, md, lg"""

    def __init__(
        self,
        label: str = "",
        event: str = "",
        options: list = None,
        variant: str = "primary",
        size: str = "md",
        is_open: bool = False,
        toggle_event: str = "toggle_split_menu",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            label=label,
            event=event,
            options=options,
            variant=variant,
            size=size,
            is_open=is_open,
            toggle_event=toggle_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.label = label
        self.event = event
        self.options = options or []
        self.variant = variant
        self.size = size
        self.is_open = is_open
        self.toggle_event = toggle_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the splitbutton HTML."""
        options = self.options or []
        e_label = html.escape(self.label)
        e_event = html.escape(self.event)
        e_toggle = html.escape(self.toggle_event)
        variant_cls = f" split-btn-{html.escape(self.variant)}"
        size_cls = f" split-btn-{html.escape(self.size)}" if self.size != "md" else ""
        cls = f"split-btn{variant_cls}{size_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        click_attr = f' dj-click="{e_event}"' if self.event else ""
        option_items = []
        for opt in options:
            if not isinstance(opt, dict):
                continue
            ol = html.escape(str(opt.get("label", "")))
            oe = html.escape(str(opt.get("event", "")))
            opt_click = f' dj-click="{oe}"' if oe else ""
            option_items.append(f'<button class="split-btn-option"{opt_click}>{ol}</button>')
        open_data = "true" if self.is_open else "false"
        menu_html = ""
        if option_items:
            menu_html = (
                f'<div class="split-btn-menu" data-open="{open_data}">{"".join(option_items)}</div>'
            )
        return (
            f'<div class="{cls}">'
            f'<button class="split-btn-primary"{click_attr}>{e_label}</button>'
            f'<button class="split-btn-toggle" dj-click="{e_toggle}">'
            f'<span class="split-btn-caret">&#9662;</span></button>'
            f"{menu_html}"
            f"</div>"
        )
