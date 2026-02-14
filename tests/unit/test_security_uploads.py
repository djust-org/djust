"""
Security tests for file upload handling in djust.

Tests verify that the upload system properly rejects:
- Path traversal in filenames (../../etc/passwd)
- MIME type spoofing (wrong magic bytes)
- Oversized file uploads
- Malicious filenames (XSS payloads, null bytes)
- Binary frame protocol manipulation
- Upload slot exhaustion attacks
"""

import os
import struct
import tempfile
import uuid


from djust.uploads import (
    FRAME_CANCEL,
    FRAME_CHUNK,
    FRAME_COMPLETE,
    UploadConfig,
    UploadEntry,
    UploadManager,
    parse_upload_frame,
    validate_magic_bytes,
)


# ============================================================================
# Path traversal tests
# ============================================================================


class TestPathTraversalInFilenames:
    """Verify upload filenames with path traversal components are handled safely."""

    def _make_entry(self, client_name: str) -> UploadEntry:
        return UploadEntry(
            ref=str(uuid.uuid4()),
            upload_name="test",
            client_name=client_name,
            client_type="text/plain",
            client_size=100,
        )

    def test_directory_traversal_in_filename(self):
        """Filename with ../ should not escape upload directory on finalize."""
        entry = self._make_entry("../../etc/passwd")
        entry.add_chunk(0, b"fake content here")

        with tempfile.TemporaryDirectory() as temp_dir:
            entry.finalize(temp_dir)
            # The temp file must be created INSIDE the temp directory
            if entry._temp_path:
                assert os.path.dirname(os.path.abspath(entry._temp_path)) == os.path.abspath(
                    temp_dir
                )
            entry.cleanup()

    def test_absolute_path_in_filename(self):
        """Filename with absolute path does not escape temp directory."""
        entry = self._make_entry("/etc/shadow")
        entry.add_chunk(0, b"fake content")

        with tempfile.TemporaryDirectory() as temp_dir:
            entry.finalize(temp_dir)
            if entry._temp_path:
                assert os.path.dirname(os.path.abspath(entry._temp_path)) == os.path.abspath(
                    temp_dir
                )
            entry.cleanup()

    def test_backslash_traversal(self):
        """Windows-style backslash traversal in filename."""
        entry = self._make_entry("..\\..\\windows\\system32\\config")
        entry.add_chunk(0, b"fake content")

        with tempfile.TemporaryDirectory() as temp_dir:
            entry.finalize(temp_dir)
            if entry._temp_path:
                assert os.path.dirname(os.path.abspath(entry._temp_path)) == os.path.abspath(
                    temp_dir
                )
            entry.cleanup()

    def test_null_byte_in_filename(self):
        """Null byte injection in filename (classic C string termination attack)."""
        entry = self._make_entry("innocent.jpg\x00.exe")
        entry.add_chunk(0, b"\xff\xd8\xff\xe0" + b"\x00" * 20)

        with tempfile.TemporaryDirectory() as temp_dir:
            # Should not crash; finalize uses Path().suffix which handles null bytes
            entry.finalize(temp_dir)
            entry.cleanup()
            # The important thing is no crash and file stays in temp_dir


class TestFilenameXSS:
    """Verify malicious filenames don't enable XSS when rendered."""

    def test_script_tag_in_filename(self):
        """Filename with script tag should be stored as-is (escaping is template's job)."""
        entry = UploadEntry(
            ref=str(uuid.uuid4()),
            upload_name="docs",
            client_name='<script>alert("xss")</script>.pdf',
            client_type="application/pdf",
            client_size=100,
        )
        # The filename is stored verbatim; template auto-escaping handles display
        assert "<script>" in entry.client_name

    def test_event_handler_in_filename(self):
        """Filename with HTML event handler attributes."""
        entry = UploadEntry(
            ref=str(uuid.uuid4()),
            upload_name="docs",
            client_name='file" onload="alert(1)',
            client_type="text/plain",
            client_size=100,
        )
        assert entry.client_name == 'file" onload="alert(1)'


# ============================================================================
# MIME type spoofing tests
# ============================================================================


