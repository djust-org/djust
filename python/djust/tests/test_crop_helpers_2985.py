"""``djust.components.cropping``: a crop box from the browser is validated, then applied (#2985, batch 5).

The numbers are whatever a client sent. ``crop_box`` needs no imaging library;
``crop_image`` (Pillow, skipped without it) must also refuse an image that is too
large before decoding it, honour the EXIF orientation the browser displayed, and
leave no metadata behind.
"""

from __future__ import annotations

import struct
import tracemalloc
import zlib
from io import BytesIO

import pytest

from djust.components.cropping import (
    CropBox,
    CropError,
    crop_box,
    crop_image,
    parse_aspect_ratio,
)

SIZE = (200, 100)


def bad(*args, **kw):
    with pytest.raises(CropError) as info:
        crop_box(*args, **kw)
    return str(info.value)


class TestCropBox:
    def test_a_box_inside_the_image(self):
        box = crop_box(10, 20, 100, 50, SIZE)
        assert box == CropBox(10, 20, 110, 70)
        assert (box.width, box.height) == (100, 50)

    def test_floats_are_rounded_and_the_whole_image_is_a_box(self):
        assert crop_box(9.6, 20.4, 99.5, 49.5, SIZE) == CropBox(10, 20, 110, 70)
        assert crop_box(0, 0, 200, 100, SIZE) == CropBox(0, 0, 200, 100)

    def test_a_box_that_sticks_out_is_cut_back(self):
        assert crop_box(150, 80, 100, 100, SIZE) == CropBox(150, 80, 200, 100)
        assert crop_box(-30, -10, 100, 60, SIZE) == CropBox(0, 0, 70, 50)
        assert crop_box(-1000, -1000, 1_000_000, 1_000_000, SIZE) == CropBox(0, 0, 200, 100)

    def test_with_clamp_off_it_is_refused(self):
        for args in [(150, 80, 100, 100), (-1, 0, 10, 10), (0, 0, 201, 10), (0, 0, 10, 101)]:
            assert "outside" in bad(*args, SIZE, clamp=False)
        assert crop_box(100, 50, 100, 50, SIZE, clamp=False) == CropBox(100, 50, 200, 100)

    @pytest.mark.parametrize(
        "value", [None, "5", " 5 ", "1e999", "", [5], (5,), {"a": 1}, True, False, b"5", 1j]
    )
    def test_not_a_number(self, value):
        for position in range(4):
            args = [10, 10, 50, 50]
            args[position] = value
            assert "not a number" in bad(*args, SIZE)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_not_finite(self, value):
        for position in range(4):
            args = [10, 10, 50, 50]
            args[position] = value
            assert "not finite" in bad(*args, SIZE)

    @pytest.mark.parametrize("value", [10**8, -(10**8), 10**400, 1e30])
    def test_absurd_magnitudes(self, value):
        for position in range(4):
            args = [10, 10, 50, 50]
            args[position] = value
            bad(*args, SIZE)

    @pytest.mark.parametrize("size", [0, -1, -50, 0.4, -0.6])
    def test_a_size_that_is_not_positive(self, size):
        assert "not positive" in bad(10, 10, size, 50, SIZE)
        assert "not positive" in bad(10, 10, 50, size, SIZE)

    def test_a_box_wholly_outside_the_image(self):
        for args in [(300, 10, 50, 50), (10, 200, 50, 50), (-100, 10, 50, 50), (10, -100, 50, 50)]:
            bad(*args, SIZE)

    def test_the_minimum_size(self):
        assert crop_box(0, 0, 5, 5, SIZE, min_size=5) == CropBox(0, 0, 5, 5)
        bad(0, 0, 4, 50, SIZE, min_size=5)
        bad(195, 0, 50, 50, SIZE, min_size=10)  # clamped to 5 px wide

    def test_an_image_with_no_size(self):
        assert "no size" in bad(0, 0, 1, 1, (0, 10))

    def test_messages_never_echo_the_values(self):
        for value in ("SECRET-1", 12345678901, float("nan")):
            try:
                crop_box(value, 0, 10, 10, SIZE)
            except CropError as exc:
                assert "SECRET" not in str(exc) and "12345678901" not in str(exc)
        assert issubclass(CropError, ValueError)


