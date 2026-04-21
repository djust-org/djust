"""
Unit tests for UploadWriter, BufferedUploadWriter, and writer= kwarg
integration with allow_upload() / UploadManager.

Covers:
- UploadWriter base class contract (write_chunk NotImplementedError)
- BufferedUploadWriter buffer/flush semantics
- on_part / on_complete return-value bubbling through close()
- allow_upload(writer=X) storage on UploadConfig
- Lazy per-upload instantiation with correct constructor kwargs
- open() called exactly once, before first write_chunk
- write_chunk called once per chunk with exact bytes
- Exception in write_chunk / open() → abort(exc) called, entry failed
- close() return value stored on entry.writer_result
- Size-limit exceeded → writer.abort(...) called
- Concurrent uploads → isolated writer instances
- No disk I/O on writer path
- Legacy disk path unaffected by writer= absence
"""

from __future__ import annotations

from typing import Any, List, Tuple
from unittest.mock import patch

import pytest

from djust.uploads import (
    BufferedUploadWriter,
    UploadConfig,
    UploadEntry,
    UploadManager,
    UploadMixin,
    UploadWriter,
)


# ============================================================================
# Helpers
# ============================================================================


class RecordingWriter(UploadWriter):
    """Records every call; used to verify the lifecycle contract."""

    def __init__(
        self,
        upload_id: str,
        filename: str,
        content_type: str,
        expected_size: int | None = None,
    ):
        super().__init__(upload_id, filename, content_type, expected_size)
        self.opens: int = 0
        self.chunks: List[bytes] = []
        self.closed: bool = False
        self.aborted_with: List[BaseException] = []
        self.close_return: Any = {"ok": True}

    def open(self) -> None:
        self.opens += 1

    def write_chunk(self, chunk: bytes) -> None:
        self.chunks.append(bytes(chunk))

    def close(self) -> Any:
        self.closed = True
        return self.close_return

    def abort(self, error: BaseException) -> None:
        self.aborted_with.append(error)


def _register(mgr: UploadManager, upload_name: str, size: int = 1024) -> UploadEntry:
    """Register a dummy entry in the manager."""
    ref = "00000000-0000-0000-0000-000000000001"
    entry = mgr.register_entry(
        upload_name=upload_name,
        ref=ref,
        client_name="photo.png",
        client_type="image/png",
        client_size=size,
    )
    assert entry is not None
    return entry


# ============================================================================
# UploadWriter base class
# ============================================================================


class TestUploadWriterBase:
    def test_write_chunk_raises_not_implemented(self):
        w = UploadWriter("id", "file.bin", "application/octet-stream", 10)
        with pytest.raises(NotImplementedError):
            w.write_chunk(b"hello")

    def test_base_open_close_abort_are_noops(self):
        w = UploadWriter("id", "f", "t", None)
        # Must not raise.
        assert w.open() is None
        assert w.close() is None
        assert w.abort(RuntimeError("x")) is None

    def test_constructor_fields(self):
        w = UploadWriter("u1", "name.txt", "text/plain", 42)
        assert w.upload_id == "u1"
        assert w.filename == "name.txt"
        assert w.content_type == "text/plain"
        assert w.expected_size == 42


# ============================================================================
# BufferedUploadWriter
# ============================================================================


class PartRecorder(BufferedUploadWriter):
    buffer_threshold = 5  # tiny for deterministic testing

    def __init__(self, *a: Any, **kw: Any):
        super().__init__(*a, **kw)
        self.parts: List[Tuple[bytes, int]] = []
        self.completed: bool = False
        self.complete_return: Any = "DONE"

    def on_part(self, part: bytes, part_num: int) -> None:
        self.parts.append((bytes(part), part_num))

    def on_complete(self) -> Any:
        self.completed = True
        return self.complete_return


