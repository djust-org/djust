"""Skeleton component."""

import html
from djust import Component


class Skeleton(Component):
    """Skeleton loading placeholder component.

    Args:
        skeleton_type: text, card, avatar, table
        lines: number of lines for text/table type"""

    def __init__(
        self,
        skeleton_type: str = "text",
        lines: int = 3,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            skeleton_type=skeleton_type,
            lines=lines,
            custom_class=custom_class,
            **kwargs,
        )
        self.skeleton_type = skeleton_type
        self.lines = lines
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the skeleton HTML."""
        if self.skeleton_type == "avatar":
            cls = "skeleton-avatar"
            if self.custom_class:
                cls += f" {html.escape(self.custom_class)}"
            return f'<div class="{cls}"></div>'
        if self.skeleton_type == "card":
            inner = "".join('<div class="skeleton-line"></div>' for _ in range(max(1, self.lines)))
            cls = "skeleton-card"
            if self.custom_class:
                cls += f" {html.escape(self.custom_class)}"
            return (
                f'<div class="{cls}">'
                f'<div class="skeleton-card-header"></div>'
                f'<div class="skeleton-card-body">{inner}</div>'
                f"</div>"
            )
        if self.skeleton_type == "table":
            rows = "".join('<div class="skeleton-line"></div>' for _ in range(max(1, self.lines)))
            cls = "skeleton-table"
            if self.custom_class:
                cls += f" {html.escape(self.custom_class)}"
            return (
                f'<div class="{cls}">'
                f'<div class="skeleton-line skeleton-line-header"></div>'
                f"{rows}"
                f"</div>"
            )
        # Default: text lines
        line_html = "".join('<div class="skeleton-line"></div>' for _ in range(max(1, self.lines)))
        cls = "skeleton-text"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        return f'<div class="{cls}">{line_html}</div>'