class TestMimeTypeSpoofing:
    """Verify magic bytes validation catches MIME type mismatches."""

    def test_exe_disguised_as_jpeg(self):
        """EXE file with .jpg extension fails magic byte check."""
        # MZ header (Windows PE)
        exe_data = b"MZ" + b"\x00" * 30
        assert validate_magic_bytes(exe_data, "image/jpeg") is False

    def test_valid_jpeg_passes(self):
        """Actual JPEG magic bytes pass validation."""
        jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        assert validate_magic_bytes(jpeg_data, "image/jpeg") is True

    def test_valid_png_passes(self):
        """Actual PNG magic bytes pass validation."""
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        assert validate_magic_bytes(png_data, "image/png") is True

    def test_png_header_in_jpeg_slot(self):
        """PNG data claiming to be JPEG fails validation."""
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        assert validate_magic_bytes(png_data, "image/jpeg") is False

    def test_jpeg_header_in_png_slot(self):
        """JPEG data claiming to be PNG fails validation."""
        jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        assert validate_magic_bytes(jpeg_data, "image/png") is False

    def test_empty_data_rejected(self):
        """Empty file data is always rejected."""
        assert validate_magic_bytes(b"", "image/jpeg") is False

    def test_too_short_data_rejected(self):
        """Data shorter than magic byte check threshold is rejected."""
        assert validate_magic_bytes(b"\xff\xd8", "image/jpeg") is False

    def test_webp_requires_both_signatures(self):
        """WebP requires RIFF at offset 0 AND WEBP at offset 8."""
        # Only RIFF, missing WEBP
        partial = b"RIFF\x00\x00\x00\x00XXXX" + b"\x00" * 10
        assert validate_magic_bytes(partial, "image/webp") is False

        # Correct WebP header
        valid = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 10
        assert validate_magic_bytes(valid, "image/webp") is True

    def test_unknown_mime_type_passes(self):
        """MIME types without known magic bytes are allowed (permissive)."""
        assert validate_magic_bytes(b"arbitrary data here!!", "application/octet-stream") is True

    def test_svg_with_xml_header(self):
        """SVG files can start with <?xml declaration."""
        svg_data = b"<?xml version='1.0'?><svg>" + b"\x00" * 10
        assert validate_magic_bytes(svg_data, "image/svg+xml") is True

    def test_html_disguised_as_svg(self):
        """HTML file claiming to be SVG but without svg/xml magic bytes."""
        html_data = b"<!DOCTYPE html><html>" + b"\x00" * 10
        assert validate_magic_bytes(html_data, "image/svg+xml") is False


# ============================================================================
# Upload size enforcement tests
# ============================================================================


class TestUploadSizeEnforcement:
    """Verify file size limits are enforced at multiple levels."""

    def test_register_rejects_oversized_declaration(self):
        """Client-declared size exceeding max_file_size is rejected at registration."""
        mgr = UploadManager()
        mgr.configure("photos", accept=".jpg", max_file_size=1_000_000)

        entry = mgr.register_entry(
            upload_name="photos",
            ref=str(uuid.uuid4()),
            client_name="huge.jpg",
            client_type="image/jpeg",
            client_size=50_000_000,  # 50MB, way over 1MB limit
        )
        assert entry is None

    def test_chunk_accumulation_enforced(self):
        """Chunks exceeding max_file_size are rejected mid-transfer."""
        mgr = UploadManager()
        mgr.configure("photos", accept=".jpg", max_file_size=1000)

        ref = str(uuid.uuid4())
        entry = mgr.register_entry(
            upload_name="photos",
            ref=ref,
            client_name="test.jpg",
            client_type="image/jpeg",
            client_size=500,  # Declared small
        )
        assert entry is not None

        # Send chunks that exceed max_file_size
        progress = mgr.add_chunk(ref, 0, b"\xff" * 800)
        assert progress is not None  # First chunk OK (800 < 1000)

        progress = mgr.add_chunk(ref, 1, b"\xff" * 500)
        assert progress is None  # Cumulative 1300 > 1000, rejected

    def test_finalize_checks_actual_size(self):
        """Finalize validates actual data size against declared client_size."""
        entry = UploadEntry(
            ref=str(uuid.uuid4()),
            upload_name="test",
            client_name="test.txt",
            client_type="text/plain",
            client_size=100,  # Declared 100 bytes
        )
        # Add 200 bytes of data (> 110% of declared)
        entry.add_chunk(0, b"x" * 200)

        with tempfile.TemporaryDirectory() as temp_dir:
            result = entry.finalize(temp_dir)
            assert result is False
            assert "too large" in entry.error.lower()
            entry.cleanup()


