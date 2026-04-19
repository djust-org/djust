"""Icon component."""

import html
from djust import Component


class Icon(Component):
    """Icon component wrapper.

    Args:
        name: icon name
        size: xs, sm, md, lg
        icon_set: icon set name (default heroicons)"""

    def __init__(
        self,
        name: str = "",
        size: str = "md",
        icon_set: str = "heroicons",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            size=size,
            icon_set=icon_set,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.size = size
        self.icon_set = icon_set
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the icon HTML."""
        e_name = html.escape(self.name)
        e_size = html.escape(self.size)
        e_set = html.escape(self.icon_set)
        sizes = {"xs": "12", "sm": "16", "md": "20", "lg": "24"}
        px = sizes.get(self.size, "20")
        cls = f"dj-icon dj-icon--{e_size}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        return (
            f'<span class="{cls}" data-icon="{e_name}" data-set="{e_set}" '
            f'aria-hidden="true"></span>'
        )
