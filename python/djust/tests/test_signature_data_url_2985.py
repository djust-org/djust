"""``decode_signature_data_url`` accepts a bounded PNG and nothing else (#2985, batch 5).

The value is whatever a browser sent. Every test builds the hostile value
byte by byte rather than relying on an imaging library, because the point is
what the validator does *without* decoding pixels: a small file that claims to
be huge, a stream that inflates far past its header, a damaged chunk, a value
that is not a PNG at all.
"""

from __future__ import annotations

import base64
import struct
import tracemalloc
import zlib

import pytest

from djust.components import signature as sig
from djust.components.signature import SignatureError, decode_signature_data_url

PREFIX = "data:image/png;base64,"


def chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    )


def ihdr(width, height, depth=8, colour=6, compression=0, filtering=0, interlace=0) -> bytes:
    return chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, depth, colour, compression, filtering, interlace),
    )


def row_bytes(width, depth, channels):
    return (width * channels * depth + 7) // 8


def png(width=8, height=4, depth=8, colour=6, *, raw=None, extra=b"", trailing=b"", filter_byte=0):
    channels = sig._COLOUR_TYPES[colour][0]
    stride = row_bytes(width, depth, channels)
    if raw is None:
        raw = (bytes([filter_byte]) + b"\x00" * stride) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + ihdr(width, height, depth, colour)
        + extra
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
        + trailing
    )


def url(data: bytes) -> str:
    return PREFIX + base64.b64encode(data).decode()


def rejects(value, **kw):
    with pytest.raises(SignatureError) as info:
        decode_signature_data_url(value, **kw)
    return str(info.value)


class TestAccepts:
    @pytest.mark.parametrize(
        "colour,depth", [(0, 8), (0, 1), (2, 8), (2, 16), (4, 8), (6, 8), (6, 16), (3, 8), (3, 4)]
    )
    def test_a_well_formed_png_of_any_colour_type(self, colour, depth):
        data = png(9, 5, depth, colour, extra=chunk(b"PLTE", b"\x00" * 3) if colour == 3 else b"")
        image = decode_signature_data_url(url(data))
        assert image.data == data
        assert (image.width, image.height) == (9, 5)

    def test_returns_the_original_bytes_not_a_re_encoding(self):
        data = png(20, 10, extra=chunk(b"tEXt", b"k\x00v"))
        assert decode_signature_data_url(url(data)).data == data

    def test_a_png_made_by_pillow(self):
        pil = pytest.importorskip("PIL.Image")
        from io import BytesIO

        for mode in ("RGBA", "RGB", "L", "P", "LA"):
            out = BytesIO()
            pil.new(mode, (40, 20), 0).save(out, "PNG")
            image = decode_signature_data_url(url(out.getvalue()))
            assert (image.width, image.height) == (40, 20), mode

    def test_the_largest_allowed_value_is_accepted(self):
        data = png(8, 4, extra=chunk(b"tEXt", b"k\x00" + b"v" * 1000))
        limit = len(data)
        assert decode_signature_data_url(url(data), max_bytes=limit).data == data
        rejects(url(data), max_bytes=limit - 1)


class TestRejectsBeforeDecoding:
    @pytest.mark.parametrize(
        "value", [None, 5, 1.5, b"data:image/png;base64,AAAA", ["x"], {"a": 1}, True]
    )
    def test_not_a_string(self, value):
        assert "not a string" in rejects(value)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "hello",
            "data:image/jpeg;base64,/9j/4AAQ",
            "data:image/png;base64",
            "data:image/png,AAAA",
            "DATA:image/png;base64,AAAA",
            " data:image/png;base64,AAAA",
            "data:image/svg+xml;base64,PHN2Zz4=",
            "data:image/png;charset=utf-8;base64,AAAA",
            "javascript:alert(1)",
            "http://example.com/a.png",
        ],
    )
    def test_not_the_exact_png_data_url_prefix(self, value):
        assert "PNG data URL" in rejects(value)

    def test_an_oversized_value_is_refused_before_any_base64_work(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sig.base64, "b64decode", lambda *a, **k: calls.append(1))
        assert "too large" in rejects(PREFIX + "A" * 400_000)
        assert "too large" in rejects(PREFIX + "A" * 4000, max_bytes=1000)
        assert calls == []

    @pytest.mark.parametrize(
        "payload",
        [
            "",
            "A",
            "AAA",
            "AA=A",
            "AAAA\n",
            "AAAA ",
            "AA AA",
            "AAA*",
            "AAA-",
            "AAA_",
            "====",
            "A===",
            "AAAAA==",
        ],
    )
    def test_not_strict_base64(self, payload):
        assert "base64" in rejects(PREFIX + payload)

    def test_the_decoded_size_is_capped_too(self):
        assert "too large" in rejects(url(b"\x89PNG\r\n\x1a\n" + b"\x00" * 5000), max_bytes=1000)


