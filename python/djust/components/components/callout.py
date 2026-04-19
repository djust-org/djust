"""Callout component."""

import html
from djust import Component


class Callout(Component):
    """Callout/blockquote component.

    Args:
        content: callout body (pre-rendered HTML)
        variant: default, info, warning, danger, success
        title: optional title text
        icon: optional icon text"""

    def __init__(
        self,
        content: str = "",
        variant: str = "default",
        title: str = "",
        icon: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            variant=variant,
            title=title,
            icon=icon,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.variant = variant
        self.title = title
        self.icon = icon
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the callout HTML."""
        e_variant = html.escape(self.variant)
        cls = "dj-callout"
        if self.variant != "default":
            cls += f" dj-callout--{e_variant}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        icon_html = (
            f'<span class="dj-callout__icon">{html.escape(self.icon)}</span>' if self.icon else ""
        )
        title_html = (
            f'<div class="dj-callout__title">{html.escape(self.title)}</div>' if self.title else ""
        )
        return (
            f'<div class="{cls}">'
            f"{icon_html}"
            f'<div class="dj-callout__body">'
            f"{title_html}"
            f'<div class="dj-callout__content">{self.content}</div>'
            f"</div></div>"
        )
