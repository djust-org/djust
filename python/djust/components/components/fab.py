"""Fab component."""

import html
from djust import Component


class Fab(Component):
    """Floating action button component.

    Args:
        icon: icon text/emoji
        event: dj-click event name
        position: bottom-right, bottom-left, top-right, top-left
        label: accessible label
        size: sm, md, lg
        variant: primary, secondary, danger, success"""

    def __init__(
        self,
        icon: str = "+",
        event: str = "",
        position: str = "bottom-right",
        label: str = "",
        size: str = "md",
        variant: str = "primary",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            icon=icon,
            event=event,
            position=position,
            label=label,
            size=size,
            variant=variant,
            custom_class=custom_class,
            **kwargs,
        )
        self.icon = icon
        self.event = event
        self.position = position
        self.label = label
        self.size = size
        self.variant = variant
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the fab HTML."""
        valid_positions = ("bottom-right", "bottom-left", "top-right", "top-left")
        pos_cls = self.position if self.position in valid_positions else "bottom-right"
        size_cls = f" fab-{html.escape(self.size)}" if self.size != "md" else ""
        variant_cls = f" fab-{html.escape(self.variant)}"
        cls = f"fab{size_cls}{variant_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        click_attr = f' dj-click="{html.escape(self.event)}"' if self.event else ""
        aria_label = f' aria-label="{html.escape(self.label)}"' if self.label else ""
        e_icon = html.escape(self.icon)
        return (
            f'<div class="fab-container fab-{html.escape(pos_cls)}">'
            f'<button class="{cls}"{click_attr}{aria_label}>'
            f'<span class="fab-icon">{e_icon}</span>'
            f"</button></div>"
        )
