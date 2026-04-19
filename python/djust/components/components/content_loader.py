"""ContentLoader / Suspense component for showing placeholder until data is ready."""

import html

from djust import Component


class ContentLoader(Component):
    """Style-agnostic content loader / suspense component.

    Shows a placeholder (e.g. skeleton) until a server event signals that
    content is ready. Pairs with ``SkeletonFactory`` for automatic loading states.

    Usage in a LiveView::

        # Show skeleton until data_loaded event fires
        self.loader = ContentLoader(
            loading_event="data_loaded",
            loaded=False,
            placeholder=SkeletonFactory(component="data_table", columns=5).render(),
            content="<table>...actual data...</table>",
        )

        # After data loads, update:
        self.loader = ContentLoader(
            loading_event="data_loaded",
            loaded=True,
            content=rendered_table,
        )

    In template::

        {{ loader|safe }}

    CSS Custom Properties::

        --dj-content-loader-min-height: minimum height (default: 4rem)
        --dj-content-loader-transition: fade transition duration (default: 0.3s)

    Args:
        loading_event: Server event name that signals content is ready
        loaded: Whether content has loaded (default: False)
        placeholder: HTML string to show while loading (e.g. skeleton)
        content: Actual content to show when loaded
        error: Error message to display if loading failed
        error_event: Optional event name for retry action
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        loading_event: str = "data_loaded",
        loaded: bool = False,
        placeholder: str = "",
        content: str = "",
        error: str = "",
        error_event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            loading_event=loading_event,
            loaded=loaded,
            placeholder=placeholder,
            content=content,
            error=error,
            error_event=error_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.loading_event = loading_event
        self.loaded = loaded
        self.placeholder = placeholder
        self.content = content
        self.error = error
        self.error_event = error_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-content-loader"
        if self.loaded:
            cls += " dj-content-loader--loaded"
        if self.error:
            cls += " dj-content-loader--error"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_event = html.escape(self.loading_event)

        if self.error:
            e_error = html.escape(self.error)
            retry_html = ""
            if self.error_event:
                e_retry = html.escape(self.error_event)
                retry_html = (
                    f'<button class="dj-content-loader__retry" dj-click="{e_retry}">Retry</button>'
                )
            return (
                f'<div class="{cls}" data-loading-event="{e_event}">'
                f'<div class="dj-content-loader__error" role="alert">'
                f'<span class="dj-content-loader__error-msg">{e_error}</span>'
                f"{retry_html}</div></div>"
            )

        if self.loaded:
            # Content is ready — render actual content (already safe HTML from server)
            return (
                f'<div class="{cls}" data-loading-event="{e_event}">'
                f'<div class="dj-content-loader__content">{self.content}</div>'
                f"</div>"
            )

        # Still loading — show placeholder
        placeholder_html = self.placeholder or (
            '<div class="dj-content-loader__default-placeholder">'
            '<span class="dj-spinner" role="status" '
            'aria-label="Loading"></span>'
            "</div>"
        )
        return (
            f'<div class="{cls}" data-loading-event="{e_event}" '
            f'role="status" aria-label="Loading">'
            f'<div class="dj-content-loader__placeholder">'
            f"{placeholder_html}</div></div>"
        )