class TestAspectRatio:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("16/9", 16 / 9),
            ("4:3", 4 / 3),
            ("1/1", 1.0),
            ("1.5", 1.5),
            (" 2 ", 2.0),
            (1, 1.0),
            (0.5, 0.5),
            ("21 / 9", 21 / 9),
        ],
    )
    def test_valid(self, value, expected):
        assert parse_aspect_ratio(value) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            None,
            True,
            "x",
            "0",
            "0/1",
            "1/0",
            "-1",
            "1/",
            "/2",
            "nan",
            "inf",
            "100",
            "0.001",
            "1/2/3",
        ],
    )
    def test_free(self, value):
        assert parse_aspect_ratio(value) is None


try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow is not a djust dependency: the crop_image tests need it
    Image = ImageOps = None

needs_pillow = pytest.mark.skipif(Image is None, reason="Pillow is not installed")


def encode(image, fmt, **save):
    out = BytesIO()
    image.save(out, fmt, **save)
    return out.getvalue()


def striped(width, height, mode="RGB"):
    """Left half red, right half blue, a white row on top: easy to see where a crop went."""
    image = Image.new(mode, (width, height), (0, 0, 255) if mode == "RGB" else 0)
    for x in range(width // 2):
        for y in range(height):
            image.putpixel((x, y), (255, 0, 0) if mode == "RGB" else 255)
    return image


@needs_pillow
class TestCropImage:
    def test_crops_to_the_box_and_keeps_the_format(self):
        for fmt in ("PNG", "JPEG", "WEBP"):
            source = encode(striped(40, 20), fmt)
            cropped = crop_image(BytesIO(source), 5, 4, 20, 10)
            assert cropped.format == fmt
            assert (cropped.width, cropped.height) == (20, 10)
            result = Image.open(BytesIO(cropped.data))
            assert result.size == (20, 10) and result.format == fmt

    def test_the_pixels_come_from_the_right_place(self):
        source = encode(striped(40, 20), "PNG")
        left = Image.open(BytesIO(crop_image(BytesIO(source), 0, 0, 10, 10).data)).convert("RGB")
        right = Image.open(BytesIO(crop_image(BytesIO(source), 30, 0, 10, 10).data)).convert("RGB")
        assert left.getpixel((5, 5)) == (255, 0, 0)
        assert right.getpixel((5, 5)) == (0, 0, 255)

    def test_a_path_works_too(self, tmp_path):
        path = tmp_path / "a.png"
        path.write_bytes(encode(striped(40, 20), "PNG"))
        assert crop_image(str(path), 0, 0, 10, 10).width == 10

    def test_the_exif_orientation_is_applied_first(self):
        """The browser shows the upright image. A 40x20 sensor image tagged
        'rotate 90' is shown as 20x40, and the box is in THAT image's pixels."""
        image = striped(40, 20)
        exif = Image.Exif()
        exif[0x0112] = 6  # shown rotated 90 degrees clockwise
        source = encode(image, "JPEG", exif=exif)
        upright = ImageOps.exif_transpose(Image.open(BytesIO(source)))
        assert upright.size == (20, 40)
        cropped = crop_image(BytesIO(source), 0, 0, 20, 40)
        assert (cropped.width, cropped.height) == (20, 40)
        # The sensor's left (red) half is the TOP of the upright image.
        top = Image.open(BytesIO(cropped.data)).convert("RGB").getpixel((10, 5))
        bottom = Image.open(BytesIO(cropped.data)).convert("RGB").getpixel((10, 35))
        assert top[0] > 200 and bottom[2] > 200
        # And a box that only fits the upright shape is accepted, not clamped to the raw one.
        assert crop_image(BytesIO(source), 0, 20, 20, 20).height == 20

    def test_no_metadata_is_copied(self):
        exif = Image.Exif()
        exif[0x010F] = "Camera Corp"
        exif[0x8825] = {1: "N"}
        source = encode(striped(40, 20), "JPEG", exif=exif)
        assert b"Camera Corp" in source
        result = crop_image(BytesIO(source), 0, 0, 20, 10).data
        assert b"Camera Corp" not in result
        assert not Image.open(BytesIO(result)).getexif()

    def test_an_image_above_the_limit_is_refused_before_it_is_decoded(self):
        # 200 bytes that claim to be 30000 x 30000: a bomb header, no pixels at all.
        def chunk(kind, body):
            return (
                struct.pack(">I", len(body))
                + kind
                + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
            )

        header = b"\x89PNG\r\n\x1a\n" + chunk(
            b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 6, 0, 0, 0)
        )
        data = header + chunk(b"IDAT", zlib.compress(b"\x00" * 100)) + chunk(b"IEND", b"")
        tracemalloc.start()
        try:
            with pytest.raises(CropError) as info:
                crop_image(BytesIO(data), 0, 0, 10, 10)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert "too large" in str(info.value)
        assert peak < 5 * 1024 * 1024, peak

    def test_the_limit_is_a_parameter(self):
        source = encode(striped(40, 20), "PNG")
        with pytest.raises(CropError, match="too large"):
            crop_image(BytesIO(source), 0, 0, 10, 10, max_pixels=799)
        assert crop_image(BytesIO(source), 0, 0, 10, 10, max_pixels=800).width == 10

    def test_pillows_global_limit_is_left_as_it_was(self):
        before = Image.MAX_IMAGE_PIXELS
        crop_image(BytesIO(encode(striped(40, 20), "PNG")), 0, 0, 10, 10)
        with pytest.raises(CropError):
            crop_image(BytesIO(b"not an image"), 0, 0, 10, 10)
        assert Image.MAX_IMAGE_PIXELS == before

    def test_pillows_own_bomb_error_becomes_a_crop_error(self, monkeypatch):
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
        with pytest.raises(CropError, match="too large"):
            crop_image(BytesIO(encode(striped(40, 20), "PNG")), 0, 0, 10, 10, max_pixels=10_000)

    @pytest.mark.parametrize(
        "data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, b"GIF89a"]
    )
    def test_not_an_image(self, data):
        with pytest.raises(CropError, match="could not be read"):
            crop_image(BytesIO(data), 0, 0, 10, 10)

    def test_a_truncated_image(self):
        source = encode(striped(80, 80), "PNG")
        with pytest.raises(CropError):
            crop_image(BytesIO(source[: len(source) // 2]), 0, 0, 10, 10)

    def test_the_box_is_validated_against_the_real_image_not_a_claimed_size(self):
        source = encode(striped(40, 20), "PNG")
        assert crop_image(BytesIO(source), 30, 10, 500, 500).width == 10  # clamped to the image
        for bad_args in [
            (float("nan"), 0, 10, 10),
            (0, 0, -5, 10),
            ("0", 0, 10, 10),
            (0, 0, 10**9, 10**9),
        ]:
            with pytest.raises(CropError):
                crop_image(BytesIO(source), *bad_args, clamp=False)

    def test_the_aspect_ratio_can_be_enforced(self):
        source = encode(striped(160, 90), "PNG")
        crop_image(BytesIO(source), 0, 0, 160, 90, aspect_ratio="16/9")
        crop_image(BytesIO(source), 0, 0, 80, 45, aspect_ratio="16:9")  # exact
        crop_image(BytesIO(source), 0, 0, 80, 45, aspect_ratio="16/9", ratio_tolerance=0.0)
        with pytest.raises(CropError, match="aspect ratio"):
            crop_image(BytesIO(source), 0, 0, 80, 80, aspect_ratio="16/9")
        crop_image(BytesIO(source), 0, 0, 80, 80, aspect_ratio="not a ratio")  # free

    def test_a_cmyk_or_palette_source_survives(self):
        for mode in ("P", "L", "RGBA", "CMYK"):
            image = Image.new(mode, (30, 30))
            fmt = "JPEG" if mode in ("CMYK", "L") else "PNG"
            assert crop_image(BytesIO(encode(image, fmt)), 0, 0, 10, 10).width == 10

    def test_the_result_extension(self):
        source = encode(striped(40, 20), "JPEG")
        assert crop_image(BytesIO(source), 0, 0, 10, 10).extension == "jpg"
        assert crop_image(BytesIO(encode(striped(40, 20), "PNG")), 0, 0, 10, 10).extension == "png"
