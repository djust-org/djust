"""Cursors Overlay component for showing other users' cursor positions (Google Docs style)."""

import html
from typing import List, Optional, Union

from djust import Component


class CursorsOverlay(Component):
    """Overlay showing other users' cursor positions in real-time.

    Renders colored cursor arrows with user name labels, designed to
    pair with djust PresenceMixin for live cursor tracking.

    Usage in a LiveView::

        self.cursors = CursorsOverlay(
            users=[
                {"name": "Alice", "color": "#3b82f6", "x": 120, "y": 340},
                {"name": "Bob", "color": "#ef4444", "x": 450, "y": 200},
            ],
        )

    In template::

        {{ cursors|safe }}

    CSS Custom Properties::

        --dj-cursors-label-font-size: label text size (default: 0.75rem)
        --dj-cursors-label-radius: label border radius (default: 0.25rem)

    Args:
        users: List of user dicts with name, color, x, y.
        custom_class: Additional CSS classes.
    """

    DEFAULT_COLORS = [
        "#3b82f6",
        "#ef4444",
        "#22c55e",
        "#f59e0b",
        "#8b5cf6",
        "#ec4899",
        "#06b6d4",
        "#f97316",
    ]

    def __init__(
        self,
        users: Optional[List[Union[dict, object]]] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            users=users,
            custom_class=custom_class,
            **kwargs,
        )
        self.users = users or []
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        parts = []
        for i, user in enumerate(self.users):
            if isinstance(user, dict):
                name = user.get("name", "")
                color = user.get("color", self.DEFAULT_COLORS[i % len(self.DEFAULT_COLORS)])
                x = user.get("x", 0)
                y = user.get("y", 0)
            else:
                name = str(user)
                color = self.DEFAULT_COLORS[i % len(self.DEFAULT_COLORS)]
                x = 0
                y = 0

            e_name = html.escape(str(name))
            e_color = html.escape(str(color))

            try:
                px = int(x)
            except (ValueError, TypeError):
                px = 0
            try:
                py = int(y)
            except (ValueError, TypeError):
                py = 0

            # SVG cursor arrow
            cursor_svg = (
                f'<svg class="dj-cursors__arrow" width="16" height="20" viewBox="0 0 16 20" '
                f'fill="{e_color}">'
                f'<path d="M0 0L16 12L8 12L12 20L8 18L4 12L0 16Z"/>'
                f"</svg>"
            )

            parts.append(
                f'<div class="dj-cursors__cursor" '
                f'style="left:{px}px;top:{py}px" '
                f'data-user="{e_name}">'
                f"{cursor_svg}"
                f'<span class="dj-cursors__label" '
                f'style="background:{e_color}">{e_name}</span>'
                f"</div>"
            )

        cls = "dj-cursors"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        total = len(self.users)
        return (
            f'<div class="{cls}" role="group" '
            f'aria-label="{total} cursor{"s" if total != 1 else ""}" '
            f'dj-hook="CursorsOverlay">'
            f"{''.join(parts)}"
            f"</div>"
        )
