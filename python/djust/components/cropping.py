"""Validate a crop box from the browser and crop on the server (#2985).

The ImageCropper component sends ONLY ``{x, y, width, height}``, in pixels of the
image's natural size; the image itself never leaves the server. The values come
from the browser, so they are untrusted: a crafted client can send ``NaN``,
negative sizes, a box far outside the image or a number with a million digits.
This module is what a ``crop_event`` handler uses::

    from djust.components.cropping import CropError, crop_image

    @event_handler()
    def save_crop(self, x=None, y=None, width=None, height=None, **kwargs):
        try:
            cropped = crop_image(self.photo.file, x, y, width, height)
        except CropError:
            self.error = "That crop could not be applied."  # fixed text: never echo the values
            return
        self.photo.thumbnail.save(f"{uuid4().hex}.{cropped.extension}", ContentFile(cropped.data))

:func:`crop_box` is the validation alone (no imaging library): it returns
integer pixel bounds clamped to the image, or raises :class:`CropError`.
:func:`crop_image` (Pillow, imported only when called) opens the image, refuses
one that is too large **before** decoding any pixel, applies the EXIF
orientation (the browser shows the image upright, so the box is in the upright
image's pixels, not in the raw sensor layout), crops, strips the metadata and
re-encodes in the same format. Pillow is not a djust dependency.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

__all__ = [
    "CropBox",
    "CropError",
    "CroppedImage",
    "crop_box",
    "crop_image",
    "parse_aspect_ratio",
]

# No sane image side is longer; anything bigger is garbage or an attack.
_MAX_COORDINATE = 10_000_000


class CropError(ValueError):
    """The crop box or the image is not acceptable.

    The message is a fixed phrase naming the rule that failed; it never contains
    any of the values, so it is safe to log.
    """


class CropBox(NamedTuple):
    """Integer pixel bounds, as Pillow's ``Image.crop`` takes them."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class CroppedImage(NamedTuple):
    """A cropped, re-encoded image."""

    data: bytes
    format: str
    width: int
    height: int

    @property
    def extension(self) -> str:
        return {"JPEG": "jpg"}.get(self.format, self.format.lower())


def _number(value: Any) -> float:
    # bool is an int: ``True`` is not a coordinate. Strings are refused too: the
    # component sends numbers, and "1e999" or " 5 " is not one.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CropError("crop value is not a number")
    if isinstance(value, int) and abs(value) > _MAX_COORDINATE:
        # Before float(): a huge int would raise OverflowError there.
        raise CropError("crop value is out of range")
    number = float(value)
    if not math.isfinite(number):
        raise CropError("crop value is not finite")
    if abs(number) > _MAX_COORDINATE:
        raise CropError("crop value is out of range")
    return number


def crop_box(
    x: Any,
    y: Any,
    width: Any,
    height: Any,
    image_size: tuple[int, int],
    *,
    clamp: bool = True,
    min_size: int = 1,
) -> CropBox:
    """The pixel bounds of a crop, validated against an image of ``image_size``.

    Every value must be a finite number (not a bool, not a string) of sane
    magnitude; ``width`` and ``height`` must be positive. Fractions are rounded.
    With ``clamp=True`` (the default) a box that sticks out of the image is cut
    back to it (a browser rounding error should not fail a crop); with
    ``clamp=False`` it is refused. Either way the result must be at least
    ``min_size`` pixels each way, or :class:`CropError` is raised.
    """
    image_w, image_h = int(image_size[0]), int(image_size[1])
    if image_w < 1 or image_h < 1:
        raise CropError("image has no size")
    left, top = round(_number(x)), round(_number(y))
    box_w, box_h = round(_number(width)), round(_number(height))
    if box_w < 1 or box_h < 1:
        raise CropError("crop size is not positive")
    right, bottom = left + box_w, top + box_h
    if clamp:
        left, top = max(0, left), max(0, top)
        right, bottom = min(image_w, right), min(image_h, bottom)
    elif left < 0 or top < 0 or right > image_w or bottom > image_h:
        raise CropError("crop is outside the image")
    if right - left < min_size or bottom - top < min_size:
        raise CropError("crop is outside the image or too small")
    return CropBox(left, top, right, bottom)


def parse_aspect_ratio(value: Any) -> float | None:
    """``"16/9"``, ``"4:3"`` or ``"1.5"`` as width / height, or ``None`` (free)."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(":", "/")
    if not text:
        return None
    try:
        if "/" in text:
            numerator, _, denominator = text.partition("/")
            ratio = float(numerator) / float(denominator)
        else:
            ratio = float(text)
    except (ValueError, ZeroDivisionError):
        return None
    return ratio if math.isfinite(ratio) and 0.05 <= ratio <= 20 else None


def crop_image(
    source: Any,
    x: Any,
    y: Any,
    width: Any,
    height: Any,
    *,
    max_pixels: int = 40_000_000,
    clamp: bool = True,
    min_size: int = 1,
    aspect_ratio: Any = None,
    ratio_tolerance: float = 0.02,
) -> CroppedImage:
    """Crop the image at ``source`` (a path or a binary file object) to the box.

    The image is judged before it is decoded: its header gives the dimensions,
    and one above ``max_pixels`` is refused (Pillow's own decompression-bomb
    error is caught too; raise ``Image.MAX_IMAGE_PIXELS`` yourself if you set
    ``max_pixels`` above Pillow's limit). Only the first frame of an animated
    image is cropped. The EXIF orientation is applied first, because the
    browser shows the upright image and the box is in its pixels. Metadata is
    not copied to the result. When ``aspect_ratio`` is given the cropped box
    must match it within ``ratio_tolerance`` (a relative error), which is what
    the component's locked ratio guarantees up to rounding.

    Needs Pillow; raises :class:`ImportError` without it and :class:`CropError`
    for anything wrong with the image or the box.
    """
    from io import BytesIO

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError("djust.components.cropping.crop_image needs Pillow") from exc

    try:
        with Image.open(source) as opened:
            # The header alone gives the size: nothing is decoded for an image over the limit.
            if opened.width * opened.height > max_pixels:
                raise CropError("image is too large")
            fmt = opened.format or "PNG"
            if fmt not in ("PNG", "JPEG", "WEBP", "GIF"):
                fmt = "PNG"
            upright = ImageOps.exif_transpose(opened)
            box = crop_box(x, y, width, height, upright.size, clamp=clamp, min_size=min_size)
            if aspect_ratio is not None:
                wanted = parse_aspect_ratio(aspect_ratio)
                if wanted and abs(box.width / box.height - wanted) / wanted > ratio_tolerance:
                    raise CropError("crop does not match the aspect ratio")
            cropped = upright.crop(box)
            out = BytesIO()
            cropped.save(out, format=fmt)
    except CropError:
        raise
    except Image.DecompressionBombError:
        # Pillow's own limit (Image.MAX_IMAGE_PIXELS), when max_pixels is set above it.
        raise CropError("image is too large") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise CropError("image could not be read") from None
    return CroppedImage(out.getvalue(), fmt, box.width, box.height)
