"""DescriptionList component."""

import html

from djust import Component


class DescriptionList(Component):
    """Description list (term/detail pairs) component.

    Args:
        items: list of dicts with keys: term, detail
        layout: vertical, horizontal"""

    def __init__(
        self,
        items: list = None,
        layout: str = "vertical",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            layout=layout,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.layout = layout
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the descriptionlist HTML."""
        items = self.items or []
        cls = "dj-dl"
        if self.layout == "horizontal":
            cls += " dj-dl--horizontal"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        dl_items = []
        for item in items:
            if isinstance(item, dict):
                term = html.escape(str(item.get("term", "")))
                detail = html.escape(str(item.get("detail", "")))
                dl_items.append(
                    f'<div class="dj-dl__pair">'
                    f'<dt class="dj-dl__term">{term}</dt>'
                    f'<dd class="dj-dl__detail">{detail}</dd>'
                    f"</div>"
                )
        return f'<dl class="{cls}">{"".join(dl_items)}</dl>'
