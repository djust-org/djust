"""Error boundary component for catching rendering errors."""

import html

from djust import Component
from typing import Any


class ErrorBoundary(Component):
    """Style-agnostic error boundary component.

    Shows ``content`` normally and a fallback message while ``error`` is set.
    It does not catch exceptions itself: your view catches the failure and
    sets ``error``, then clears it (for example from ``retry_event``) once the
    work succeeds.

    Usage in a LiveView::

        self.boundary = ErrorBoundary(
            content="Revenue chart",
            fallback="The chart could not load.",
            retry_event="retry_load",
        )

        @event_handler()
        def retry_load(self, **kwargs):
            self.boundary.error = ""

    In template::

        {{ boundary }}

    Args:
        fallback: Fallback message to show on error
        error: Current error message (empty = no error)
        retry_event: djust event for retrying
        custom_class: Additional CSS classes
        content: What to show while there is no error — plain text, escaped
    """

    def __init__(
        self,
        fallback: str = "Something went wrong",
        error: str = "",
        retry_event: str = "",
        custom_class: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> None:
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
                retry_html = (
                    f'<button class="dj-error-boundary__retry" '
                    f"{self.event_attrs(self.retry_event)}>Retry</button>"
                )
            return (
                f'<div class="{class_str}" role="alert">'
                f'<div class="dj-error-boundary__fallback">'
                f'<p class="dj-error-boundary__message">{e_fallback}</p>'
                f"{retry_html}</div></div>"
            )

        return f'<div class="{class_str}">{html.escape(self.content)}</div>'
