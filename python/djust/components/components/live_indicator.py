"""Live Indicator component for showing per-field typing/editing indicators."""

import html
from typing import Optional

from djust import Component


class LiveIndicator(Component):
    """Shows 'Alice is typing...' style indicator per field.

    Designed for collaborative editing where you want to show which
    user is currently editing a specific field.

    Usage in a LiveView::

        self.indicator = LiveIndicator(
            user={"name": "Alice", "avatar": "/img/alice.jpg"},
            field="title",
            action="typing",
        )

    In template::

        {{ indicator|safe }}

    CSS Custom Properties::

        --dj-live-indicator-bg: background color (default: transparent)
        --dj-live-indicator-fg: text color (default: #6b7280)
        --dj-live-indicator-dot-color: animated dot color (default: #3b82f6)

    Args:
        user: Dict with name (and optional avatar) or string name.
        field: Field name being edited.
        action: Action label (default: "typing").
        active: Whether the indicator is visible (default: True).
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        user: Optional[object] = None,
        field: str = "",
        action: str = "typing",
        active: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            user=user,
            field=field,
            action=action,
            active=active,
            custom_class=custom_class,
            **kwargs,
        )
        self.user = user
        self.field = field
        self.action = action
        self.active = active
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        if not self.active or not self.user:
            cls = "dj-live-indicator dj-live-indicator--hidden"
            if self.custom_class:
                cls += f" {html.escape(self.custom_class)}"
            return f'<div class="{cls}"></div>'

        if isinstance(self.user, dict):
            name = self.user.get("name", "")
            avatar = self.user.get("avatar", "")
        elif hasattr(self.user, "get_full_name"):
            name = self.user.get_full_name() or str(self.user)
            avatar = getattr(self.user, "avatar", "")
            if hasattr(avatar, "url"):
                avatar = avatar.url
        else:
            name = str(self.user)
            avatar = ""

        e_name = html.escape(str(name))
        e_avatar = html.escape(str(avatar))
        e_field = html.escape(str(self.field))
        e_action = html.escape(str(self.action))

        cls = "dj-live-indicator"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        avatar_html = ""
        if e_avatar:
            avatar_html = f'<img src="{e_avatar}" alt="{e_name}" class="dj-live-indicator__avatar">'

        dots = (
            '<span class="dj-live-indicator__dots">'
            '<span class="dj-live-indicator__dot"></span>'
            '<span class="dj-live-indicator__dot"></span>'
            '<span class="dj-live-indicator__dot"></span>'
            "</span>"
        )

        field_attr = f' data-field="{e_field}"' if e_field else ""

        return (
            f'<div class="{cls}"{field_attr} role="status" aria-live="polite">'
            f"{avatar_html}"
            f'<span class="dj-live-indicator__text">'
            f"{e_name} is {e_action}{dots}</span>"
            f"</div>"
        )
