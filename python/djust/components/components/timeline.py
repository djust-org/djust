"""Timeline component."""

import html

from djust import Component


class Timeline(Component):
    """Timeline component.

    Args:
        items: list of dicts with keys: title, time, content
        content: pre-rendered HTML (alternative to items list)"""

    def __init__(
        self,
        items: list = None,
        content: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            content=content,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.content = content
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the timeline HTML."""
        cls = "timeline"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        if self.items:
            parts = []
            for item in self.items:
                if not isinstance(item, dict):
                    continue
                title = html.escape(str(item.get("title", "")))
                time = html.escape(str(item.get("time", "")))
                content = item.get("content", "")
                title_html = f'<div class="timeline-title">{title}</div>' if title else ""
                time_html = f'<div class="timeline-time">{time}</div>' if time else ""
                parts.append(
                    f'<div class="timeline-item">'
                    f'<div class="timeline-marker"></div>'
                    f'<div class="timeline-content">{title_html}{time_html}{content}</div>'
                    f"</div>"
                )
            inner = "".join(parts)
        else:
            inner = self.content
        return f'<div class="{cls}">{inner}</div>'