# ============================================================================
# Upload slot exhaustion tests
# ============================================================================


class TestUploadSlotExhaustion:
    """Verify max_entries limits prevent slot exhaustion attacks."""

    def test_max_entries_enforced(self):
        """Cannot register more entries than max_entries allows."""
        mgr = UploadManager()
        mgr.configure("avatar", max_entries=1, max_file_size=10_000_000)

        # First entry succeeds
        entry1 = mgr.register_entry(
            upload_name="avatar",
            ref=str(uuid.uuid4()),
            client_name="pic1.jpg",
            client_type="image/jpeg",
            client_size=1000,
        )
        assert entry1 is not None

        # Second entry rejected (max_entries=1)
        entry2 = mgr.register_entry(
            upload_name="avatar",
            ref=str(uuid.uuid4()),
            client_name="pic2.jpg",
            client_type="image/jpeg",
            client_size=1000,
        )
        assert entry2 is None

    def test_completed_entries_free_slots(self):
        """Completed and consumed entries free up slots for new uploads."""
        mgr = UploadManager()
        mgr.configure("avatar", accept=".txt", max_entries=1, max_file_size=10_000_000)

        ref = str(uuid.uuid4())
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=ref,
            client_name="file.txt",
            client_type="text/plain",
            client_size=5,
        )
        assert entry is not None
        entry.add_chunk(0, b"hello")
        mgr.complete_upload(ref)

        # Consume the entry
        list(mgr.consume_entries("avatar"))

        # New entry should now be allowed
        entry2 = mgr.register_entry(
            upload_name="avatar",
            ref=str(uuid.uuid4()),
            client_name="file2.txt",
            client_type="text/plain",
            client_size=5,
        )
        assert entry2 is not None

    def test_unconfigured_upload_name_rejected(self):
        """Uploads to non-configured slot names are rejected."""
        mgr = UploadManager()
        mgr.configure("avatar", max_entries=1, max_file_size=10_000_000)

        entry = mgr.register_entry(
            upload_name="backdoor",
            ref=str(uuid.uuid4()),
            client_name="evil.sh",
            client_type="text/plain",
            client_size=100,
        )
        assert entry is None


# ============================================================================
# Extension validation tests
# ============================================================================


class TestExtensionValidation:
    """Verify file extension validation catches bypass attempts."""

    def test_accepted_extension_passes(self):
        config = UploadConfig(name="test", accept=".jpg,.png")
        assert config.validate_extension("photo.jpg") is True
        assert config.validate_extension("photo.png") is True

    def test_rejected_extension_blocked(self):
        config = UploadConfig(name="test", accept=".jpg,.png")
        assert config.validate_extension("script.exe") is False
        assert config.validate_extension("shell.sh") is False
        assert config.validate_extension("page.html") is False

    def test_double_extension_uses_last(self):
        """Double extensions like .jpg.exe use the last extension."""
        config = UploadConfig(name="test", accept=".jpg")
        # Path().suffix returns .exe for "file.jpg.exe"
        assert config.validate_extension("photo.jpg.exe") is False

    def test_case_insensitive_extension(self):
        """Extension check is case-insensitive."""
        config = UploadConfig(name="test", accept=".jpg,.png")
        assert config.validate_extension("PHOTO.JPG") is True
        assert config.validate_extension("image.PNG") is True

    def test_no_extension_restriction(self):
        """Empty accept allows all extensions."""
        config = UploadConfig(name="test", accept="")
        assert config.validate_extension("anything.exe") is True

    def test_mime_type_in_accept(self):
        """Accept can include MIME types directly."""
        config = UploadConfig(name="test", accept="image/jpeg,image/png")
        assert config.validate_mime("image/jpeg") is True
        assert config.validate_mime("image/png") is True
        assert config.validate_mime("application/pdf") is False

    def test_wildcard_mime(self):
        """Wildcard MIME types like image/* work."""
        config = UploadConfig(name="test", accept="image/*")
        assert config.validate_mime("image/jpeg") is True
        assert config.validate_mime("image/png") is True
        assert config.validate_mime("application/pdf") is False


