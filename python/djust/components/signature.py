"""Validate and decode the PNG data URL a SignaturePad sends (#2985).

The browser is not trusted: the ``signature`` value of a ``save_event`` handler
is whatever a client chose to send. :func:`decode_signature_data_url` checks it
the way a PNG decoder would have to, without ever decoding it into pixels, so a
hostile value costs a bounded amount of memory and time:

* the value is a string with the exact ``data:image/png;base64,`` prefix and its
  length is checked **before** any base64 work;
* the base64 is strict (alphabet and padding), the decoded size is capped;
* the PNG signature is the eight magic bytes;
* every chunk is walked: lengths stay inside the data, every CRC matches, the
  first chunk is a sane ``IHDR`` (non-interlaced, a valid colour type and bit
  depth, width, height and pixel count within the limits), the last is a
  ``IEND`` and nothing follows it;
* the ``IDAT`` stream is inflated with an output limit equal to what the header
  promises (a decompression bomb with a small header cannot expand), must end
  exactly there, and every scanline starts with a valid filter byte.

It needs no imaging library. What comes back is the **original bytes** (not a
re-encoding) with the validated dimensions. Typical use::

    from djust.components.signature import SignatureError, decode_signature_data_url

    @event_handler()
    def save_signature(self, signature="", **kwargs):
        try:
            image = decode_signature_data_url(signature)
        except SignatureError:
            self.error = "That signature could not be read."  # fixed text: never echo the value
            return
        # image.data is a valid PNG of image.width x image.height pixels
        document.signature.save(f"{uuid4().hex}.png", ContentFile(image.data))

To store a normalised file instead (white background, no metadata), re-encode it
with Pillow *after* this check, so the decoder only ever sees a PNG whose size
is already bounded::

    from io import BytesIO
    from PIL import Image

    with Image.open(BytesIO(image.data)) as png:
        png.load()
        flat = Image.new("RGB", png.size, "white")
        flat.paste(png.convert("RGBA"), mask=png.convert("RGBA").getchannel("A"))
        out = BytesIO()
        flat.save(out, format="PNG", optimize=True)

The SignaturePad component sends at most ``max_bytes`` (default 200 KB) of data
URL and the defaults here accept at most that much decoded PNG; both limits are
yours to tighten.
"""

from __future__ import annotations

import base64
import binascii
import struct
import zlib
from typing import NamedTuple

__all__ = [
    "DEFAULT_MAX_BYTES",
    "SignatureError",
    "SignatureImage",
    "decode_signature_data_url",
]

#: What the SignaturePad hook sends at most, and what is accepted by default.
DEFAULT_MAX_BYTES = 200 * 1024

_PREFIX = "data:image/png;base64,"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# colour type -> (channels, allowed bit depths), PNG specification table 11.1
_COLOUR_TYPES = {
    0: (1, (1, 2, 4, 8, 16)),
    2: (3, (8, 16)),
    3: (1, (1, 2, 4, 8)),
    4: (2, (8, 16)),
    6: (4, (8, 16)),
}
_MAX_CHUNKS = 1024


class SignatureError(ValueError):
    """The value is not an acceptable signature PNG.

    The message is a fixed phrase naming the rule that failed; it never contains
    any part of the value, so it is safe to log.
    """


class SignatureImage(NamedTuple):
    """A validated signature: the PNG bytes as received, and its size."""

    data: bytes
    width: int
    height: int


def _base64_length(size: int) -> int:
    return 4 * ((size + 2) // 3)


def decode_signature_data_url(
    value: object,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_width: int = 4096,
    max_height: int = 2048,
    max_pixels: int = 2_000_000,
) -> SignatureImage:
    """Validate ``value`` as a ``data:image/png;base64,...`` URL and return the PNG.

    Raises :class:`SignatureError` for anything that is not a well-formed,
    bounded PNG. See the module docstring for the checks and for an example
    handler.
    """
    if not isinstance(value, str):
        raise SignatureError("signature is not a string")
    if len(value) > len(_PREFIX) + _base64_length(max_bytes):
        raise SignatureError("signature is too large")
    if not value.startswith(_PREFIX):
        raise SignatureError("signature is not a PNG data URL")
    payload = value[len(_PREFIX) :]
    if not payload or len(payload) % 4:
        raise SignatureError("signature is not valid base64")
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("signature is not valid base64") from None
    if len(data) > max_bytes:
        raise SignatureError("signature is too large")
    width, height = _validate_png(data, max_bytes, max_width, max_height, max_pixels)
    return SignatureImage(data, width, height)


def _validate_png(
    data: bytes, max_bytes: int, max_width: int, max_height: int, max_pixels: int
) -> tuple[int, int]:
    if data[:8] != _PNG_SIGNATURE:
        raise SignatureError("signature is not a PNG")
    pos = 8
    size = len(data)
    width = height = 0
    row_bytes = 0
    seen_idat = False
    idat: list[bytes] = []
    chunks = 0
    ended = False
    while pos < size:
        chunks += 1
        if chunks > _MAX_CHUNKS or ended:
            raise SignatureError("signature PNG has too many chunks or data after its end")
        if size - pos < 12:
            raise SignatureError("signature PNG is truncated")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        if length > size - pos - 12:
            raise SignatureError("signature PNG chunk is longer than the data")
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise SignatureError("signature PNG chunk checksum is wrong")
        if chunks == 1:
            if kind != b"IHDR" or length != 13:
                raise SignatureError("signature PNG does not start with a header")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if not (0 < width <= max_width and 0 < height <= max_height):
                raise SignatureError("signature PNG dimensions are out of range")
            if width * height > max_pixels:
                raise SignatureError("signature PNG has too many pixels")
            channels_depths = _COLOUR_TYPES.get(colour)
            if channels_depths is None or depth not in channels_depths[1]:
                raise SignatureError("signature PNG colour type is not valid")
            if compression or filtering or interlace:
                raise SignatureError("signature PNG uses an unsupported method")
            row_bytes = (width * channels_depths[0] * depth + 7) // 8
        elif kind == b"IHDR":
            raise SignatureError("signature PNG has two headers")
        elif kind == b"IDAT":
            seen_idat = True
            idat.append(body)
        elif kind == b"IEND":
            if length:
                raise SignatureError("signature PNG end chunk is not empty")
            ended = True
        pos += 12 + length
    if not ended or not seen_idat:
        raise SignatureError("signature PNG is incomplete")
    _validate_pixels(b"".join(idat), width, height, row_bytes)
    return width, height


def _validate_pixels(compressed: bytes, width: int, height: int, row_bytes: int) -> None:
    """Inflate the image data, never past the size the header promises."""
    expected = height * (row_bytes + 1)
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(compressed, expected + 1)
    except zlib.error:
        raise SignatureError("signature PNG image data is corrupt") from None
    if len(raw) != expected or inflater.unconsumed_tail or not inflater.eof or inflater.unused_data:
        raise SignatureError("signature PNG image data does not match its header")
    # Each scanline starts with a filter type, 0..4.
    if any(raw[i] > 4 for i in range(0, expected, row_bytes + 1)):
        raise SignatureError("signature PNG has an invalid scanline filter")
