"""PageHeader component."""

import html
from djust import Component


class PageHeader(Component):
    """Page-level header with title, subtitle, and optional actions.

    Args:
        title: page title
        subtitle: subtitle text
        description: description text
        actions: pre-rendered HTML for action buttons (caller's responsibility)"""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        description: str = "",
        actions: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            title=title,
            subtitle=subtitle,
            description=description,
            actions=actions,
            custom_class=custom_class,
            **kwargs,
        )
        self.title = title
        self.subtitle = subtitle
        self.description = description
        self.actions = actions
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the pageheader HTML."""
        cls = "dj-page-header"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_title = html.escape(self.title)
        e_subtitle = html.escape(self.subtitle)
        e_description = html.escape(self.description)
        title_html = f'<h1 class="dj-page-header__title">{e_title}</h1>' if self.title else ""
        subtitle_html = (
            f'<p class="dj-page-header__subtitle">{e_subtitle}</p>' if self.subtitle else ""
        )
        desc_html = (
            f'<p class="dj-page-header__description">{e_description}</p>'
            if self.description
            else ""
        )
        actions_html = (
            f'<div class="dj-page-header__actions">{self.actions}</div>' if self.actions else ""
        )
        return (
            f'<header class="{cls}">'
            f'<div class="dj-page-header__row">'
            f'<div class="dj-page-header__text">{title_html}{subtitle_html}{desc_html}</div>'
            f"{actions_html}"
            f"</div></header>"
        )