class TestBufferedUploadWriter:
    def test_split_across_threshold(self):
        w = PartRecorder("u", "f", "t", None)
        w.write_chunk(b"aaa")
        w.write_chunk(b"bbb")  # now 6 bytes — emits part of 5, 1 left
        assert w.parts == [(b"aaabb", 1)]
        # No further parts until we cross again.
        w.write_chunk(b"c")  # 2 bytes in buf, no emission
        assert len(w.parts) == 1

    def test_exact_multiple_emits_all_parts_no_partial_final(self):
        w = PartRecorder("u", "f", "t", None)
        w.write_chunk(b"12345")
        w.write_chunk(b"67890")
        assert w.parts == [(b"12345", 1), (b"67890", 2)]
        ret = w.close()
        # No leftover bytes, no extra on_part call.
        assert w.parts == [(b"12345", 1), (b"67890", 2)]
        assert w.completed is True
        assert ret == "DONE"

    def test_close_flushes_final_partial(self):
        w = PartRecorder("u", "f", "t", None)
        w.write_chunk(b"1234567")  # 7 bytes → part 1 = 5, buf=2
        ret = w.close()
        assert w.parts == [(b"12345", 1), (b"67", 2)]
        assert ret == "DONE"

    def test_on_part_part_nums_sequential(self):
        w = PartRecorder("u", "f", "t", None)
        # 17 bytes → parts of 5,5,5 + final 2
        w.write_chunk(b"a" * 17)
        w.close()
        assert [n for _, n in w.parts] == [1, 2, 3, 4]

    def test_on_complete_return_bubbles_through_close(self):
        w = PartRecorder("u", "f", "t", None)
        w.complete_return = {"url": "s3://bucket/key"}
        result = w.close()
        assert result == {"url": "s3://bucket/key"}


# ============================================================================
# UploadConfig / allow_upload wiring
# ============================================================================


class _View(UploadMixin):
    """Minimal host for UploadMixin in tests."""


class TestAllowUploadWiring:
    def test_allow_upload_stores_writer_class(self):
        v = _View()
        cfg = v.allow_upload("avatar", writer=RecordingWriter)
        assert isinstance(cfg, UploadConfig)
        assert cfg.writer is RecordingWriter

    def test_allow_upload_without_writer_is_none(self):
        v = _View()
        cfg = v.allow_upload("avatar")
        assert cfg.writer is None


# ============================================================================
# Lifecycle via UploadManager
# ============================================================================


class TestWriterLifecycle:
    def _make_mgr(self, writer_cls=RecordingWriter):
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=writer_cls)
        return mgr

    def test_first_chunk_instantiates_writer_with_correct_kwargs(self):
        mgr = self._make_mgr()
        entry = _register(mgr, "avatar", size=500)
        progress = mgr.add_chunk(entry.ref, 0, b"AAA")
        assert progress is not None
        w = entry.writer_instance
        assert isinstance(w, RecordingWriter)
        assert w.upload_id == entry.ref
        assert w.filename == "photo.png"
        assert w.content_type == "image/png"
        assert w.expected_size == 500

    def test_open_called_exactly_once_across_many_chunks(self):
        mgr = self._make_mgr()
        entry = _register(mgr, "avatar", size=500)
        for i in range(5):
            mgr.add_chunk(entry.ref, i, b"Z")
        assert entry.writer_instance.opens == 1

    def test_write_chunk_exact_bytes_per_chunk(self):
        mgr = self._make_mgr()
        entry = _register(mgr, "avatar", size=500)
        payloads = [b"one", b"two", b"three"]
        for i, p in enumerate(payloads):
            mgr.add_chunk(entry.ref, i, p)
        assert entry.writer_instance.chunks == payloads

    def test_close_return_stored_on_writer_result(self):
        mgr = self._make_mgr()
        entry = _register(mgr, "avatar", size=500)
        entry_added = mgr.add_chunk(entry.ref, 0, b"data")
        assert entry_added is not None
        # Set a distinctive return value.
        entry.writer_instance.close_return = {"url": "https://x/k"}
        result_entry = mgr.complete_upload(entry.ref)
        assert result_entry is entry
        assert entry.writer_result == {"url": "https://x/k"}
        assert entry.complete is True
        assert entry.writer_instance.closed is True


