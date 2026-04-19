"""Rating component."""

import html
from djust import Component


class Rating(Component):
    """Star rating component.

    Args:
        value: current rating value (0-max_stars, supports half stars)
        max_stars: maximum number of stars
        readonly: if True, stars are not clickable
        event: dj-click event name
        size: sm, md, lg"""

    def __init__(
        self,
        value: float = 0,
        max_stars: int = 5,
        readonly: bool = False,
        event: str = "set_rating",
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            value=value,
            max_stars=max_stars,
            readonly=readonly,
            event=event,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.value = value
        self.max_stars = max_stars
        self.readonly = readonly
        self.event = event
        self.size = size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the rating HTML."""
        size_cls = f" rating-{html.escape(self.size)}" if self.size != "md" else ""
        cls = f"rating{size_cls}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_event = html.escape(self.event)
        parts = []
        for i in range(1, self.max_stars + 1):
            if i <= self.value:
                star_cls = "rating-star rating-star-full"
            elif i - 0.5 <= self.value:
                star_cls = "rating-star rating-star-half"
            else:
                star_cls = "rating-star rating-star-empty"
            if self.readonly:
                parts.append(f'<span class="{star_cls}">&#9733;</span>')
            else:
                parts.append(
                    f'<button class="{star_cls}" dj-click="{e_event}" '
                    f'data-value="{i}">&#9733;</button>'
                )
        return f'<div class="{cls}">{"".join(parts)}</div>'