class TestRejectsBadPngs:
    def test_not_a_png(self):
        assert "not a PNG" in rejects(url(b"GIF89a" + b"\x00" * 40))
        assert "not a PNG" in rejects(url(b"\x89PNG\r\n\x1a" + b"\x00" * 40))
        assert "not a PNG" in rejects(url(b""[:0] + b"\x00\x00\x00\x00"))

    def test_every_truncation_of_a_valid_png_is_refused(self):
        data = png(6, 3)
        for cut in range(len(data) - 1):
            rejects(url(data[:cut]) if cut else PREFIX)

    def test_data_after_the_end_chunk(self):
        rejects(url(png(trailing=b"\x00")))
        rejects(url(png(trailing=chunk(b"tEXt", b"a\x00b"))))
        rejects(url(png(trailing=b"<script>alert(1)</script>")))

    def test_a_damaged_chunk_checksum_anywhere(self):
        data = bytearray(png(6, 3))
        for i in range(8, len(data)):
            damaged = bytearray(data)
            damaged[i] ^= 0x01
            rejects(url(bytes(damaged)))

    def test_header_rules(self):
        """Each case has image data that matches its own header, so the header
        rule is the only thing that can refuse it."""
        magic = b"\x89PNG\r\n\x1a\n"
        iend = chunk(b"IEND", b"")

        def idat(w, h, depth=8, colour=6):
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour, 1)
            stride = (w * channels * depth + 7) // 8
            return chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00" * stride) * h))

        good = idat(8, 4)
        decode_signature_data_url(url(magic + ihdr(8, 4) + good + iend))
        # IHDR not first / twice / the wrong length
        assert "header" in rejects(url(magic + good + ihdr(8, 4) + iend))
        assert "two headers" in rejects(url(magic + ihdr(8, 4) + ihdr(8, 4) + good + iend))
        assert "header" in rejects(url(magic + chunk(b"IHDR", b"\x00" * 12) + good + iend))
        # nonsense fields
        for args in [
            (0, 4),
            (8, 0),
            (8, 4, 8, 1),
            (8, 4, 4, 2),
            (8, 4, 8, 7),
            (8, 4, 3, 6),
            (8, 4, 8, 6, 1),
            (8, 4, 8, 6, 0, 1),
            (8, 4, 8, 6, 0, 0, 1),
            (8, 4, 8, 6, 0, 0, 2),
        ]:
            assert rejects(url(magic + ihdr(*args) + idat(*args[:4]) + iend)), args
        assert "unsupported" in rejects(url(magic + ihdr(8, 4, 8, 6, 0, 0, 1) + good + iend))
        assert "unsupported" in rejects(url(magic + ihdr(8, 4, 8, 6, 1) + good + iend))

    def test_no_image_data_or_no_end(self):
        magic = b"\x89PNG\r\n\x1a\n"
        stream = zlib.compress(b"\x00" * 132)
        rejects(url(magic + ihdr(8, 4) + chunk(b"IEND", b"")))
        rejects(url(magic + ihdr(8, 4) + chunk(b"IDAT", stream)))
        assert "not empty" in rejects(
            url(magic + ihdr(8, 4) + chunk(b"IDAT", stream) + chunk(b"IEND", b"x"))
        )

    def test_a_stream_with_the_right_amount_of_data_but_no_end_marker(self):
        magic = b"\x89PNG\r\n\x1a\n"
        stream = zlib.compress(b"\x00" * 132)
        for cut in (1, 2, 3, 4):  # the Adler-32 trailer, in whole or part
            data = magic + ihdr(8, 4) + chunk(b"IDAT", stream[:-cut]) + chunk(b"IEND", b"")
            assert "does not match" in rejects(url(data)), cut

    def test_a_chunk_length_that_runs_past_the_data(self):
        data = (
            b"\x89PNG\r\n\x1a\n"
            + ihdr(8, 4)
            + struct.pack(">I", 0xFFFFFFFF)
            + b"IDAT"
            + b"\x00" * 16
        )
        assert "longer" in rejects(url(data))

    def test_too_many_chunks(self):
        many = chunk(b"tEXt", b"a\x00b") * 1100
        rejects(url(png(extra=many)), max_bytes=1_000_000)


