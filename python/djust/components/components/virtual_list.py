"""VirtualList component."""

import html

from djust import Component


class VirtualList(Component):
    """Paginated virtual list component.

    Args:
        items: list of dicts or strings
        total: total number of items
        page: current page number
        page_size: items per page
        load_more_event: dj-click event for loading more"""

    def __init__(
        self,
        items: list = None,
        total: int = 0,
        page: int = 1,
        page_size: int = 20,
        load_more_event: str = "load_more",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            load_more_event=load_more_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.total = total
        self.page = page
        self.page_size = page_size
        self.load_more_event = load_more_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the virtuallist HTML."""
        items = self.items or []
        cls = "virtual-list"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_load = html.escape(self.load_more_event)
        rows = ""
        for item in items:
            if isinstance(item, dict):
                label = html.escape(str(item.get("label", str(item))))
            else:
                label = html.escape(str(item))
            rows += f'<div class="vl-item"><span class="vl-item-label">{label}</span></div>'
        shown = len(items)
        has_more = (self.page * self.page_size) < self.total
        load_more_html = (
            f'<div class="vl-load-more">'
            f'<button class="btn btn-ghost btn-sm" dj-click="{e_load}">Load more</button>'
            f"</div>"
            if has_more
            else ""
        )
        return (
            f'<div class="{cls}">'
            f'<div class="vl-info">Showing {shown} of {self.total} items</div>'
            f'<div class="vl-scroll">{rows}{load_more_html}</div>'
            f"</div>"
        )
