"""NavMenu component."""

import html

from djust import Component


class NavMenu(Component):
    """Horizontal navigation menu component.

    Args:
        items: list of dicts with keys: label, href, active (bool)
        brand: brand/logo text
        brand_href: brand link URL
        content: pre-rendered HTML content (alternative to items)"""

    def __init__(
        self,
        items: list = None,
        brand: str = "",
        brand_href: str = "/",
        content: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            brand=brand,
            brand_href=brand_href,
            content=content,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.brand = brand
        self.brand_href = brand_href
        self.content = content
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the navmenu HTML."""
        cls = "dj-nav"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        brand_html = ""
        if self.brand:
            e_brand = html.escape(self.brand)
            e_href = html.escape(self.brand_href)
            brand_html = f'<a class="dj-nav__brand" href="{e_href}">{e_brand}</a>'
        if self.items:
            items_html = ""
            for item in self.items:
                if not isinstance(item, dict):
                    continue
                label = html.escape(str(item.get("label", "")))
                href = html.escape(str(item.get("href", "#")))
                active = item.get("active", False)
                active_cls = " dj-nav__item--active" if active else ""
                items_html += (
                    f'<li class="dj-nav__item{active_cls}">'
                    f'<a class="dj-nav__link" href="{href}">{label}</a></li>'
                )
            list_html = f'<ul class="dj-nav__list">{items_html}</ul>'
        else:
            list_html = self.content
        return f'<nav class="{cls}">{brand_html}{list_html}</nav>'