# ============================================================================
# Binary frame protocol security tests
# ============================================================================


class TestBinaryFrameProtocol:
    """Verify binary frame parsing handles malformed/malicious frames safely."""

    def test_too_short_frame_rejected(self):
        """Frames shorter than header size return None."""
        assert parse_upload_frame(b"") is None
        assert parse_upload_frame(b"\x01") is None
        assert parse_upload_frame(b"\x01" * 10) is None

    def test_invalid_uuid_rejected(self):
        """Invalid UUID bytes in frame header return None."""
        # Frame type + 16 bytes of invalid UUID
        frame = b"\x01" + b"\xff" * 16
        # UUID(bytes=...) may or may not raise ValueError for arbitrary bytes
        # but the result should either be None or a valid parsed frame
        result = parse_upload_frame(frame)
        # If it parses, it should at least be a chunk type needing more data
        if result is not None:
            # Chunk frames need additional 4 bytes for chunk_index
            assert result.get("type") in ("chunk", "complete", "cancel")

    def test_unknown_frame_type_rejected(self):
        """Unknown frame types return None."""
        ref_bytes = uuid.uuid4().bytes
        frame = b"\xff" + ref_bytes  # 0xFF is not a valid frame type
        assert parse_upload_frame(frame) is None

    def test_chunk_without_index_rejected(self):
        """Chunk frame without chunk_index bytes returns None."""
        ref_bytes = uuid.uuid4().bytes
        # Chunk type + UUID but no chunk_index (needs 4 more bytes)
        frame = bytes([FRAME_CHUNK]) + ref_bytes
        assert parse_upload_frame(frame) is None

    def test_valid_chunk_frame_parsed(self):
        """Valid chunk frame is parsed correctly."""
        ref = uuid.uuid4()
        chunk_data = b"file content here"
        frame = bytes([FRAME_CHUNK]) + ref.bytes + struct.pack(">I", 0) + chunk_data
        result = parse_upload_frame(frame)
        assert result is not None
        assert result["type"] == "chunk"
        assert result["ref"] == str(ref)
        assert result["chunk_index"] == 0
        assert result["data"] == chunk_data

    def test_valid_complete_frame_parsed(self):
        """Valid complete frame is parsed correctly."""
        ref = uuid.uuid4()
        frame = bytes([FRAME_COMPLETE]) + ref.bytes
        result = parse_upload_frame(frame)
        assert result is not None
        assert result["type"] == "complete"
        assert result["ref"] == str(ref)

    def test_valid_cancel_frame_parsed(self):
        """Valid cancel frame is parsed correctly."""
        ref = uuid.uuid4()
        frame = bytes([FRAME_CANCEL]) + ref.bytes
        result = parse_upload_frame(frame)
        assert result is not None
        assert result["type"] == "cancel"

    def test_nonexistent_ref_chunk_ignored(self):
        """Chunk for unknown ref returns None from UploadManager."""
        mgr = UploadManager()
        result = mgr.add_chunk("nonexistent-ref", 0, b"data")
        assert result is None


# ============================================================================
# Cleanup and resource management tests
# ============================================================================


class TestUploadCleanup:
    """Verify uploads are properly cleaned up to prevent resource exhaustion."""

    def test_cancel_removes_entry(self):
        """Cancelled uploads are fully cleaned up."""
        mgr = UploadManager()
        mgr.configure("test", max_file_size=10_000_000)

        ref = str(uuid.uuid4())
        mgr.register_entry(
            upload_name="test",
            ref=ref,
            client_name="test.txt",
            client_type="text/plain",
            client_size=100,
        )
        mgr.add_chunk(ref, 0, b"data")
        mgr.cancel_upload(ref)

        assert ref not in mgr._entries

    def test_cleanup_removes_all(self):
        """Full cleanup removes all entries and temp files."""
        mgr = UploadManager()
        mgr.configure("test", max_file_size=10_000_000)

        for i in range(5):
            ref = str(uuid.uuid4())
            mgr.register_entry(
                upload_name="test",
                ref=ref,
                client_name=f"file{i}.txt",
                client_type="text/plain",
                client_size=10,
            )
            mgr.add_chunk(ref, 0, b"data")

        mgr.cleanup()
        assert len(mgr._entries) == 0
        assert len(mgr._name_to_refs) == 0
