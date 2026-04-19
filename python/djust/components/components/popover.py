"""Popover component."""

import html
from djust import Component


class Popover(Component):
    """Popover overlay component.

    Args:
        trigger: trigger button text
        content: popover body (pre-rendered HTML)
        title: optional popover title
        placement: bottom, top, left, right"""

    def __init__(
        self,
        trigger: str = "Click me",
        content: str = "",
        title: str = "",
        placement: str = "bottom",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            trigger=trigger,
            content=content,
            title=title,
            placement=placement,
            custom_class=custom_class,
            **kwargs,
        )
        self.trigger = trigger
        self.content = content
        self.title = title
        self.placement = placement
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the popover HTML."""
        cls = "popover-wrapper"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_trigger = html.escape(self.trigger)
        e_placement = html.escape(self.placement)
        title_html = (
            f'<div class="popover-title">{html.escape(self.title)}</div>' if self.title else ""
        )
        return (
            f'<div class="{cls}">'
            f'<button class="popover-trigger">{e_trigger}</button>'
            f'<div class="popover popover-{e_placement}">'
            f"{title_html}"
            f'<div class="popover-content">{self.content}</div>'
            f"</div></div>"
        )
