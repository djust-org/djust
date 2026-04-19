"""Presence Avatars component for stacked online user avatars with overflow count."""

import html
from typing import List, Optional, Union

from djust import Component


class PresenceAvatars(Component):
    """Stacked avatar group showing online/present users with status dots.

    Displays overlapping avatar circles with online/away/busy presence indicators
    and a "+N" overflow count. Designed to pair with djust PresenceMixin.

    Usage in a LiveView::

        self.online = PresenceAvatars(
            users=[
                {"name": "Alice", "avatar": "/img/alice.jpg", "status": "online"},
                {"name": "Bob", "status": "away"},
                {"name": "Carol", "status": "busy"},
            ],
            max_display=3,
        )

    In template::

        {{ online|safe }}

    CSS Custom Properties::

        --dj-presence-size: avatar diameter (default: 2.25rem)
        --dj-presence-overlap: negative margin (default: -0.5rem)
        --dj-presence-border: ring around each avatar (default: 2px solid white)
        --dj-presence-dot-size: status dot size (default: 0.5rem)

    Args:
        users: List of user dicts (name, avatar, status) or objects.
               status can be: "online", "away", "busy", "offline" (default: "online").
        max_display: Maximum avatars before "+N" overflow (default: 5).
        custom_class: Additional CSS classes.
    """

    VALID_STATUSES = {"online", "away", "busy", "offline"}

    def __init__(
        self,
        users: Optional[List[Union[dict, object]]] = None,
        max_display: int = 5,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            users=users,
            max_display=max_display,
            custom_class=custom_class,
            **kwargs,
        )
        self.users = users or []
        self.max_display = max_display
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        visible = self.users[: self.max_display]
        overflow = len(self.users) - self.max_display

        parts = []
        for i, user in enumerate(visible):
            if isinstance(user, dict):
                name = user.get("name", "")
                src = user.get("avatar", "") or user.get("src", "")
                status = user.get("status", "online")
            elif hasattr(user, "get_full_name"):
                name = user.get_full_name() or str(user)
                src = getattr(user, "avatar", "")
                if hasattr(src, "url"):
                    src = src.url
                status = getattr(user, "status", "online")
            else:
                name = str(user)
                src = ""
                status = "online"

            e_name = html.escape(str(name))
            e_src = html.escape(str(src))
            safe_status = status if status in self.VALID_STATUSES else "online"
            initials = html.escape("".join(w[0].upper() for w in str(name).split()[:2] if w)) or "?"
            z = len(visible) - i

            if e_src:
                avatar_inner = f'<img src="{e_src}" alt="{e_name}" class="dj-presence__img">'
            else:
                avatar_inner = f'<span class="dj-presence__initials">{initials}</span>'

            dot = f'<span class="dj-presence__dot dj-presence__dot--{safe_status}"></span>'

            parts.append(
                f'<span class="dj-presence__item" title="{e_name}" '
                f'style="z-index:{z}">'
                f"{avatar_inner}{dot}"
                f"</span>"
            )

        if overflow > 0:
            parts.append(
                f'<span class="dj-presence__item dj-presence__overflow">+{overflow}</span>'
            )

        cls = "dj-presence"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        total = len(self.users)
        return (
            f'<div class="{cls}" role="group" '
            f'aria-label="{total} user{"s" if total != 1 else ""} present">'
            f"{''.join(parts)}"
            f"</div>"
        )
