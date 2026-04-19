"""Breadcrumb component."""

import html

from djust import Component


class Breadcrumb(Component):
    """Breadcrumb navigation component.

    Args:
        items: list of dicts with keys: label, url, active (bool)"""

    def __init__(
        self,
        items: list = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the breadcrumb HTML."""
        items = self.items or []
        cls = "breadcrumb"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        if not items:
            return f'<nav class="{cls}"></nav>'
        parts = []
        for i, item in enumerate(items):
            if isinstance(item, dict):
                lbl = html.escape(str(item.get("label", "")))
                url = html.escape(str(item.get("url", "")))
                active = item.get("active", False)
            else:
                lbl = html.escape(str(item))
                url = ""
                active = False
            if active or not url:
                parts.append(f'<span class="breadcrumb-item breadcrumb-active">{lbl}</span>')
            else:
                parts.append(f'<a class="breadcrumb-item breadcrumb-link" href="{url}">{lbl}</a>')
            if i < len(items) - 1:
                parts.append('<span class="breadcrumb-separator">&#8250;</span>')
        return f'<nav class="{cls}">{"".join(parts)}</nav>'
