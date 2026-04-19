"""Masonry Grid component — Pinterest-style layout."""

import html
from typing import Optional

from djust import Component


class MasonryGrid(Component):
    """Pinterest-style masonry grid layout.

    Usage in a LiveView::

        self.grid = MasonryGrid(
            items=[
                {"content": "<p>Card 1</p>", "height": 200},
                {"content": "<p>Card 2</p>", "height": 150},
                {"content": "<img src='photo.jpg' />", "height": 300},
            ],
            columns=3,
        )

    In template::

        {{ grid|safe }}

    Args:
        items: List of dicts with ``content`` (HTML string), optional ``height``, ``class``
        columns: Number of columns (default: 3)
        gap: Gap between items in px (default: 16)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        items: Optional[list] = None,
        columns: int = 3,
        gap: int = 16,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            columns=columns,
            gap=gap,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        try:
            self.columns = max(1, int(columns))
        except (ValueError, TypeError):
            self.columns = 3
        try:
            self.gap = int(gap)
        except (ValueError, TypeError):
            self.gap = 16
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-masonry"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.items:
            return f'<div class="{class_str}"></div>'

        # Distribute items across columns (shortest-column-first)
        col_heights = [0] * self.columns
        col_items = [[] for _ in range(self.columns)]

        for item in self.items:
            if not isinstance(item, dict):
                continue
            # Find shortest column
            min_col = col_heights.index(min(col_heights))
            col_items[min_col].append(item)
            try:
                h = int(item.get("height", 100))
            except (ValueError, TypeError):
                h = 100
            col_heights[min_col] += h + self.gap

        # Render columns
        col_html = []
        for col_idx, items_in_col in enumerate(col_items):
            item_cards = []
            for item in items_in_col:
                content = str(item.get("content", ""))
                item_class = html.escape(str(item.get("class", "")))
                extra = f" {item_class}" if item_class else ""
                item_cards.append(f'<div class="dj-masonry__item{extra}">{content}</div>')
            col_html.append(f'<div class="dj-masonry__col">{"".join(item_cards)}</div>')

        style = f"--dj-masonry-columns: {self.columns}; --dj-masonry-gap: {self.gap}px"

        return f'<div class="{class_str}" style="{style}" role="list">{"".join(col_html)}</div>'
