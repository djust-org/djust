"""
Tests for djust file upload support.
"""

import importlib.util
import os
import struct
import tempfile
import uuid
from unittest import TestCase

# Import uploads.py directly to avoid djust/__init__.py (which pulls in channels/Django)
_uploads_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'djust', 'uploads.py')
_spec = importlib.util.spec_from_file_location('djust_uploads', _uploads_path)
uploads = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uploads)

FRAME_CHUNK = uploads.FRAME_CHUNK
FRAME_COMPLETE = uploads.FRAME_COMPLETE
FRAME_CANCEL = uploads.FRAME_CANCEL
UploadConfig = uploads.UploadConfig
UploadEntry = uploads.UploadEntry
UploadManager = uploads.UploadManager
UploadMixin = uploads.UploadMixin
parse_upload_frame = uploads.parse_upload_frame
validate_magic_bytes = uploads.validate_magic_bytes
mime_from_extension = uploads.mime_from_extension
build_progress_message = uploads.build_progress_message


class TestMagicBytes(TestCase):
    def test_jpeg_valid(self):
        data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert validate_magic_bytes(data, "image/jpeg") is True

    def test_jpeg_invalid(self):
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert validate_magic_bytes(data, "image/jpeg") is False

    def test_png_valid(self):
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert validate_magic_bytes(data, "image/png") is True

    def test_webp_valid(self):
        data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 100
        assert validate_magic_bytes(data, "image/webp") is True

    def test_webp_invalid_missing_webp(self):
        data = b"RIFF" + b"\x00\x00\x00\x00" + b"XXXX" + b"\x00" * 100
        assert validate_magic_bytes(data, "image/webp") is False

    def test_unknown_mime_allowed(self):
        data = b"\x00" * 100
        assert validate_magic_bytes(data, "text/plain") is True

    def test_empty_data_rejected(self):
        assert validate_magic_bytes(b"", "image/jpeg") is False
        assert validate_magic_bytes(b"\xff", "image/jpeg") is False

    def test_pdf_valid(self):
        data = b"%PDF-1.4" + b"\x00" * 100
        assert validate_magic_bytes(data, "application/pdf") is True


class TestMimeFromExtension(TestCase):
    def test_known_extensions(self):
        assert mime_from_extension("photo.jpg") == "image/jpeg"
        assert mime_from_extension("photo.jpeg") == "image/jpeg"
        assert mime_from_extension("image.png") == "image/png"
        assert mime_from_extension("doc.pdf") == "application/pdf"

    def test_unknown_extension(self):
        assert mime_from_extension("file.xyz") is None

    def test_case_insensitive(self):
        assert mime_from_extension("PHOTO.JPG") == "image/jpeg"


class TestUploadConfig(TestCase):
    def test_basic_config(self):
        config = UploadConfig(name="avatar", accept=".jpg,.png", max_entries=1)
        assert config.accepted_extensions == {".jpg", ".png"}
        assert "image/jpeg" in config.accepted_mimes
        assert "image/png" in config.accepted_mimes

    def test_validate_extension(self):
        config = UploadConfig(name="avatar", accept=".jpg,.png")
        assert config.validate_extension("photo.jpg") is True
        assert config.validate_extension("photo.gif") is False

    def test_validate_extension_no_restriction(self):
        config = UploadConfig(name="files")
        assert config.validate_extension("anything.xyz") is True

    def test_validate_mime(self):
        config = UploadConfig(name="avatar", accept=".jpg,.png")
        assert config.validate_mime("image/jpeg") is True
        assert config.validate_mime("image/png") is True
        assert config.validate_mime("image/gif") is False

    def test_mime_wildcard(self):
        config = UploadConfig(name="images", accept="image/*")
        assert config.validate_mime("image/jpeg") is True
        assert config.validate_mime("image/png") is True
        assert config.validate_mime("application/pdf") is False


