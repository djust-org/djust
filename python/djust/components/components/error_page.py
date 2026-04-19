"""Error Page component for styled error pages."""

import html

from djust import Component


class ErrorPage(Component):
    """Styled error page with code, title, message, and action.

    Usage in a LiveView::

        self.err = ErrorPage(code=404, title="Not Found")

    In template::

        {{ err|safe }}

    CSS Custom Properties::

        --dj-error-page-bg: background (default: #fff)
        --dj-error-page-code-color: error code color (default: #3b82f6)
        --dj-error-page-title-color: title color (default: #111827)
        --dj-error-page-msg-color: message color (default: #6b7280)

    Args:
        code: HTTP error code (e.g. 404, 500).
        title: Error title.
        message: Error description.
        action_url: URL for the action button.
        action_label: Label for the action button.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        code: int = 500,
        title: str = "Something went wrong",
        message: str = "",
        action_url: str = "/",
        action_label: str = "Go Home",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            code=code,
            title=title,
            message=message,
            action_url=action_url,
            action_label=action_label,
            custom_class=custom_class,
            **kwargs,
        )
        self.code = code
        self.title = title
        self.message = message
        self.action_url = action_url
        self.action_label = action_label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-error-page"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        try:
            code = int(self.code)
        except (ValueError, TypeError):
            code = 500

        e_title = html.escape(str(self.title))
        e_message = html.escape(str(self.message))
        e_url = html.escape(str(self.action_url))
        e_label = html.escape(str(self.action_label))

        msg_html = ""
        if e_message:
            msg_html = f'<p class="dj-error-page__message">{e_message}</p>'

        action_html = ""
        if e_url:
            action_html = f'<a href="{e_url}" class="dj-error-page__action">{e_label}</a>'

        return (
            f'<div class="{cls}" role="alert">'
            f'<div class="dj-error-page__code">{code}</div>'
            f'<h1 class="dj-error-page__title">{e_title}</h1>'
            f"{msg_html}"
            f"{action_html}"
            f"</div>"
        )
