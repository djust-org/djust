"""Infinite scroll component for lazy-loading content."""

import html

from djust import Component


class InfiniteScroll(Component):
    """Style-agnostic infinite scroll component.

    Fires an event when scrolling near the bottom of the container.

    Usage in a LiveView::

        self.scroller = InfiniteScroll(
            load_event="load_more",
            threshold="200px",
            loading=True,
        )

    In template::

        {{ scroller|safe }}

    Args:
        load_event: djust event to fire when threshold is reached
        threshold: Distance from bottom to trigger (default: "200px")
        loading: Whether currently loading more items
        finished: Whether all items have been loaded
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        load_event: str = "load_more",
        threshold: str = "200px",
        loading: bool = False,
        finished: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            load_event=load_event,
            threshold=threshold,
            loading=loading,
            finished=finished,
            custom_class=custom_class,
            **kwargs,
        )
        self.load_event = load_event
        self.threshold = threshold
        self.loading = loading
        self.finished = finished
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-infinite-scroll"]
        if self.loading:
            classes.append("dj-infinite-scroll--loading")
        if self.finished:
            classes.append("dj-infinite-scroll--finished")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_event = html.escape(self.load_event)
        e_threshold = html.escape(self.threshold)

        inner = ""
        if self.loading:
            inner = (
                '<div class="dj-infinite-scroll__spinner" role="status" aria-label="Loading"></div>'
            )
        elif self.finished:
            inner = '<div class="dj-infinite-scroll__done">No more items</div>'

        return (
            f'<div class="{class_str}" '
            f'dj-hook="InfiniteScroll" '
            f'data-event="{e_event}" '
            f'data-threshold="{e_threshold}">'
            f"{inner}</div>"
        )
