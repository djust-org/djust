"""Avatar Group component for stacked overlapping avatars with overflow count."""

import html
from typing import List, Optional, Union

from djust import Component


class AvatarGroup(Component):
    """Style-agnostic avatar group using CSS custom properties.

    Displays a row of overlapping avatar circles with a "+N" overflow
    indicator when the list exceeds ``max_display``.

    Usage in a LiveView::

        self.team = AvatarGroup(
            users=[
                {"name": "Alice", "avatar": "/img/alice.jpg"},
                {"name": "Bob", "avatar": "/img/bob.jpg"},
                {"name": "Carol"},  # shows initials "C"
            ],
            max_display=3,
        )

    In template::

        {{ team|safe }}

    CSS Custom Properties::

        --dj-avatar-group-size: avatar diameter (default: 2.5rem)
        --dj-avatar-group-overlap: negative margin (default: -0.75rem)
        --dj-avatar-group-border: ring around each avatar (default: 2px solid white)
        --dj-avatar-group-bg: fallback background for initials (default: #e5e7eb)
        --dj-avatar-group-fg: initials text color (default: #374151)
        --dj-avatar-group-radius: border-radius (default: 50%)

    Args:
        users: List of user dicts (name, avatar/src) or objects with
               get_full_name() and optional avatar attribute.
        max_display: Maximum avatars shown before "+N" overflow (default: 5).
        size: Size variant (sm, md, lg).
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        users: Optional[List[Union[dict, object]]] = None,
        max_display: int = 5,
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            users=users,
            max_display=max_display,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.users = users or []
        self.max_display = max_display
        self.size = size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the avatar group HTML."""
        visible = self.users[: self.max_display]
        overflow = len(self.users) - self.max_display

        parts = []
        for i, user in enumerate(visible):
            if isinstance(user, dict):
                name = user.get("name", "")
                src = user.get("avatar", "") or user.get("src", "")
            elif hasattr(user, "get_full_name"):
                name = user.get_full_name() or str(user)
                src = getattr(user, "avatar", "")
                if hasattr(src, "url"):
                    src = src.url
            else:
                name = str(user)
                src = ""
            e_name = html.escape(str(name))
            e_src = html.escape(str(src))
            initials = html.escape("".join(w[0].upper() for w in str(name).split()[:2] if w))
            z = len(visible) - i
            if e_src:
                parts.append(
                    f'<span class="dj-avatar-group__item" title="{e_name}" '
                    f'style="z-index:{z}">'
                    f'<img src="{e_src}" alt="{e_name}" '
                    f'class="dj-avatar-group__img"></span>'
                )
            else:
                parts.append(
                    f'<span class="dj-avatar-group__item '
                    f'dj-avatar-group__initials" title="{e_name}" '
                    f'style="z-index:{z}">{initials}</span>'
                )

        if overflow > 0:
            parts.append(
                f'<span class="dj-avatar-group__item dj-avatar-group__overflow">+{overflow}</span>'
            )

        classes = [
            "dj-avatar-group",
            f"dj-avatar-group--{html.escape(self.size)}",
        ]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        return f'<div class="{" ".join(classes)}">{"".join(parts)}</div>'
