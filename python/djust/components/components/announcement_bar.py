"""AnnouncementBar component."""

import html
from djust import Component


class AnnouncementBar(Component):
    """Announcement/banner bar component.

    Args:
        content: bar content (pre-rendered HTML)
        variant: info, warning, danger, success
        dismissible: whether bar can be dismissed
        dismiss_event: dj-click event name"""

    def __init__(
        self,
        content: str = "",
        variant: str = "info",
        dismissible: bool = False,
        dismiss_event: str = "dismiss_announcement",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            variant=variant,
            dismissible=dismissible,
            dismiss_event=dismiss_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.variant = variant
        self.dismissible = dismissible
        self.dismiss_event = dismiss_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the announcementbar HTML."""
        e_variant = html.escape(self.variant)
        cls = f"dj-announcement-bar dj-announcement-bar--{e_variant}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        close_html = ""
        if self.dismissible:
            e_dismiss = html.escape(self.dismiss_event)
            close_html = f'<button class="dj-announcement-bar__close" dj-click="{e_dismiss}">&times;</button>'
        return (
            f'<div class="{cls}" role="banner">'
            f'<div class="dj-announcement-bar__content">{self.content}</div>'
            f"{close_html}"
            f"</div>"
        )
