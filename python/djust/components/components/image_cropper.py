"""Image Cropper component — drag-to-crop with aspect ratio lock."""

import html

from djust import Component
from typing import Any, NamedTuple

_HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
_KEYS_HELP = (
    "Arrow keys move the crop area, Alt with an arrow resizes it, Shift makes the steps ten "
    "times larger, Enter crops and Escape resets."
)


class CropperExtras(NamedTuple):
    """The additions to the markup the component always had (see :func:`cropper_extras`)."""

    selection_attrs: str  # attributes of the selection element
    handles: str  # the eight resize handles, inside the selection
    status: str  # the live region after the actions


def cropper_extras() -> CropperExtras:
    """The markup the hook needs beyond what the component always rendered.

    Constant markup, shared by the component class, the Django tag and the Rust
    tag handler so the three cannot drift.
    """
    return CropperExtras(
        selection_attrs=(
            f' tabindex="0" role="group" aria-label="Crop area" aria-description="{_KEYS_HELP}"'
        ),
        handles="".join(
            f'<span class="dj-image-cropper__handle dj-image-cropper__handle--{h}" '
            f'data-handle="{h}" aria-hidden="true"></span>'
            for h in _HANDLES
        ),
        status='<div class="dj-image-cropper__status" role="status" aria-live="polite"></div>',
    )


class ImageCropper(Component):
    """Drag-to-crop image component with optional aspect ratio lock.

    Uses ``dj-hook="ImageCropper"`` for client-side cropping interactions: drag
    the crop area to move it, drag a handle (corner or edge) to resize it, drag
    on the image to draw a new one; the keyboard does the same (see below). Load
    ``djust_components/image-cropper.js`` after the djust client.

    Usage in a LiveView::

        self.cropper = ImageCropper(
            src="/uploads/photo.jpg",
            crop_event="save_crop",
            aspect_ratio="16/9",
        )

    In template::

        {{ cropper|safe }}

    The image comes from ``src`` (a URL or a ``data:`` URL the server provides:
    nothing is uploaded here). **Crop sends ONLY the box**, ``{x, y, width,
    height}`` as whole numbers of pixels of the image's *natural* size (what the
    file contains, not what the screen shows), and the server does the cropping.
    The numbers come from the browser, so treat them as untrusted:
    ``djust.components.cropping`` validates them and, with Pillow, crops::

        from djust.components.cropping import CropError, crop_image

        @event_handler()
        def save_crop(self, x=None, y=None, width=None, height=None, **kwargs):
            try:
                cropped = crop_image(self.photo.file, x, y, width, height, aspect_ratio="16/9")
            except CropError:
                self.error = "That crop could not be applied."  # never echo the values
                return
            self.photo.cropped.save(f"{uuid4().hex}.{cropped.extension}", ContentFile(cropped.data))

    ``crop_box`` alone (no Pillow) rejects non-numbers, ``NaN``/infinity, absurd
    magnitudes and non-positive sizes, clamps a box that sticks out of the image
    and enforces a minimum size; ``crop_image`` also refuses an image above
    ``max_pixels`` before decoding it (decompression bombs) and applies the EXIF
    orientation first, because the browser shows the upright image: a box taken
    from the screen would otherwise be applied to the sensor's layout.

    Aspect ratio: ``aspect_ratio="16/9"`` (also ``"4:3"`` or ``"1.5"``) locks the
    box to it while moving and resizing, with a starting box of that shape;
    empty is free. ``min_width`` / ``min_height`` are in natural pixels (a box
    never gets smaller; an image smaller than that is selected whole).

    Keyboard: focus the crop area (a labelled group), then the arrow keys move
    it by one screen pixel (Shift: ten times that), Alt with an arrow resizes it
    from its top left corner, Enter crops, Escape resets. Every change is
    announced with the box in image pixels. The box is kept in natural pixels,
    so it follows the image when the window is resized, and touch drags work
    (the page does not scroll while one is in progress).

    Args:
        src: image URL to crop
        crop_event: djust event fired with crop data (x, y, width, height)
        aspect_ratio: lock ratio (e.g. "1/1", "16/9", "4/3"), empty = free
        min_width: minimum crop width in px (default 50)
        min_height: minimum crop height in px (default 50)
        disabled: disable cropping (default False)
        custom_class: additional CSS classes
        alt: alternative text of the image (default "Image to crop")
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
        alt: str = "Image to crop",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            src=src,
            crop_event=crop_event,
            aspect_ratio=aspect_ratio,
            min_width=min_width,
            min_height=min_height,
            disabled=disabled,
            custom_class=custom_class,
            alt=alt,
            **kwargs,
        )
        self.src = src
        self.crop_event = crop_event
        self.aspect_ratio = aspect_ratio
        self.min_width = min_width
        self.min_height = min_height
        self.disabled = disabled
        self.custom_class = custom_class
        self.alt = alt

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
        e_alt = html.escape(str(self.alt))
        extras = cropper_extras()

        return (
            f'<div class="{class_str}" dj-hook="ImageCropper" '
            f'data-crop-event="{e_event}" '
            f'data-min-width="{int(self.min_width)}" '
            f'data-min-height="{int(self.min_height)}"{ratio_attr}>'
            f'<div class="dj-image-cropper__canvas">'
            f'<img class="dj-image-cropper__image" src="{e_src}" alt="{e_alt}" draggable="false">'
            f'<div class="dj-image-cropper__overlay"></div>'
            f'<div class="dj-image-cropper__selection"{extras.selection_attrs}>{extras.handles}</div>'
            f"</div>"
            f'<div class="dj-image-cropper__actions">'
            f'<button class="dj-image-cropper__crop-btn" type="button">Crop</button>'
            f'<button class="dj-image-cropper__reset-btn" type="button">Reset</button>'
            f"</div>"
            f"{extras.status}"
            f"</div>"
        )
