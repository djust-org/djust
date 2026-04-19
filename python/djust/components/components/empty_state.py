"""EmptyState component."""

import html
from djust import Component


class EmptyState(Component):
    """Empty state placeholder component.

    Args:
        title: heading text
        description: descriptive text
        icon: optional icon
        action_label: CTA button text
        action_event: dj-click event for CTA"""

    def __init__(
        self,
        title: str = "",
        description: str = "",
        icon: str = "",
        action_label: str = "",
        action_event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            title=title,
            description=description,
            icon=icon,
            action_label=action_label,
            action_event=action_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.title = title
        self.description = description
        self.icon = icon
        self.action_label = action_label
        self.action_event = action_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the emptystate HTML."""
        cls = "empty-state"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        icon_html = (
            f'<div class="empty-state-icon">{html.escape(self.icon)}</div>' if self.icon else ""
        )
        title_html = (
            f'<h3 class="empty-state-title">{html.escape(self.title)}</h3>' if self.title else ""
        )
        desc_html = (
            f'<p class="empty-state-description">{html.escape(self.description)}</p>'
            if self.description
            else ""
        )
        action_html = ""
        if self.action_label:
            e_event = html.escape(self.action_event)
            e_label = html.escape(self.action_label)
            action_html = f'<button class="btn btn-primary empty-state-action" dj-click="{e_event}">{e_label}</button>'
        return f'<div class="{cls}">{icon_html}{title_html}{desc_html}{action_html}</div>'
