"""Regression tests for GCSMultipartWriter (#822).

The google-cloud-storage SDK is mocked via ``client=`` injection and
``requests.put`` is patched at the ``_put_range`` seam so we exercise
all four lifecycle paths (init, write, finalize, abort) without needing
``pip install google-cloud-storage``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from djust.contrib.uploads.errors import (
    UploadCredentialError,
    UploadNetworkError,
    UploadQuotaError,
)
from djust.contrib.uploads.gcs import GCSMultipartWriter


def _make_writer(**overrides):
    """Factory: GCSMultipartWriter subclass with mocked SDK client.

    The mock blob exposes ``create_resumable_upload_session`` returning
    a session URL, and ``generation`` as a fake numeric generation.
    """
    blob = MagicMock()
    blob.create_resumable_upload_session = MagicMock(
        return_value="https://storage.googleapis.com/upload/session/abc"
    )
    blob.generation = 1234567890
    bucket = MagicMock()
    bucket.blob = MagicMock(return_value=blob)
    client = MagicMock()
    client.bucket = MagicMock(return_value=bucket)

    attrs = {
        "bucket_name": "my-bucket",
        "key_prefix": "prefix/",
        "client": client,
    }
    attrs.update(overrides)
    Cls = type("TestGCSWriter", (GCSMultipartWriter,), attrs)
    return Cls, client, blob


class TestGCSInit:
    def test_open_creates_resumable_session(self):
        Cls, client, blob = _make_writer()
        w = Cls("upload-1", "photo.jpg", "image/jpeg", expected_size=1000)
        with patch("requests.put"), patch("requests.delete"):
            w.open()
        blob.create_resumable_upload_session.assert_called_once_with(
            content_type="image/jpeg", size=1000
        )
        assert w._session_url is not None
        assert w._key.startswith("prefix/")
        assert w._key.endswith("-photo.jpg")

    def test_open_requires_bucket_name(self):
        Cls, _, _ = _make_writer(bucket_name="")
        w = Cls("u", "f.bin", "application/octet-stream")
        with pytest.raises(ValueError):
            w.open()

    def test_open_sanitizes_filename(self):
        """Client-supplied path traversal gets stripped by Path(filename).name."""
        Cls, _, _ = _make_writer()
        w = Cls("u", "/etc/passwd/../evil.jpg", "image/jpeg")
        with patch("requests.put"), patch("requests.delete"):
            w.open()
        # Path(...).name of '/etc/passwd/../evil.jpg' == 'evil.jpg'
        assert w._key.endswith("-evil.jpg")
        assert "/etc" not in w._key


class TestGCSWriteChunk:
    def test_write_chunk_puts_range_header(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        mock_put = MagicMock()
        mock_put.return_value = MagicMock(status_code=308, text="")
        with patch("requests.put", mock_put), patch("requests.delete"):
            w.open()
            w.write_chunk(b"hello")
        # First write: bytes 0-4/*
        last_call = mock_put.call_args_list[0]
        headers = last_call.kwargs.get("headers", {})
        assert headers["Content-Range"] == "bytes 0-4/*"
        assert w._offset == 5

    def test_write_chunk_advances_offset(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        mock_put = MagicMock(return_value=MagicMock(status_code=308, text=""))
        with patch("requests.put", mock_put), patch("requests.delete"):
            w.open()
            w.write_chunk(b"a" * 100)
            w.write_chunk(b"b" * 50)
        assert w._offset == 150
        # Second call: bytes 100-149/*
        headers = mock_put.call_args_list[1].kwargs["headers"]
        assert headers["Content-Range"] == "bytes 100-149/*"

    def test_write_chunk_after_open_required(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        # No open() called
        with pytest.raises(RuntimeError):
            w.write_chunk(b"x")

    def test_write_chunk_empty_is_noop(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        mock_put = MagicMock()
        with patch("requests.put", mock_put), patch("requests.delete"):
            w.open()
            w.write_chunk(b"")
        mock_put.assert_not_called()

    def test_non_retryable_status_raises_network_error(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        resp = MagicMock(status_code=400, text="bad range")
        with patch("requests.put", return_value=resp), patch("requests.delete"):
            w.open()
            with pytest.raises(UploadNetworkError):
                w.write_chunk(b"chunk")

    def test_401_translates_to_credential_error(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        resp = MagicMock(status_code=401, text="auth")
        with patch("requests.put", return_value=resp), patch("requests.delete"):
            w.open()
            with pytest.raises(UploadCredentialError):
                w.write_chunk(b"x")

    def test_429_translates_to_quota_error(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        # 429 hits the retry loop — exhaust retries then raise
        resp = MagicMock(status_code=429, text="slow down")
        with patch("requests.put", return_value=resp), patch("requests.delete"):
            w.open()
            with pytest.raises(UploadQuotaError):
                w.write_chunk(b"x")


class TestGCSFinalize:
    def test_close_finalizes_with_total_size_header(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        # First write returns 308 (in progress); final PUT returns 200.
        in_progress = MagicMock(status_code=308, text="")
        final = MagicMock(status_code=200, text="")
        put_mock = MagicMock(side_effect=[in_progress, final])
        with patch("requests.put", put_mock), patch("requests.delete"):
            w.open()
            w.write_chunk(b"abcde")  # 5 bytes
            result = w.close()
        # Final call's Content-Range must be bytes */5 (zero-byte finalize).
        final_headers = put_mock.call_args_list[1].kwargs["headers"]
        assert final_headers["Content-Range"] == "bytes */5"
        assert result["bucket"] == "my-bucket"
        assert result["size"] == 5
        assert result["generation"] == 1234567890
        assert result["url"].startswith("gs://my-bucket/")

    def test_close_is_idempotent(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        put_mock = MagicMock(
            side_effect=[
                MagicMock(status_code=308, text=""),
                MagicMock(status_code=200, text=""),
            ]
        )
        with patch("requests.put", put_mock), patch("requests.delete"):
            w.open()
            w.write_chunk(b"abc")
            first = w.close()
            second = w.close()
        assert first == second


class TestGCSAbort:
    def test_abort_deletes_session(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        delete_mock = MagicMock()
        with (
            patch("requests.put", return_value=MagicMock(status_code=308, text="")),
            patch("requests.delete", delete_mock),
        ):
            w.open()
            w.abort(Exception("cancelled"))
        delete_mock.assert_called_once()

    def test_abort_swallows_delete_failure(self):
        """Base-class contract: abort() must never raise."""
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        with (
            patch("requests.put", return_value=MagicMock(status_code=308, text="")),
            patch("requests.delete", side_effect=RuntimeError("network down")),
        ):
            w.open()
            # Must not raise.
            w.abort(Exception("x"))

    def test_abort_noop_after_finalize(self):
        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        delete_mock = MagicMock()
        put_mock = MagicMock(
            side_effect=[
                MagicMock(status_code=308, text=""),
                MagicMock(status_code=200, text=""),
            ]
        )
        with patch("requests.put", put_mock), patch("requests.delete", delete_mock):
            w.open()
            w.write_chunk(b"x")
            w.close()
            w.abort(Exception("too late"))
        # Should not DELETE after a successful close.
        delete_mock.assert_not_called()


class TestGCSResultShape:
    def test_result_is_json_serializable(self):
        """close() return MUST round-trip through DjangoJSONEncoder — the
        upload manager enforces this and a non-serializable return
        would break state updates (see UploadWriter.close docstring)."""
        import json as _json

        from django.core.serializers.json import DjangoJSONEncoder

        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        put_mock = MagicMock(
            side_effect=[
                MagicMock(status_code=308, text=""),
                MagicMock(status_code=200, text=""),
            ]
        )
        with patch("requests.put", put_mock), patch("requests.delete"):
            w.open()
            w.write_chunk(b"x")
            result = w.close()
        # Should not raise.
        _json.dumps(result, cls=DjangoJSONEncoder)
