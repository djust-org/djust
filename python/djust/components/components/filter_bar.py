"""FilterBar component."""

import html
from djust import Component


class FilterBar(Component):
    """Filter bar component with filter controls.

    Args:
        content: filter controls (pre-rendered HTML)
        clear_event: dj-click event for clearing filters
        active_count: number of active filters"""

    def __init__(
        self,
        content: str = "",
        clear_event: str = "clear_filters",
        active_count: int = 0,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            clear_event=clear_event,
            active_count=active_count,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.clear_event = clear_event
        self.active_count = active_count
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the filterbar HTML."""
        cls = "dj-filter-bar"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_clear = html.escape(self.clear_event)
        badge_html = (
            f' <span class="dj-filter-bar__badge">{self.active_count}</span>'
            if self.active_count > 0
            else ""
        )
        clear_html = (
            f'<button class="dj-filter-bar__clear" dj-click="{e_clear}">Clear filters{badge_html}</button>'
            if self.active_count > 0
            else ""
        )
        return (
            f'<div class="{cls}">'
            f'<div class="dj-filter-bar__controls">{self.content}</div>'
            f"{clear_html}"
            f"</div>"
        )
