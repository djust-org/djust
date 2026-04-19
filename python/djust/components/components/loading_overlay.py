"""LoadingOverlay component."""

import html
from djust import Component


class LoadingOverlay(Component):
    """Loading overlay component.

    Args:
        content: wrapped content (pre-rendered HTML)
        active: whether overlay is shown
        text: loading message text
        spinner_size: sm, md, lg"""

    def __init__(
        self,
        content: str = "",
        active: bool = False,
        text: str = "",
        spinner_size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            active=active,
            text=text,
            spinner_size=spinner_size,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.active = active
        self.text = text
        self.spinner_size = spinner_size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the loadingoverlay HTML."""
        cls = "dj-loading-overlay-wrap"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        overlay_html = ""
        if self.active:
            e_text = html.escape(self.text)
            text_html = (
                f'<span class="dj-loading-overlay__text">{e_text}</span>' if self.text else ""
            )
            e_size = html.escape(self.spinner_size)
            overlay_html = (
                f'<div class="dj-loading-overlay">'
                f'<div class="dj-loading-overlay__spinner dj-loading-overlay__spinner--{e_size}"></div>'
                f"{text_html}"
                f"</div>"
            )
        return f'<div class="{cls}">{self.content}{overlay_html}</div>'
