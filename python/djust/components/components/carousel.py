"""Carousel component."""

import html

from djust import Component
from typing import Any, Optional


class Carousel(Component):
    """Image carousel/slideshow component.

    Args:
        images: list of dicts with keys: src, alt, caption
        active: 0-based index of active slide
        prev_event, next_event, go_event: dj-click events"""

    def __init__(
        self,
        images: Optional[list] = None,
        active: int = 0,
        prev_event: str = "carousel_prev",
        next_event: str = "carousel_next",
        go_event: str = "carousel_go",
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            images=images,
            active=active,
            prev_event=prev_event,
            next_event=next_event,
            go_event=go_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.images = images or []
        self.active = active
        self.prev_event = prev_event
        self.next_event = next_event
        self.go_event = go_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the carousel HTML."""
        images = self.images or []
        if not images:
            return '<div class="carousel carousel-empty"></div>'
        cls = "carousel"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        slides = ""
        dots = ""
        for i, img in enumerate(images):
            if isinstance(img, dict):
                src = html.escape(str(img.get("src", "")))
                alt = html.escape(str(img.get("alt", f"Slide {i + 1}")))
                caption = img.get("caption", "")
            else:
                src = html.escape(str(img))
                alt = f"Slide {i + 1}"
                caption = ""
            active_cls = " carousel-slide-active" if i == self.active else ""
            caption_html = (
                f'<div class="carousel-caption">{html.escape(caption)}</div>' if caption else ""
            )
            slides += f'<div class="carousel-slide{active_cls}"><img src="{src}" alt="{alt}" class="carousel-img">{caption_html}</div>'
            dot_cls = " carousel-dot-active" if i == self.active else ""
            dots += f'<button class="carousel-dot{dot_cls}" {self.event_attrs(self.go_event, value=i)}></button>'
        return (
            f'<div class="{cls}">'
            f'<div class="carousel-track">{slides}</div>'
            f'<button class="carousel-btn carousel-btn-prev" {self.event_attrs(self.prev_event)}>&#8249;</button>'
            f'<button class="carousel-btn carousel-btn-next" {self.event_attrs(self.next_event)}>&#8250;</button>'
            f'<div class="carousel-dots">{dots}</div>'
            f"</div>"
        )
