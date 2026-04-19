"""Error boundary component for catching rendering errors."""

import html

from djust import Component


class ErrorBoundary(Component):
    """Style-agnostic error boundary component.

    Catches rendering errors and displays a fallback message.

    Usage in a LiveView::

        self.boundary = ErrorBoundary(
            fallback="Component failed to render",
        )

    In template::

        {{ boundary|safe }}

    Args:
        fallback: Fallback message to show on error
        error: Current error message (empty = no error)
        retry_event: djust event for retrying
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        fallback: str = "Something went wrong",
        error: str = "",
        retry_event: str = "",
        custom_class: str = "",
        content: str = "",
        **kwargs,
    ):
        super().__init__(
            fallback=fallback,
            error=error,
            retry_event=retry_event,
            custom_class=custom_class,
            content=content,
            **kwargs,
        )
        self.fallback = fallback
        self.error = error
        self.retry_event = retry_event
        self.custom_class = custom_class
        self.content = content

    def _render_custom(self) -> str:
        classes = ["dj-error-boundary"]
        if self.error:
            classes.append("dj-error-boundary--error")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if self.error:
            e_fallback = html.escape(self.fallback)
            retry_html = ""
            if self.retry_event:
                e_retry = html.escape(self.retry_event)
                retry_html = (
                    f'<button class="dj-error-boundary__retry" dj-click="{e_retry}">Retry</button>'
                )
            return (
                f'<div class="{class_str}" role="alert">'
                f'<div class="dj-error-boundary__fallback">'
                f'<p class="dj-error-boundary__message">{e_fallback}</p>'
                f"{retry_html}</div></div>"
            )

        return f'<div class="{class_str}">{html.escape(self.content)}</div>'
