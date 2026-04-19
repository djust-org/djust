"""Hover Card component for rich content on hover (GitHub-style user cards)."""

import html

from djust import Component


class HoverCard(Component):
    """Style-agnostic hover card using CSS custom properties.

    Displays rich content in a floating card when hovering a trigger element.
    Similar to GitHub's user hovercards.

    Usage in a LiveView::

        self.user_card = HoverCard(
            trigger="@alice",
            content="<strong>Alice</strong><br>Senior Engineer",
            position="bottom",
        )

    In template::

        {{ user_card|safe }}

    CSS Custom Properties::

        --dj-hover-card-bg: background (default: white)
        --dj-hover-card-border: border color (default: #e5e7eb)
        --dj-hover-card-shadow: box-shadow (default: 0 4px 12px rgba(0,0,0,0.1))
        --dj-hover-card-radius: border-radius (default: 0.5rem)
        --dj-hover-card-padding: content padding (default: 0.75rem 1rem)
        --dj-hover-card-max-width: max width (default: 20rem)

    Args:
        trigger: Text or HTML for the trigger element.
        content: HTML content for the card body.
        position: Placement relative to trigger (top, bottom, left, right).
        delay_in: Hover delay before showing (ms, default 200).
        delay_out: Delay before hiding after mouse leaves (ms, default 300).
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        trigger: str = "",
        content: str = "",
        position: str = "bottom",
        delay_in: int = 200,
        delay_out: int = 300,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            trigger=trigger,
            content=content,
            position=position,
            delay_in=delay_in,
            delay_out=delay_out,
            custom_class=custom_class,
            **kwargs,
        )
        self.trigger = trigger
        self.content = content
        self.position = position
        self.delay_in = delay_in
        self.delay_out = delay_out
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the hover card HTML."""
        e_trigger = html.escape(str(self.trigger))
        e_position = html.escape(str(self.position))

        classes = [
            "dj-hover-card",
            f"dj-hover-card--{e_position}",
        ]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        return (
            f'<span class="{" ".join(classes)}" '
            f'data-delay-in="{int(self.delay_in)}" '
            f'data-delay-out="{int(self.delay_out)}">'
            f'<span class="dj-hover-card__trigger">{e_trigger}</span>'
            f'<div class="dj-hover-card__content">{self.content}</div>'
            f"</span>"
        )
