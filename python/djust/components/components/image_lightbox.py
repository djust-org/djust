"""Image Lightbox component — full-screen image viewer overlay."""

import html
from typing import Optional

from djust import Component


class ImageLightbox(Component):
    """Full-screen image viewer overlay with navigation.

    Uses ``dj-hook="ImageLightbox"`` for client-side keyboard/swipe nav.

    Usage in a LiveView::

        self.lightbox = ImageLightbox(
            images=[
                {"src": "/img/1.jpg", "alt": "Photo 1"},
                {"src": "/img/2.jpg", "alt": "Photo 2"},
            ],
            active=0,
            open=True,
        )

    In template::

        {{ lightbox|safe }}

    Args:
        images: list of dicts with ``src`` and optional ``alt``/``caption``
        active: index of currently displayed image (default 0)
        open: whether lightbox is visible (default False)
        close_event: djust event to close (default "close_lightbox")
        navigate_event: djust event on nav (default "lightbox_navigate")
        show_counter: show "N of M" counter (default True)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        images: Optional[list] = None,
        active: int = 0,
        open: bool = False,
        close_event: str = "close_lightbox",
        navigate_event: str = "lightbox_navigate",
        show_counter: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            images=images,
            active=active,
            open=open,
            close_event=close_event,
            navigate_event=navigate_event,
            show_counter=show_counter,
            custom_class=custom_class,
            **kwargs,
        )
        self.images = images or []
        self.active = active
        self.open = open
        self.close_event = close_event
        self.navigate_event = navigate_event
        self.show_counter = show_counter
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        if not self.open:
            return ""

        classes = ["dj-lightbox"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_close = html.escape(self.close_event)
        e_nav = html.escape(self.navigate_event)

        total = len(self.images)
        idx = max(0, min(self.active, total - 1)) if total else 0

        # Current image
        img_html = ""
        caption_html = ""
        if self.images and 0 <= idx < total:
            img = self.images[idx]
            if isinstance(img, dict):
                e_src = html.escape(str(img.get("src", "")))
                e_alt = html.escape(str(img.get("alt", "")))
                caption = img.get("caption", "")
                img_html = f'<img class="dj-lightbox__image" src="{e_src}" alt="{e_alt}">'
                if caption:
                    caption_html = (
                        f'<p class="dj-lightbox__caption">{html.escape(str(caption))}</p>'
                    )

        # Navigation
        prev_btn = (
            (
                f'<button class="dj-lightbox__prev" dj-click="{e_nav}" '
                f'data-value="{idx - 1}" aria-label="Previous">'
                f"&#8249;</button>"
            )
            if total > 1
            else ""
        )

        next_btn = (
            (
                f'<button class="dj-lightbox__next" dj-click="{e_nav}" '
                f'data-value="{idx + 1}" aria-label="Next">'
                f"&#8250;</button>"
            )
            if total > 1
            else ""
        )

        counter = ""
        if self.show_counter and total > 1:
            counter = f'<span class="dj-lightbox__counter">{idx + 1} of {total}</span>'

        return (
            f'<div class="{class_str}" dj-hook="ImageLightbox" '
            f'data-close-event="{e_close}" data-navigate-event="{e_nav}" '
            f'role="dialog" aria-modal="true">'
            f'<div class="dj-lightbox__backdrop" dj-click="{e_close}"></div>'
            f'<button class="dj-lightbox__close" dj-click="{e_close}" '
            f'aria-label="Close">&times;</button>'
            f"{prev_btn}"
            f'<div class="dj-lightbox__stage">{img_html}{caption_html}</div>'
            f"{next_btn}"
            f"{counter}"
            f"</div>"
        )
