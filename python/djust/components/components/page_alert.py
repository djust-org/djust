"""Page alert / banner component for full-width content-area alerts."""

import html

from djust import Component


class PageAlert(Component):
    """Style-agnostic full-width page alert / banner component.

    Full-width content-area alert with variant styling and optional dismiss.

    Usage in a LiveView::

        self.banner = PageAlert(
            message="Changes saved successfully!",
            type="success",
            dismissible=True,
            dismiss_event="dismiss_alert",
        )

    In template::

        {{ banner|safe }}

    Args:
        message: Alert message text
        type: Alert type (info, success, warning, error)
        dismissible: Whether the alert can be dismissed
        dismiss_event: djust event for dismissing
        icon: Optional icon/emoji
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        message: str = "",
        type: str = "info",
        dismissible: bool = False,
        dismiss_event: str = "dismiss_alert",
        icon: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            message=message,
            type=type,
            dismissible=dismissible,
            dismiss_event=dismiss_event,
            icon=icon,
            custom_class=custom_class,
            **kwargs,
        )
        self.message = message
        self.type = type
        self.dismissible = dismissible
        self.dismiss_event = dismiss_event
        self.icon = icon
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-page-alert", f"dj-page-alert--{self.type}"]
        if self.dismissible:
            classes.append("dj-page-alert--dismissible")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_msg = html.escape(self.message)

        icon_html = ""
        if self.icon:
            icon_html = f'<span class="dj-page-alert__icon">{html.escape(self.icon)}</span>'

        dismiss_html = ""
        if self.dismissible:
            e_event = html.escape(self.dismiss_event)
            dismiss_html = (
                f'<button class="dj-page-alert__dismiss" '
                f'dj-click="{e_event}" aria-label="Dismiss">&times;</button>'
            )

        return (
            f'<div class="{class_str}" role="alert">'
            f"{icon_html}"
            f'<span class="dj-page-alert__message">{e_msg}</span>'
            f"{dismiss_html}</div>"
        )
