"""Feedback widget component for rating responses."""

import html
from typing import Optional

from djust import Component


class FeedbackWidget(Component):
    """Thumbs up/down, star rating, or emoji feedback widget.

    Provides quick user feedback for AI responses with three mode options:
    thumbs (up/down), stars (1-5), or emoji (set of reaction emojis).

    Usage in a LiveView::

        # Thumbs mode (default)
        self.feedback = FeedbackWidget(event="rate_response", mode="thumbs")

        # Star rating
        self.feedback = FeedbackWidget(event="rate_response", mode="stars")

        # Emoji reactions
        self.feedback = FeedbackWidget(event="rate_response", mode="emoji")

    In template::

        {{ feedback|safe }}

    CSS Custom Properties::

        --dj-feedback-color: default icon color
        --dj-feedback-active-color: selected/active icon color
        --dj-feedback-hover-color: hover icon color
        --dj-feedback-gap: gap between buttons (default: 0.25rem)

    Args:
        event: djust event fired with rating value
        mode: Rating mode (thumbs, stars, emoji)
        value: Current selected value (for thumbs: "up"/"down", stars: 1-5, emoji: the emoji)
        custom_class: Additional CSS classes
    """

    VALID_MODES = {"thumbs", "stars", "emoji"}

    def __init__(
        self,
        event: str = "rate_response",
        mode: str = "thumbs",
        value: Optional[str] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            event=event,
            mode=mode,
            value=value,
            custom_class=custom_class,
            **kwargs,
        )
        self.event = event
        self.mode = mode if mode in self.VALID_MODES else "thumbs"
        self.value = value
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = f"dj-feedback dj-feedback--{self.mode}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_event = html.escape(self.event)

        if self.mode == "thumbs":
            buttons = self._render_thumbs(e_event)
        elif self.mode == "stars":
            buttons = self._render_stars(e_event)
        else:
            buttons = self._render_emoji(e_event)

        return f'<div class="{cls}" role="group" aria-label="Feedback">{buttons}</div>'

    def _render_thumbs(self, e_event: str) -> str:
        up_cls = "dj-feedback__btn--active" if self.value == "up" else ""
        down_cls = "dj-feedback__btn--active" if self.value == "down" else ""
        return (
            f'<button class="dj-feedback__btn {up_cls}" '
            f'dj-click="{e_event}" data-value="up" aria-label="Thumbs up">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" width="18" height="18">'
            f'<path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z"/>'
            f'<path d="M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/>'
            f"</svg></button>"
            f'<button class="dj-feedback__btn {down_cls}" '
            f'dj-click="{e_event}" data-value="down" aria-label="Thumbs down">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" width="18" height="18">'
            f'<path d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z"/>'
            f'<path d="M17 2h2.67A2.31 2.31 0 0122 4v7a2.31 2.31 0 01-2.33 2H17"/>'
            f"</svg></button>"
        )

    def _render_stars(self, e_event: str) -> str:
        parts = []
        current = int(self.value) if self.value and self.value.isdigit() else 0
        for i in range(1, 6):
            active = "dj-feedback__star--active" if i <= current else ""
            parts.append(
                f'<button class="dj-feedback__btn dj-feedback__star {active}" '
                f'dj-click="{e_event}" data-value="{i}" aria-label="{i} star">'
                f'<svg viewBox="0 0 24 24" fill="{("currentColor" if i <= current else "none")}" '
                f'stroke="currentColor" stroke-width="2" width="18" height="18">'
                f'<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>'
                f"</svg></button>"
            )
        return "".join(parts)

    def _render_emoji(self, e_event: str) -> str:
        emojis = [
            ("\U0001f44d", "thumbs_up"),
            ("\u2764\ufe0f", "heart"),
            ("\U0001f60a", "smile"),
            ("\U0001f914", "thinking"),
            ("\U0001f44e", "thumbs_down"),
        ]
        parts = []
        for emoji, val in emojis:
            active = "dj-feedback__btn--active" if self.value == val else ""
            parts.append(
                f'<button class="dj-feedback__btn {active}" '
                f'dj-click="{e_event}" data-value="{val}" aria-label="{val}">'
                f"{emoji}</button>"
            )
        return "".join(parts)
