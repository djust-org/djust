"""Collapsible component."""

import html
from djust import Component


class Collapsible(Component):
    """Collapsible/expandable section component.

    Args:
        trigger: trigger button text
        content: collapsible body (pre-rendered HTML)
        is_open: whether section is open
        event: dj-click event name"""

    def __init__(
        self,
        trigger: str = "Toggle",
        content: str = "",
        is_open: bool = False,
        event: str = "toggle_collapsible",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            trigger=trigger,
            content=content,
            is_open=is_open,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.trigger = trigger
        self.content = content
        self.is_open = is_open
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the collapsible HTML."""
        open_cls = " collapsible-open" if self.is_open else ""
        cls = f"collapsible{open_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_trigger = html.escape(self.trigger)
        e_event = html.escape(self.event)
        return (
            f'<div class="{cls}">'
            f'<button class="collapsible-trigger" dj-click="{e_event}">'
            f'<span class="collapsible-label">{e_trigger}</span>'
            f'<span class="collapsible-icon">&#9662;</span>'
            f"</button>"
            f'<div class="collapsible-content">{self.content}</div>'
            f"</div>"
        )