class TestBoundedWork:
    def test_dimensions_are_checked_from_the_header_alone(self):
        # A file of a few hundred bytes that claims to be gigapixel.
        for w, h in [(60000, 60000), (4097, 10), (10, 2049), (2**32 - 1, 2**32 - 1), (5000, 5000)]:
            assert "dimensions" in rejects(url(png(w, h, raw=b"\x00"))) or "pixels" in rejects(
                url(png(w, h, raw=b"\x00"))
            )

    def test_the_pixel_budget(self):
        assert "pixels" in rejects(url(png(2000, 1100, raw=b"\x00")), max_pixels=2_000_000)
        assert "pixels" in rejects(url(png(100, 100, raw=b"\x00")), max_pixels=9_999)
        decode_signature_data_url(url(png(100, 100)), max_pixels=10_000)

    def test_a_decompression_bomb_behind_a_small_header_is_refused_without_inflating_it(self):
        # 150 MB of zeros compress to ~150 KB: under the byte cap, wildly over the header's 100x100.
        bomb = zlib.compress(b"\x00" * 150_000_000, 9)
        assert len(bomb) < 200 * 1024
        data = b"\x89PNG\r\n\x1a\n" + ihdr(100, 100) + chunk(b"IDAT", bomb) + chunk(b"IEND", b"")
        tracemalloc.start()
        try:
            assert "does not match" in rejects(url(data))
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 8 * 1024 * 1024, peak

    def test_image_data_that_is_short_or_long_or_followed_by_junk(self):
        stride = row_bytes(8, 8, 4)
        want = (1 + stride) * 4
        for raw in (b"\x00" * (want - 1), b"\x00" * (want + 1), b"\x00" * (want * 3)):
            assert "does not match" in rejects(url(png(8, 4, raw=raw)))
        magic = b"\x89PNG\r\n\x1a\n"
        stream = zlib.compress(b"\x00" * want)
        rejects(url(magic + ihdr(8, 4) + chunk(b"IDAT", stream + b"junk") + chunk(b"IEND", b"")))
        rejects(
            url(magic + ihdr(8, 4) + chunk(b"IDAT", stream[:-6]) + chunk(b"IEND", b""))
        )  # no end of stream
        rejects(url(magic + ihdr(8, 4) + chunk(b"IDAT", b"not zlib at all") + chunk(b"IEND", b"")))

    def test_image_data_split_over_several_idat_chunks(self):
        stride = row_bytes(8, 8, 4)
        stream = zlib.compress((b"\x00" + b"\x01" * stride) * 4)
        half = len(stream) // 2
        data = (
            b"\x89PNG\r\n\x1a\n"
            + ihdr(8, 4)
            + chunk(b"IDAT", stream[:half])
            + chunk(b"IDAT", stream[half:])
            + chunk(b"IEND", b"")
        )
        assert decode_signature_data_url(url(data)).width == 8

    def test_an_invalid_scanline_filter_byte(self):
        stride = row_bytes(8, 8, 4)
        raw = (b"\x00" + b"\x00" * stride) * 3 + b"\x05" + b"\x00" * stride
        assert "filter" in rejects(url(png(8, 4, raw=raw)))
        for ok in range(5):
            decode_signature_data_url(url(png(8, 4, filter_byte=ok)))

    def test_the_idat_total_counts_against_the_byte_cap(self):
        data = png(8, 4, raw=None)
        assert "too large" in rejects(url(data), max_bytes=len(data) - 30)


class TestErrors:
    def test_is_a_value_error_and_never_echoes_the_value(self):
        secret = "SECRET-123456"
        for value in (
            PREFIX + base64.b64encode(secret.encode()).decode(),
            secret,
            PREFIX + secret + "!",
        ):
            with pytest.raises(ValueError) as info:
                decode_signature_data_url(value)
            assert isinstance(info.value, SignatureError)
            assert secret not in str(info.value) and "SECRET" not in str(info.value)
            assert info.value.__cause__ is None or secret not in repr(info.value.__cause__)

    def test_the_default_limit_is_the_components_default(self):
        from djust.components.components.signature_pad import SignaturePad

        assert sig.DEFAULT_MAX_BYTES == 200 * 1024
        assert 'data-max-bytes="204800"' in SignaturePad().render()
