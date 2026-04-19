"""Chat Bubble component for message thread UI with sender, time, and status."""

import html
from typing import Dict, Optional

from djust import Component


class ChatBubble(Component):
    """Single chat message bubble with sender avatar, timestamp, and delivery status.

    Displays an individual chat message with avatar (image or initials fallback),
    sender name, timestamp, and optional delivery status indicator.

    Usage in a LiveView::

        self.bubble = ChatBubble(
            message={
                "sender": "user",
                "name": "Alice",
                "text": "Hello!",
                "time": "10:01 AM",
                "avatar": "/img/alice.jpg",
                "status": "delivered",
            }
        )

    In template::

        {{ bubble|safe }}

    CSS Custom Properties::

        --dj-bubble-bg: bubble background
        --dj-bubble-user-bg: user message background
        --dj-bubble-other-bg: other sender message background
        --dj-bubble-avatar-size: avatar circle size (default: 2.25rem)
        --dj-bubble-radius: bubble border-radius (default: 0.75rem)

    Args:
        message: Dict with keys: sender, name, text, time, avatar (optional), status (optional).
                 sender="user" renders right-aligned; anything else renders left-aligned.
                 status can be: "sending", "sent", "delivered", "read", "error".
        custom_class: Additional CSS classes.
    """

    VALID_STATUSES = {"sending", "sent", "delivered", "read", "error"}

    def __init__(
        self,
        message: Optional[Dict] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            message=message,
            custom_class=custom_class,
            **kwargs,
        )
        self.message = message or {}
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        msg = self.message
        sender = msg.get("sender", "user")
        name = html.escape(str(msg.get("name", "")))
        text = html.escape(str(msg.get("text", "")))
        time_str = html.escape(str(msg.get("time", "")))
        avatar_src = html.escape(str(msg.get("avatar", "")))
        status = msg.get("status", "")

        side = "dj-bubble--user" if sender == "user" else "dj-bubble--other"

        cls = f"dj-bubble {side}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        # Avatar: image or initials fallback
        initials = "".join(w[0].upper() for w in str(msg.get("name", "")).split()[:2] if w)
        if not initials:
            initials = "?"
        initials = html.escape(initials)

        if avatar_src:
            avatar_html = (
                f'<span class="dj-bubble__avatar">'
                f'<img src="{avatar_src}" alt="{name}" class="dj-bubble__avatar-img">'
                f"</span>"
            )
        else:
            avatar_html = (
                f'<span class="dj-bubble__avatar dj-bubble__avatar--initials">{initials}</span>'
            )

        # Status indicator
        status_html = ""
        if status and status in self.VALID_STATUSES:
            e_status = html.escape(status)
            status_icons = {
                "sending": "&#8987;",  # hourglass
                "sent": "&#10003;",  # single check
                "delivered": "&#10003;&#10003;",  # double check
                "read": "&#10003;&#10003;",  # double check (colored via CSS)
                "error": "&#9888;",  # warning
            }
            icon = status_icons.get(status, "")
            status_html = (
                f'<span class="dj-bubble__status dj-bubble__status--{e_status}" '
                f'aria-label="{e_status}">{icon}</span>'
            )

        # Header (name + time)
        header_html = ""
        if name or time_str:
            name_part = f'<span class="dj-bubble__name">{name}</span>' if name else ""
            time_part = f'<span class="dj-bubble__time">{time_str}</span>' if time_str else ""
            header_html = f'<div class="dj-bubble__header">{name_part}{time_part}</div>'

        # Footer (status)
        footer_html = ""
        if status_html:
            footer_html = f'<div class="dj-bubble__footer">{status_html}</div>'

        return (
            f'<div class="{cls}">'
            f"{avatar_html}"
            f'<div class="dj-bubble__content">'
            f"{header_html}"
            f'<div class="dj-bubble__text">{text}</div>'
            f"{footer_html}"
            f"</div>"
            f"</div>"
        )
