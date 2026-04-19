"""Reactions component for Slack-style emoji reactions with live counts."""

import html
from typing import Dict, List, Optional

from djust import Component


class Reactions(Component):
    """Slack-style emoji reactions with live-updating counts.

    Renders a row of emoji buttons with count badges. Clicking
    fires a djust event with the emoji identifier.

    Usage in a LiveView::

        self.reactions = Reactions(
            options=["👍", "❤️", "🎉", "🚀"],
            counts={"👍": 5, "❤️": 2, "🎉": 0, "🚀": 1},
            event="react",
            active=["👍"],
        )

    In template::

        {{ reactions|safe }}

    CSS Custom Properties::

        --dj-reactions-gap: space between buttons (default: 0.375rem)
        --dj-reactions-btn-bg: button background (default: #f3f4f6)
        --dj-reactions-btn-active-bg: active button background (default: #dbeafe)
        --dj-reactions-btn-radius: button border radius (default: 9999px)
        --dj-reactions-count-color: count text color (default: #6b7280)

    Args:
        options: List of emoji strings to display.
        counts: Dict mapping emoji to count.
        event: djust event to fire on click.
        active: List of emojis the current user has selected.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        options: Optional[List[str]] = None,
        counts: Optional[Dict[str, int]] = None,
        event: str = "react",
        active: Optional[List[str]] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            options=options,
            counts=counts,
            event=event,
            active=active,
            custom_class=custom_class,
            **kwargs,
        )
        self.options = options or []
        self.counts = counts or {}
        self.event = event
        self.active = active or []
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-reactions"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_event = html.escape(str(self.event))

        buttons = []
        for emoji in self.options:
            e_emoji = html.escape(str(emoji))
            count = 0
            try:
                count = int(self.counts.get(str(emoji), 0))
            except (ValueError, TypeError):
                count = 0

            is_active = str(emoji) in self.active
            btn_cls = "dj-reactions__btn"
            if is_active:
                btn_cls += " dj-reactions__btn--active"

            aria_pressed = "true" if is_active else "false"

            count_html = ""
            if count > 0:
                count_html = f'<span class="dj-reactions__count">{count}</span>'

            buttons.append(
                f'<button type="button" class="{btn_cls}" '
                f'dj-click="{e_event}" dj-value-emoji="{e_emoji}" '
                f'aria-pressed="{aria_pressed}" '
                f'aria-label="{e_emoji} {count}">'
                f'<span class="dj-reactions__emoji">{e_emoji}</span>'
                f"{count_html}"
                f"</button>"
            )

        return f'<div class="{cls}" role="group" aria-label="Reactions">{"".join(buttons)}</div>'
