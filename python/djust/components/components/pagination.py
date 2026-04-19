"""Pagination component."""

import html
from djust import Component


class Pagination(Component):
    """Pagination controls component.

    Args:
        page: current page number
        total_pages: total number of pages
        prev_event: dj-click event for previous page
        next_event: dj-click event for next page"""

    def __init__(
        self,
        page: int = 1,
        total_pages: int = 1,
        prev_event: str = "page_prev",
        next_event: str = "page_next",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            page=page,
            total_pages=total_pages,
            prev_event=prev_event,
            next_event=next_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.page = page
        self.total_pages = total_pages
        self.prev_event = prev_event
        self.next_event = next_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the pagination HTML."""
        cls = "dj-pagination"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_prev = html.escape(self.prev_event)
        e_next = html.escape(self.next_event)
        prev_disabled = " disabled" if self.page <= 1 else ""
        next_disabled = " disabled" if self.page >= self.total_pages else ""
        return (
            f'<nav class="{cls}">'
            f'<button class="dj-pagination__prev" dj-click="{e_prev}"{prev_disabled}>&laquo; Prev</button>'
            f'<span class="dj-pagination__info">Page {self.page} of {self.total_pages}</span>'
            f'<button class="dj-pagination__next" dj-click="{e_next}"{next_disabled}>Next &raquo;</button>'
            f"</nav>"
        )
