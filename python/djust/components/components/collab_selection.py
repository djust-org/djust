"""Collaborative Selection component for highlighting text/cells other users selected."""

import html
from typing import List, Optional, Union

from djust import Component


class CollabSelection(Component):
    """Highlights text or cell ranges selected by other users.

    Renders colored highlight rectangles with user labels, designed
    for Google-Docs-style collaborative selection visualization.

    Usage in a LiveView::

        self.selections = CollabSelection(
            users=[
                {"name": "Alice", "color": "#3b82f6",
                 "start": 10, "end": 25, "text": "selected text"},
                {"name": "Bob", "color": "#ef4444",
                 "start": 40, "end": 55, "text": "other selection"},
            ],
        )

    In template::

        {{ selections|safe }}

    CSS Custom Properties::

        --dj-collab-sel-label-size: label font size (default: 0.6875rem)
        --dj-collab-sel-opacity: highlight opacity (default: 0.25)

    Args:
        users: List of user dicts with name, color, and selection data.
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
                text = user.get("text", "")
                start = user.get("start", 0)
                end = user.get("end", 0)
            else:
                name = str(user)
                color = self.DEFAULT_COLORS[i % len(self.DEFAULT_COLORS)]
                text = ""
                start = 0
                end = 0

            e_name = html.escape(str(name))
            e_color = html.escape(str(color))
            e_text = html.escape(str(text))

            try:
                s = int(start)
            except (ValueError, TypeError):
                s = 0
            try:
                e = int(end)
            except (ValueError, TypeError):
                e = 0

            parts.append(
                f'<span class="dj-collab-sel__range" '
                f'style="--dj-collab-sel-color:{e_color}" '
                f'data-user="{e_name}" data-start="{s}" data-end="{e}">'
                f'<span class="dj-collab-sel__highlight">{e_text}</span>'
                f'<span class="dj-collab-sel__label" '
                f'style="background:{e_color}">{e_name}</span>'
                f"</span>"
            )

        cls = "dj-collab-sel"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        total = len(self.users)
        return (
            f'<div class="{cls}" role="group" '
            f'aria-label="{total} selection{"s" if total != 1 else ""}" '
            f'dj-hook="CollabSelection">'
            f"{''.join(parts)}"
            f"</div>"
        )