class TestUploadEntry(TestCase):
    def test_add_chunks_and_data(self):
        entry = UploadEntry(
            ref="test-ref",
            upload_name="avatar",
            client_name="photo.jpg",
            client_type="image/jpeg",
            client_size=100,
        )
        entry.add_chunk(0, b"hello")
        entry.add_chunk(1, b"world")
        assert entry.data == b"helloworld"
        assert entry.progress == 10  # 10/100

    def test_progress_calculation(self):
        entry = UploadEntry(
            ref="test-ref",
            upload_name="avatar",
            client_name="photo.jpg",
            client_type="image/jpeg",
            client_size=200,
        )
        entry.add_chunk(0, b"x" * 100)
        assert entry.progress == 50

        entry.add_chunk(1, b"x" * 100)
        assert entry.progress == 100

    def test_progress_zero_size(self):
        entry = UploadEntry(
            ref="test-ref",
            upload_name="avatar",
            client_name="empty.txt",
            client_type="text/plain",
            client_size=0,
        )
        assert entry.progress == 100

    def test_file_property(self):
        entry = UploadEntry(
            ref="test-ref",
            upload_name="avatar",
            client_name="photo.jpg",
            client_type="image/jpeg",
            client_size=11,
        )
        entry.add_chunk(0, b"hello")
        entry.add_chunk(1, b" world")
        f = entry.file
        assert f.read() == b"hello world"

    def test_finalize_and_cleanup(self):
        entry = UploadEntry(
            ref="test-ref",
            upload_name="files",
            client_name="test.txt",
            client_type="text/plain",
            client_size=5,
        )
        entry.add_chunk(0, b"hello")

        with tempfile.TemporaryDirectory() as tmpdir:
            assert entry.finalize(tmpdir) is True
            assert entry.complete is True
            assert entry._temp_path is not None
            assert os.path.exists(entry._temp_path)
            assert entry.data == b"hello"

            entry.cleanup()
            assert not os.path.exists(entry._temp_path)


