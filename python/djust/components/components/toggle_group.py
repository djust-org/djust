"""ToggleGroup component."""

import html

from djust import Component


class ToggleGroup(Component):
    """Segmented toggle button group component.

    Args:
        name: group name
        options: list of dicts with keys: value, label
        value: currently selected value
        event: dj-click event name
        size: sm, md, lg"""

    def __init__(
        self,
        name: str = "",
        options: list = None,
        value: str = "",
        event: str = "toggle_select",
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            options=options,
            value=value,
            event=event,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.options = options or []
        self.value = value
        self.event = event
        self.size = size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the togglegroup HTML."""
        options = self.options or []
        size_cls = f" toggle-group-{html.escape(self.size)}" if self.size != "md" else ""
        cls = f"toggle-group{size_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_event = html.escape(self.event)
        buttons = []
        for opt in options:
            if not isinstance(opt, dict):
                continue
            ov = html.escape(str(opt.get("value", "")))
            ol = html.escape(str(opt.get("label", "")))
            is_active = str(opt.get("value", "")) == str(self.value)
            active_cls = " toggle-group-btn--active" if is_active else ""
            buttons.append(
                f'<button class="toggle-group-btn{active_cls}" '
                f'dj-click="{e_event}" data-value="{ov}">'
                f'<span class="toggle-group-label">{ol}</span></button>'
            )
        return f'<div class="{cls}" role="group">{"".join(buttons)}</div>'