# ============================================================================
# Error paths — abort() contract
# ============================================================================


class OpenRaisesWriter(RecordingWriter):
    def open(self) -> None:
        raise RuntimeError("cannot open")


class WriteChunkRaisesWriter(RecordingWriter):
    def write_chunk(self, chunk: bytes) -> None:
        raise RuntimeError("cannot write")


class CloseRaisesWriter(RecordingWriter):
    def close(self) -> Any:
        raise RuntimeError("cannot close")


class TestWriterErrorPaths:
    def _mgr_for(self, cls):
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=cls)
        return mgr

    def test_open_exception_triggers_abort_no_write_chunk(self):
        mgr = self._mgr_for(OpenRaisesWriter)
        entry = _register(mgr, "avatar", size=100)
        progress = mgr.add_chunk(entry.ref, 0, b"xyz")
        assert progress is None
        w = entry.writer_instance
        assert isinstance(w, OpenRaisesWriter)
        assert w.chunks == []  # write_chunk never called
        assert len(w.aborted_with) == 1
        assert isinstance(w.aborted_with[0], RuntimeError)
        assert entry._error is not None

    def test_write_chunk_exception_triggers_abort(self):
        mgr = self._mgr_for(WriteChunkRaisesWriter)
        entry = _register(mgr, "avatar", size=100)
        progress = mgr.add_chunk(entry.ref, 0, b"xyz")
        assert progress is None
        w = entry.writer_instance
        assert len(w.aborted_with) == 1
        assert isinstance(w.aborted_with[0], RuntimeError)
        assert "cannot write" in str(w.aborted_with[0])
        assert entry._error is not None

    def test_close_exception_triggers_abort(self):
        mgr = self._mgr_for(CloseRaisesWriter)
        entry = _register(mgr, "avatar", size=100)
        mgr.add_chunk(entry.ref, 0, b"xyz")
        result = mgr.complete_upload(entry.ref)
        assert result is None
        w = entry.writer_instance
        assert len(w.aborted_with) == 1
        assert isinstance(w.aborted_with[0], RuntimeError)
        assert entry.complete is False

    def test_abort_swallows_its_own_exceptions(self):
        class AbortRaisesWriter(RecordingWriter):
            def write_chunk(self, chunk: bytes) -> None:
                raise RuntimeError("boom")

            def abort(self, error: BaseException) -> None:
                raise RuntimeError("abort failed")

        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=AbortRaisesWriter)
        entry = _register(mgr, "avatar", size=100)
        # Must not raise even though abort raises internally.
        progress = mgr.add_chunk(entry.ref, 0, b"x")
        assert progress is None

    def test_size_limit_exceeded_aborts_writer(self):
        # Register with a small declared size so it passes the pre-accept
        # size check, but have the client send more bytes than that
        # declared size (and more than max_file_size).
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=10, writer=RecordingWriter)
        entry = _register(mgr, "avatar", size=5)
        # First chunk fits (5 bytes).
        assert mgr.add_chunk(entry.ref, 0, b"12345") is not None
        # Second chunk would exceed (6 more = 11 > 10).
        progress = mgr.add_chunk(entry.ref, 1, b"678901")
        assert progress is None
        w = entry.writer_instance
        assert len(w.aborted_with) == 1
        assert isinstance(w.aborted_with[0], ValueError)

    def test_cancel_upload_aborts_writer(self):
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=RecordingWriter)
        entry = _register(mgr, "avatar", size=20)
        mgr.add_chunk(entry.ref, 0, b"abc")
        w = entry.writer_instance
        mgr.cancel_upload(entry.ref)
        assert len(w.aborted_with) == 1
        assert isinstance(w.aborted_with[0], ConnectionAbortedError)

    def test_session_cleanup_aborts_pending_writers(self):
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=RecordingWriter)
        entry = _register(mgr, "avatar", size=20)
        mgr.add_chunk(entry.ref, 0, b"abc")
        w = entry.writer_instance
        mgr.cleanup()
        assert len(w.aborted_with) == 1
        assert isinstance(w.aborted_with[0], ConnectionAbortedError)

    def test_completed_upload_not_re_aborted_on_cleanup(self):
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=RecordingWriter)
        entry = _register(mgr, "avatar", size=20)
        mgr.add_chunk(entry.ref, 0, b"abc")
        mgr.complete_upload(entry.ref)
        w = entry.writer_instance
        assert len(w.aborted_with) == 0
        mgr.cleanup()
        assert len(w.aborted_with) == 0


