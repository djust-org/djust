"""Breadcrumb Dropdown component — breadcrumb with overflow collapse."""

import html
from typing import List, Optional

from djust import Component


class BreadcrumbDropdown(Component):
    """Breadcrumb navigation with overflow collapse into dropdown.

    When there are more items than max_visible, middle items are collapsed
    into an ellipsis dropdown menu.

    Usage in a LiveView::

        self.crumbs = BreadcrumbDropdown(
            items=[
                {"label": "Home", "url": "/"},
                {"label": "Products", "url": "/products"},
                {"label": "Category", "url": "/products/cat"},
                {"label": "Item"},
            ],
        )

    In template::

        {{ crumbs|safe }}

    CSS Custom Properties::

        --dj-breadcrumb-gap: item gap (default: 0.5rem)
        --dj-breadcrumb-separator-color: separator color
        --dj-breadcrumb-link-color: link color
        --dj-breadcrumb-current-color: current item color

    Args:
        items: List of dicts with label, optional url.
        max_visible: Max items before collapsing (default: 4).
        separator: Separator character (default: "/").
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        items: Optional[List[dict]] = None,
        max_visible: int = 4,
        separator: str = "/",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            max_visible=max_visible,
            separator=separator,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.max_visible = max_visible
        self.separator = separator
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-breadcrumb"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        if not isinstance(self.items, list):
            return f'<nav class="{cls}" aria-label="Breadcrumb"><ol class="dj-breadcrumb__list"></ol></nav>'

        e_sep = html.escape(self.separator)

        try:
            max_vis = int(self.max_visible)
        except (ValueError, TypeError):
            max_vis = 4

        items = self.items
        need_collapse = len(items) > max_vis and max_vis >= 2

        parts = []
        if need_collapse:
            # Show first item, ellipsis dropdown, last (max_vis - 2) items
            visible_start = [items[0]]
            collapsed = items[1 : -(max_vis - 1)]
            visible_end = items[-(max_vis - 1) :]

            parts.append(self._render_item(visible_start[0], False, e_sep))
            # Dropdown for collapsed
            dropdown_items = []
            for it in collapsed:
                if not isinstance(it, dict):
                    continue
                label = html.escape(str(it.get("label", "")))
                url = it.get("url", "")
                if url:
                    e_url = html.escape(str(url))
                    dropdown_items.append(
                        f'<li class="dj-breadcrumb__dropdown-item">'
                        f'<a href="{e_url}">{label}</a></li>'
                    )
                else:
                    dropdown_items.append(f'<li class="dj-breadcrumb__dropdown-item">{label}</li>')
            parts.append(
                f'<li class="dj-breadcrumb__item dj-breadcrumb__ellipsis">'
                f'<span class="dj-breadcrumb__separator" aria-hidden="true">{e_sep}</span>'
                f'<button type="button" class="dj-breadcrumb__toggle" '
                f'aria-expanded="false" aria-label="Show more">&hellip;</button>'
                f'<ul class="dj-breadcrumb__dropdown">{"".join(dropdown_items)}</ul>'
                f"</li>"
            )
            for i, it in enumerate(visible_end):
                is_last = i == len(visible_end) - 1
                parts.append(self._render_item(it, is_last, e_sep))
        else:
            for i, it in enumerate(items):
                is_last = i == len(items) - 1
                parts.append(self._render_item(it, is_last, e_sep))

        return (
            f'<nav class="{cls}" aria-label="Breadcrumb">'
            f'<ol class="dj-breadcrumb__list">{"".join(parts)}</ol>'
            f"</nav>"
        )

    @staticmethod
    def _render_item(item, is_last, separator):
        if not isinstance(item, dict):
            return ""
        label = html.escape(str(item.get("label", "")))
        url = item.get("url", "")

        # Separators are rendered between items by the caller; no per-item separator here.

        aria = ' aria-current="page"' if is_last else ""

        if url and not is_last:
            e_url = html.escape(str(url))
            content = f'<a href="{e_url}" class="dj-breadcrumb__link">{label}</a>'
        else:
            content = f'<span class="dj-breadcrumb__current">{label}</span>'

        return f'<li class="dj-breadcrumb__item"{aria}>{content}</li>'
