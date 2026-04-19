"""Data Card Grid component — filterable card grid layout."""

import html
from typing import List, Optional

from djust import Component


class DataCardGrid(Component):
    """Filterable card grid layout.

    Renders a responsive grid of data cards with optional category filtering.

    Usage in a LiveView::

        self.grid = DataCardGrid(
            items=[
                {"title": "Item 1", "description": "Desc", "category": "A"},
                {"title": "Item 2", "description": "Desc", "category": "B"},
            ],
            columns=3,
        )

    In template::

        {{ grid|safe }}

    CSS Custom Properties::

        --dj-data-card-grid-gap: grid gap (default: 1rem)
        --dj-data-card-bg: card background (default: #fff)
        --dj-data-card-border: card border color (default: #e5e7eb)
        --dj-data-card-radius: card border radius (default: 0.5rem)
        --dj-data-card-padding: card padding (default: 1rem)

    Args:
        items: List of item dicts with title, description, category, image, url.
        columns: Number of columns (default: 3).
        filter_key: Key in items used for filtering (default: "category").
        event: Event fired on card click.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        items: Optional[List[dict]] = None,
        columns: int = 3,
        filter_key: str = "category",
        event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            columns=columns,
            filter_key=filter_key,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.columns = columns
        self.filter_key = filter_key
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-data-card-grid"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        if not isinstance(self.items, list):
            return f'<div class="{cls}" role="list"></div>'

        try:
            cols = int(self.columns)
        except (ValueError, TypeError):
            cols = 3

        e_event = html.escape(self.event)

        # Collect unique categories for filter bar
        categories = []
        seen = set()
        fk = self.filter_key
        for it in self.items:
            if isinstance(it, dict):
                cat = str(it.get(fk, ""))
                if cat and cat not in seen:
                    categories.append(cat)
                    seen.add(cat)

        filter_html = ""
        if categories:
            btns = [
                '<button type="button" class="dj-data-card-grid__filter dj-data-card-grid__filter--active" data-filter="all">All</button>'
            ]
            for cat in categories:
                e_cat = html.escape(cat)
                btns.append(
                    f'<button type="button" class="dj-data-card-grid__filter" '
                    f'data-filter="{e_cat}">{e_cat}</button>'
                )
            filter_html = f'<div class="dj-data-card-grid__filters">{"".join(btns)}</div>'

        cards = []
        for it in self.items:
            if not isinstance(it, dict):
                continue
            title = html.escape(str(it.get("title", "")))
            desc = html.escape(str(it.get("description", "")))
            cat = html.escape(str(it.get(fk, "")))
            image = it.get("image", "")

            img_html = ""
            if image:
                e_img = html.escape(str(image))
                img_html = f'<img src="{e_img}" alt="{title}" class="dj-data-card-grid__img">'

            click_attr = ""
            if e_event:
                click_attr = f' dj-click="{e_event}" dj-value-title="{title}"'

            cards.append(
                f'<div class="dj-data-card-grid__card" data-category="{cat}" '
                f'role="listitem"{click_attr}>'
                f"{img_html}"
                f'<div class="dj-data-card-grid__body">'
                f'<h3 class="dj-data-card-grid__title">{title}</h3>'
                f'<p class="dj-data-card-grid__desc">{desc}</p>'
                f"</div></div>"
            )

        style = f"--dj-data-card-grid-cols:{cols}"

        return (
            f'<div class="{cls}" style="{style}">'
            f"{filter_html}"
            f'<div class="dj-data-card-grid__grid" role="list">'
            f"{''.join(cards)}</div></div>"
        )