# ============================================================================
# Isolation & regression
# ============================================================================


class TestIsolationAndRegression:
    def test_concurrent_uploads_get_isolated_writer_instances(self):
        mgr = UploadManager()
        mgr.configure(name="gallery", max_entries=5, max_file_size=1024, writer=RecordingWriter)

        entries = []
        for i in range(3):
            ref = "00000000-0000-0000-0000-00000000000%d" % (i + 1)
            e = mgr.register_entry(
                upload_name="gallery",
                ref=ref,
                client_name="file%d.png" % i,
                client_type="image/png",
                client_size=100,
            )
            assert e is not None
            entries.append(e)

        for e in entries:
            mgr.add_chunk(e.ref, 0, e.client_name.encode())

        writers = [e.writer_instance for e in entries]
        assert len({id(w) for w in writers}) == 3  # all distinct
        for e, w in zip(entries, writers):
            assert w.chunks == [e.client_name.encode()]
            assert w.upload_id == e.ref
            assert w.filename == e.client_name

    def test_no_temp_file_created_on_writer_path(self):
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=RecordingWriter)
        entry = _register(mgr, "avatar", size=20)
        with patch("djust.uploads.tempfile.mkstemp") as mock_mkstemp:
            mgr.add_chunk(entry.ref, 0, b"hello")
            mgr.complete_upload(entry.ref)
            assert mock_mkstemp.call_count == 0
        # Entry has no temp path.
        assert entry._temp_path is None
        # And the chunks dict was never populated — zero-RAM buffer.
        assert entry._chunks == {}

    def test_legacy_disk_path_still_writes_temp_file(self):
        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024)  # no writer=
        # Use non-magic-validated bytes to avoid the magic-byte check
        # (use an extension without a MAGIC_BYTES mapping).
        ref = "00000000-0000-0000-0000-000000000010"
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=ref,
            client_name="file.dat",
            client_type="application/octet-stream",
            client_size=5,
        )
        assert entry is not None
        mgr.add_chunk(entry.ref, 0, b"hello")
        result = mgr.complete_upload(entry.ref)
        assert result is entry
        # Temp file created (legacy path).
        assert entry._temp_path is not None
        assert entry.complete is True
        assert entry.writer_instance is None
        assert entry.writer_result is None
        # Cleanup.
        entry.cleanup()

    def test_writer_subclass_methods_fire_correctly(self):
        """Subclassing BufferedUploadWriter integrates via allow_upload."""

        class MyBuffered(BufferedUploadWriter):
            buffer_threshold = 4

            def __init__(self, *a: Any, **kw: Any):
                super().__init__(*a, **kw)
                self.parts: List[bytes] = []

            def on_part(self, part: bytes, part_num: int) -> None:
                self.parts.append(bytes(part))

            def on_complete(self) -> Any:
                return "COMPLETE"

        mgr = UploadManager()
        mgr.configure(name="avatar", max_file_size=1024, writer=MyBuffered)
        entry = _register(mgr, "avatar", size=20)
        mgr.add_chunk(entry.ref, 0, b"abcde")  # 5 bytes → part 1 = 4, buf=1
        mgr.add_chunk(entry.ref, 1, b"fg")  # 3 bytes buffered, no part
        mgr.complete_upload(entry.ref)
        assert entry.writer_instance.parts == [b"abcd", b"efg"]
        assert entry.writer_result == "COMPLETE"
