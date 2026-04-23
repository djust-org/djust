"""Regression tests for AzureBlockBlobWriter (#822).

azure-storage-blob is mocked at the ``service_client`` injection seam —
tests run without ``pip install azure-storage-blob``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from djust.contrib.uploads.azure import AzureBlockBlobWriter
from djust.contrib.uploads.errors import (
    UploadCredentialError,
    UploadNetworkError,
    UploadQuotaError,
)


def _make_writer(**overrides):
    """Factory: AzureBlockBlobWriter subclass with mocked SDK."""
    blob_client = MagicMock()
    blob_client.stage_block = MagicMock()
    blob_client.commit_block_list = MagicMock(return_value={"etag": '"abcd1234"'})
    service_client = MagicMock()
    service_client.get_blob_client = MagicMock(return_value=blob_client)

    attrs = {
        "account_url": "https://acct.blob.core.windows.net",
        "container_name": "uploads",
        "key_prefix": "p/",
        "service_client": service_client,
    }
    attrs.update(overrides)
    Cls = type("TestAzureWriter", (AzureBlockBlobWriter,), attrs)
    return Cls, service_client, blob_client


class TestAzureInit:
    def test_open_resolves_blob_client(self):
        Cls, svc, blob = _make_writer()
        w = Cls("u1", "photo.jpg", "image/jpeg")
        w.open()
        svc.get_blob_client.assert_called_once()
        _, kwargs = svc.get_blob_client.call_args
        assert kwargs["container"] == "uploads"
        assert kwargs["blob"].startswith("p/")
        assert kwargs["blob"].endswith("-photo.jpg")

    def test_open_requires_container_name(self):
        Cls, _, _ = _make_writer(container_name="")
        w = Cls("u", "f.bin", "application/octet-stream")
        with pytest.raises(ValueError):
            w.open()

    def test_open_sanitizes_filename(self):
        Cls, svc, _ = _make_writer()
        w = Cls("u", "../../../evil.sh", "text/plain")
        w.open()
        _, kwargs = svc.get_blob_client.call_args
        assert kwargs["blob"].endswith("-evil.sh")
        assert ".." not in kwargs["blob"]


class TestAzureWriteChunk:
    def test_write_chunk_stages_block(self):
        Cls, _, blob = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        w.write_chunk(b"hello")
        blob.stage_block.assert_called_once()
        kwargs = blob.stage_block.call_args.kwargs
        assert kwargs["data"] == b"hello"
        # block_id is base64-encoded
        import base64

        decoded = base64.b64decode(kwargs["block_id"]).decode()
        assert decoded == "00000001"
        assert w._bytes_staged == 5

    def test_write_chunk_block_ids_unique_and_ordered(self):
        Cls, _, blob = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        w.write_chunk(b"a")
        w.write_chunk(b"bb")
        w.write_chunk(b"ccc")
        assert len(w._block_ids) == 3
        assert len(set(w._block_ids)) == 3
        # All block IDs must have identical length (Azure requirement)
        lengths = {len(bid) for bid in w._block_ids}
        assert len(lengths) == 1

    def test_write_chunk_empty_is_noop(self):
        Cls, _, blob = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        w.write_chunk(b"")
        blob.stage_block.assert_not_called()

    def test_stage_block_auth_error_translates_to_credential(self):
        Cls, _, blob = _make_writer()
        # Build an exception whose class name matches the Azure SDK hierarchy
        AuthError = type("ClientAuthenticationError", (Exception,), {})
        blob.stage_block.side_effect = AuthError("bad sig")
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        with pytest.raises(UploadCredentialError):
            w.write_chunk(b"x")

    def test_stage_block_429_translates_to_quota(self):
        Cls, _, blob = _make_writer()
        HttpErr = type("HttpResponseError", (Exception,), {})
        exc = HttpErr("throttled")
        exc.status_code = 429
        blob.stage_block.side_effect = exc
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        with pytest.raises(UploadQuotaError):
            w.write_chunk(b"x")

    def test_stage_block_generic_error_translates_to_network(self):
        Cls, _, blob = _make_writer()
        blob.stage_block.side_effect = RuntimeError("network glitch")
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        with pytest.raises(UploadNetworkError):
            w.write_chunk(b"x")


class TestAzureFinalize:
    def test_close_commits_block_list(self):
        Cls, _, blob = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        w.write_chunk(b"abc")
        w.write_chunk(b"def")
        result = w.close()
        blob.commit_block_list.assert_called_once()
        (block_ids,) = blob.commit_block_list.call_args.args
        assert block_ids == w._block_ids
        assert result["container"] == "uploads"
        assert result["size"] == 6
        assert result["etag"] == "abcd1234"
        assert result["url"].endswith(w._key)

    def test_close_is_idempotent(self):
        Cls, _, blob = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        w.write_chunk(b"x")
        first = w.close()
        second = w.close()
        assert first == second
        # commit should only be called once despite close() being called twice
        assert blob.commit_block_list.call_count == 1

    def test_close_with_no_chunks_commits_empty(self):
        """Zero-byte upload: no staged blocks, empty commit list."""
        Cls, _, blob = _make_writer()
        w = Cls("u", "empty.txt", "text/plain")
        w.open()
        result = w.close()
        blob.commit_block_list.assert_called_once_with([])
        assert result["size"] == 0


class TestAzureAbort:
    def test_abort_clears_block_ids(self):
        Cls, _, blob = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        w.write_chunk(b"x")
        assert len(w._block_ids) == 1
        w.abort(Exception("cancelled"))
        assert w._block_ids == []
        # commit must NOT be called after abort
        blob.commit_block_list.assert_not_called()

    def test_abort_never_raises(self):
        """Base-class contract."""
        Cls, _, blob = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        # abort must not raise even if called before open()
        w.abort(Exception("early"))


class TestAzureResultShape:
    def test_result_is_json_serializable(self):
        import json as _json

        from django.core.serializers.json import DjangoJSONEncoder

        Cls, _, _ = _make_writer()
        w = Cls("u", "f.bin", "application/octet-stream")
        w.open()
        w.write_chunk(b"x")
        result = w.close()
        _json.dumps(result, cls=DjangoJSONEncoder)
