"""Avatar component."""

import html
from djust import Component


class Avatar(Component):
    """Avatar component with optional status indicator.

    Args:
        src: image URL
        alt: alt text
        initials: fallback initials
        size: xs, sm, md, lg, xl
        status: online, offline, busy, away"""

    def __init__(
        self,
        src: str = "",
        alt: str = "",
        initials: str = "",
        size: str = "md",
        status: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            src=src,
            alt=alt,
            initials=initials,
            size=size,
            status=status,
            custom_class=custom_class,
            **kwargs,
        )
        self.src = src
        self.alt = alt
        self.initials = initials
        self.size = size
        self.status = status
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the avatar HTML."""
        e_size = html.escape(self.size)
        cls = f"dj-avatar dj-avatar-{e_size}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        if self.src:
            e_src = html.escape(self.src)
            e_alt = html.escape(self.alt)
            inner = f'<img class="dj-avatar-img" src="{e_src}" alt="{e_alt}">'
        else:
            initials = self.initials or (self.alt[:2].upper() if self.alt else "")
            inner = f'<span class="dj-avatar-initials">{html.escape(initials)}</span>'
        status_html = ""
        if self.status:
            e_status = html.escape(self.status)
            status_html = f'<span class="dj-avatar-status dj-avatar-status-{e_status}"></span>'
        return f'<div class="{cls}">{inner}{status_html}</div>'