class TestUploadManager(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = UploadManager(temp_dir=self.tmpdir)

    def tearDown(self):
        self.mgr.cleanup()
        if os.path.isdir(self.tmpdir):
            os.rmdir(self.tmpdir)

    def test_configure_and_register(self):
        self.mgr.configure("avatar", accept=".jpg,.png", max_entries=1, max_file_size=1000)
        entry = self.mgr.register_entry(
            "avatar", "ref-1", "photo.jpg", "image/jpeg", 500
        )
        assert entry is not None
        assert entry.ref == "ref-1"

    def test_register_rejected_wrong_extension(self):
        self.mgr.configure("avatar", accept=".jpg")
        entry = self.mgr.register_entry(
            "avatar", "ref-1", "photo.png", "image/png", 500
        )
        assert entry is None

    def test_register_rejected_too_large(self):
        self.mgr.configure("avatar", max_file_size=100)
        entry = self.mgr.register_entry(
            "avatar", "ref-1", "big.jpg", "image/jpeg", 1000
        )
        assert entry is None

    def test_register_rejected_max_entries(self):
        self.mgr.configure("avatar", max_entries=1)
        self.mgr.register_entry("avatar", "ref-1", "a.jpg", "image/jpeg", 100)
        entry2 = self.mgr.register_entry("avatar", "ref-2", "b.jpg", "image/jpeg", 100)
        assert entry2 is None

    def test_add_chunk_and_complete(self):
        self.mgr.configure("files", max_file_size=1000)
        self.mgr.register_entry("files", "ref-1", "test.txt", "text/plain", 10)
        progress = self.mgr.add_chunk("ref-1", 0, b"0123456789")
        assert progress == 100

        entry = self.mgr.complete_upload("ref-1")
        assert entry is not None
        assert entry.complete is True
        assert entry.data == b"0123456789"

    def test_cancel_upload(self):
        self.mgr.configure("files")
        self.mgr.register_entry("files", "ref-1", "test.txt", "text/plain", 10)
        self.mgr.cancel_upload("ref-1")
        assert self.mgr.get_entries("files") == []

    def test_consume_entries(self):
        self.mgr.configure("files", max_entries=2)
        self.mgr.register_entry("files", "ref-1", "a.txt", "text/plain", 5)
        self.mgr.register_entry("files", "ref-2", "b.txt", "text/plain", 5)
        self.mgr.add_chunk("ref-1", 0, b"hello")
        self.mgr.add_chunk("ref-2", 0, b"world")
        self.mgr.complete_upload("ref-1")
        self.mgr.complete_upload("ref-2")

        consumed = list(self.mgr.consume_entries("files"))
        assert len(consumed) == 2
        assert consumed[0].client_name == "a.txt"
        assert consumed[1].client_name == "b.txt"

        # After consuming, entries are gone
        assert list(self.mgr.consume_entries("files")) == []

    def test_get_upload_state(self):
        self.mgr.configure("avatar", accept=".jpg", max_entries=1)
        self.mgr.register_entry("avatar", "ref-1", "photo.jpg", "image/jpeg", 500)
        state = self.mgr.get_upload_state()
        assert "avatar" in state
        assert len(state["avatar"]["entries"]) == 1
        assert state["avatar"]["entries"][0]["client_name"] == "photo.jpg"

    def test_size_limit_enforced_on_chunks(self):
        self.mgr.configure("files", max_file_size=10)
        self.mgr.register_entry("files", "ref-1", "test.txt", "text/plain", 5)
        # Try to add more data than max_file_size
        result = self.mgr.add_chunk("ref-1", 0, b"x" * 20)
        assert result is None  # Rejected


class TestParseUploadFrame(TestCase):
    def _make_ref_bytes(self):
        return uuid.uuid4().bytes

    def test_parse_chunk_frame(self):
        ref_bytes = self._make_ref_bytes()
        chunk_data = b"hello world"
        chunk_index = 5

        frame = bytes([FRAME_CHUNK]) + ref_bytes + struct.pack(">I", chunk_index) + chunk_data
        result = parse_upload_frame(frame)

        assert result is not None
        assert result["type"] == "chunk"
        assert result["chunk_index"] == 5
        assert result["data"] == b"hello world"

    def test_parse_complete_frame(self):
        ref_bytes = self._make_ref_bytes()
        frame = bytes([FRAME_COMPLETE]) + ref_bytes
        result = parse_upload_frame(frame)

        assert result is not None
        assert result["type"] == "complete"

    def test_parse_cancel_frame(self):
        ref_bytes = self._make_ref_bytes()
        frame = bytes([FRAME_CANCEL]) + ref_bytes
        result = parse_upload_frame(frame)

        assert result is not None
        assert result["type"] == "cancel"

    def test_parse_invalid_frame(self):
        assert parse_upload_frame(b"") is None
        assert parse_upload_frame(b"\x00" * 5) is None

    def test_parse_unknown_frame_type(self):
        ref_bytes = self._make_ref_bytes()
        frame = bytes([0xFF]) + ref_bytes
        assert parse_upload_frame(frame) is None


class TestBuildProgressMessage(TestCase):
    def test_basic(self):
        msg = build_progress_message("ref-1", 50)
        assert msg["type"] == "upload_progress"
        assert msg["ref"] == "ref-1"
        assert msg["progress"] == 50
        assert msg["status"] == "uploading"

    def test_complete(self):
        msg = build_progress_message("ref-1", 100, "complete")
        assert msg["status"] == "complete"


class TestUploadMixin(TestCase):
    def test_mixin_allow_upload(self):
        class FakeView(UploadMixin):
            pass

        view = FakeView()
        config = view.allow_upload("avatar", accept=".jpg,.png", max_entries=1)
        assert config.name == "avatar"
        assert config.max_entries == 1

    def test_mixin_consume_empty(self):
        class FakeView(UploadMixin):
            pass

        view = FakeView()
        view.allow_upload("avatar")
        assert list(view.consume_uploaded_entries("avatar")) == []

    def test_mixin_full_flow(self):
        class FakeView(UploadMixin):
            pass

        view = FakeView()
        view.allow_upload("files", max_entries=2, max_file_size=1000)

        mgr = view._upload_manager
        mgr.register_entry("files", "ref-1", "a.txt", "text/plain", 5)
        mgr.add_chunk("ref-1", 0, b"hello")
        mgr.complete_upload("ref-1")

        # Read data during consumption (before cleanup)
        results = []
        for entry in view.consume_uploaded_entries("files"):
            results.append((entry.client_name, entry.data))
        assert len(results) == 1
        assert results[0] == ("a.txt", b"hello")

    def test_mixin_cleanup(self):
        class FakeView(UploadMixin):
            pass

        view = FakeView()
        view.allow_upload("files")
        view._cleanup_uploads()
        assert view._upload_manager is None

    def test_mixin_get_uploads(self):
        class FakeView(UploadMixin):
            pass

        view = FakeView()
        view.allow_upload("files")
        assert view.get_uploads("files") == []

    def test_mixin_cancel(self):
        class FakeView(UploadMixin):
            pass

        view = FakeView()
        view.allow_upload("files")
        view._upload_manager.register_entry("files", "ref-1", "a.txt", "text/plain", 5)
        view.cancel_upload("files", "ref-1")
        assert view.get_uploads("files") == []

    def test_upload_context(self):
        class FakeView(UploadMixin):
            pass

        view = FakeView()
        view.allow_upload("avatar", accept=".jpg")
        ctx = view._get_upload_context()
        assert "uploads" in ctx
        assert "avatar" in ctx["uploads"]
