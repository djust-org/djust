"""Image Cropper component — drag-to-crop with aspect ratio lock."""

import html

from djust import Component


class ImageCropper(Component):
    """Drag-to-crop image component with optional aspect ratio lock.

    Uses ``dj-hook="ImageCropper"`` for client-side cropping interactions.

    Usage in a LiveView::

        self.cropper = ImageCropper(
            src="/uploads/photo.jpg",
            crop_event="save_crop",
            aspect_ratio="16/9",
        )

    In template::

        {{ cropper|safe }}

    Args:
        src: image URL to crop
        crop_event: djust event fired with crop data (x, y, width, height)
        aspect_ratio: lock ratio (e.g. "1/1", "16/9", "4/3"), empty = free
        min_width: minimum crop width in px (default 50)
        min_height: minimum crop height in px (default 50)
        disabled: disable cropping (default False)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        src: str = "",
        crop_event: str = "save_crop",
        aspect_ratio: str = "",
        min_width: int = 50,
        min_height: int = 50,
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            src=src,
            crop_event=crop_event,
            aspect_ratio=aspect_ratio,
            min_width=min_width,
            min_height=min_height,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.src = src
        self.crop_event = crop_event
        self.aspect_ratio = aspect_ratio
        self.min_width = min_width
        self.min_height = min_height
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-image-cropper"]
        if self.disabled:
            classes.append("dj-image-cropper--disabled")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_src = html.escape(self.src)
        e_event = html.escape(self.crop_event)
        e_ratio = html.escape(self.aspect_ratio) if self.aspect_ratio else ""

        ratio_attr = f' data-aspect-ratio="{e_ratio}"' if e_ratio else ""

        return (
            f'<div class="{class_str}" dj-hook="ImageCropper" '
            f'data-crop-event="{e_event}" '
            f'data-min-width="{int(self.min_width)}" '
            f'data-min-height="{int(self.min_height)}"{ratio_attr}>'
            f'<div class="dj-image-cropper__canvas">'
            f'<img class="dj-image-cropper__image" src="{e_src}" alt="Image to crop" draggable="false">'
            f'<div class="dj-image-cropper__overlay"></div>'
            f'<div class="dj-image-cropper__selection"></div>'
            f"</div>"
            f'<div class="dj-image-cropper__actions">'
            f'<button class="dj-image-cropper__crop-btn" type="button">Crop</button>'
            f'<button class="dj-image-cropper__reset-btn" type="button">Reset</button>'
            f"</div>"
            f"</div>"
        )
