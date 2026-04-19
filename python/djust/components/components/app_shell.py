"""AppShell component."""

import html
from djust import Component


class AppShell(Component):
    """Application shell layout component.

    Args:
        sidebar: sidebar content (pre-rendered HTML)
        header: header content (pre-rendered HTML)
        content: main content (pre-rendered HTML)
        variant: default, compact"""

    def __init__(
        self,
        sidebar: str = "",
        header: str = "",
        content: str = "",
        variant: str = "default",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            sidebar=sidebar,
            header=header,
            content=content,
            variant=variant,
            custom_class=custom_class,
            **kwargs,
        )
        self.sidebar = sidebar
        self.header = header
        self.content = content
        self.variant = variant
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the appshell HTML."""
        cls = "dj-app-shell"
        if self.variant != "default":
            cls += f" dj-app-shell--{html.escape(self.variant)}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        sidebar_html = (
            f'<aside class="dj-app-shell__sidebar">{self.sidebar}</aside>' if self.sidebar else ""
        )
        header_html = (
            f'<header class="dj-app-shell__header">{self.header}</header>' if self.header else ""
        )
        return (
            f'<div class="{cls}">'
            f"{sidebar_html}"
            f'<div class="dj-app-shell__main">'
            f"{header_html}"
            f'<main class="dj-app-shell__content">{self.content}</main>'
            f"</div></div>"
        )
