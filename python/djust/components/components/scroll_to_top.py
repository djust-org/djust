"""Scroll to Top button component."""

import html

from djust import Component


class ScrollToTop(Component):
    """Floating scroll-to-top button that appears after a scroll threshold.

    Client-side only — no server round-trip needed.

    Usage in a LiveView::

        self.scroll_btn = ScrollToTop(threshold="300px")

    In template::

        {{ scroll_btn|safe }}

    CSS Custom Properties::

        --dj-scroll-top-bg: background color
        --dj-scroll-top-fg: text/icon color
        --dj-scroll-top-size: button size
        --dj-scroll-top-radius: border radius
        --dj-scroll-top-right: right offset
        --dj-scroll-top-bottom: bottom offset
        --dj-scroll-top-z: z-index

    Args:
        threshold: Scroll distance before button appears (default "300px")
        label: Accessible label (default "Back to top")
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        threshold: str = "300px",
        label: str = "Back to top",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(threshold=threshold, label=label, custom_class=custom_class, **kwargs)
        self.threshold = threshold
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the scroll-to-top button HTML."""
        classes = ["dj-scroll-to-top"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_threshold = html.escape(self.threshold)
        e_label = html.escape(self.label)

        return (
            f'<button class="{class_str}" '
            f'data-threshold="{e_threshold}" '
            f'aria-label="{e_label}" '
            f'title="{e_label}" '
            f'style="display:none">'
            f'<svg width="20" height="20" viewBox="0 0 20 20" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round">'
            f'<path d="M10 16V4M10 4l-6 6M10 4l6 6"/>'
            f"</svg>"
            f"</button>"
        )
