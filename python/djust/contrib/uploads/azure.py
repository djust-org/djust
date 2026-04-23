"""Azure Blob Storage ``UploadWriter`` (#822).

``AzureBlockBlobWriter`` stages chunks as uncommitted blocks via
``BlobClient.stage_block``, tracks the block IDs, and commits them as
a single block blob via ``BlobClient.commit_block_list``.

Azure's block-blob model:

1. Each block has a developer-chosen ID (up to 64 bytes, same length
   for all blocks in a commit). Ours are hex-encoded 4-digit counters
   padded with zeros — ``0001``, ``0002``, ... This gives us 10000
   blocks per upload, which at our default 64 KB chunk ceiling caps
   one upload at ~640 MB. Apps that need larger uploads should
   increase the client-side chunk size; Azure supports up to 4000
   MiB per block and 50000 blocks per blob.
2. Uncommitted blocks auto-expire after 7 days — abort() just needs
   to not-commit; no explicit server-side cleanup required. (We also
   drop the local block-ID list so the object is GC'd.)

All SDK exceptions translate to ``djust.uploads.UploadError`` subclasses
before propagation.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional
from uuid import uuid4

from djust.contrib.uploads.errors import (
    UploadCredentialError,
    UploadNetworkError,
    UploadQuotaError,
)
from djust.uploads import UploadWriter

if TYPE_CHECKING:  # pragma: no cover
    # Only imported for type-check; runtime imports are guarded inside
    # _get_blob_client() so this module stays import-clean without the
    # azure-storage-blob extra.
    pass

logger = logging.getLogger(__name__)


class AzureBlockBlobWriter(UploadWriter):
    """UploadWriter backed by Azure Block Blob staged-block commits.

    Subclass and set ``container_name`` (and optionally ``key_prefix``
    and ``account_url`` / ``credential``) to use::

        class MyAzureWriter(AzureBlockBlobWriter):
            account_url = "https://myaccount.blob.core.windows.net"
            container_name = "uploads"
            credential = "<sas-token-or-StorageSharedKeyCredential>"

    ``close()`` returns a dict::

        {
            "account_url": "https://myaccount.blob.core.windows.net",
            "container": "uploads",
            "key": "abc123-portrait.jpg",
            "size": 123456,
            "url": "https://myaccount.blob.core.windows.net/uploads/abc123-portrait.jpg",
            "etag": "<server-returned-etag>",
        }
    """

    account_url: str = ""
    container_name: str = ""
    credential: Optional[Any] = None
    key_prefix: str = ""

    # Override to inject a pre-built ``BlobServiceClient`` (tests).
    service_client: Optional[Any] = None

    def __init__(
        self,
        upload_id: str,
        filename: str,
        content_type: str,
        expected_size: Optional[int] = None,
    ) -> None:
        super().__init__(upload_id, filename, content_type, expected_size)
        self._key: Optional[str] = None
        self._blob_client: Optional[Any] = None
        self._block_ids: List[str] = []
        self._bytes_staged: int = 0
        self._finalized: bool = False
        self._commit_result: Optional[dict] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _get_blob_client(self) -> Any:
        if self._blob_client is not None:
            return self._blob_client
        if self.service_client is not None:
            client = self.service_client.get_blob_client(
                container=self.container_name, blob=self._key
            )
            self._blob_client = client
            return client
        try:
            from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "azure-storage-blob is required for AzureBlockBlobWriter. "
                "Install with: pip install djust[azure]"
            ) from exc
        if not self.account_url:
            raise ValueError("AzureBlockBlobWriter.account_url must be set on the subclass")
        service = BlobServiceClient(account_url=self.account_url, credential=self.credential)
        self._blob_client = service.get_blob_client(container=self.container_name, blob=self._key)
        return self._blob_client

    def _build_key(self) -> str:
        safe = Path(self.filename).name or "upload"
        return f"{self.key_prefix}{uuid4()}-{safe}"

    def _next_block_id(self) -> str:
        """Azure requires block IDs to be base64-encoded strings of
        identical length within a single commit. We use a zero-padded
        counter encoded as base64 to satisfy both constraints."""
        idx = len(self._block_ids) + 1
        raw = f"{idx:08d}".encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def open(self) -> None:
        if not self.container_name:
            raise ValueError("AzureBlockBlobWriter.container_name must be set on the subclass")
        self._key = self._build_key()
        # Instantiate the client eagerly so config errors surface at
        # open() rather than on the first chunk.
        try:
            self._get_blob_client()
        except (ImportError, ValueError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate_sdk_exc(exc) from exc

    def write_chunk(self, chunk: bytes) -> None:
        if self._finalized:
            raise RuntimeError("write_chunk() called on a finalized AzureBlockBlobWriter")
        if not chunk:
            return
        client = self._get_blob_client()
        block_id = self._next_block_id()
        try:
            client.stage_block(block_id=block_id, data=chunk)
        except Exception as exc:  # noqa: BLE001
            raise self._translate_sdk_exc(exc) from exc
        self._block_ids.append(block_id)
        self._bytes_staged += len(chunk)

    def close(self) -> Any:
        if self._finalized:
            return self._commit_result
        if not self._block_ids:
            # Zero-byte file: commit an empty block list, which yields
            # a 0-byte block blob.
            pass
        client = self._get_blob_client()
        try:
            commit_resp = client.commit_block_list(self._block_ids)
        except Exception as exc:  # noqa: BLE001
            raise self._translate_sdk_exc(exc) from exc
        etag = ""
        if isinstance(commit_resp, dict):
            etag = commit_resp.get("etag", "") or commit_resp.get("ETag", "")
        else:
            etag = getattr(commit_resp, "etag", "") or ""
        url = f"{self.account_url}/{self.container_name}/{self._key}"
        self._commit_result = {
            "account_url": self.account_url,
            "container": self.container_name,
            "key": self._key,
            "size": self._bytes_staged,
            "url": url,
            "etag": str(etag).strip('"') if etag else "",
        }
        self._finalized = True
        return self._commit_result

    def abort(self, error: BaseException) -> None:
        """Discard the staged block list.

        Azure auto-garbage-collects uncommitted blocks after 7 days, so
        we don't need to DELETE anything server-side. We just clear the
        local block-ID list so nobody accidentally commits them later.
        ``abort()`` MUST NOT raise per the base-class contract.
        """
        self._block_ids = []
        self._finalized = True

    # ------------------------------------------------------------------
    # Error translation
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_sdk_exc(exc: BaseException) -> Exception:
        """Translate azure.core / azure.storage exceptions into the taxonomy.

        The azure.core.exceptions hierarchy we care about:

        - ``ClientAuthenticationError`` → ``UploadCredentialError``
        - ``ResourceExistsError`` / ``HttpResponseError`` with 409/412
          → ``UploadNetworkError`` (conflict during commit is usually
          a retry signal, not an auth issue)
        - ``ServiceRequestError`` → ``UploadNetworkError``
        - Throttling (``ResourceExhausted`` equivalent, HTTP 503 +
          specific error code) → ``UploadQuotaError``
        """
        cls_name = type(exc).__name__
        if cls_name in ("ClientAuthenticationError", "Unauthorized"):
            return UploadCredentialError("Azure credentials rejected", sdk_exc=exc)
        # Azure often signals throttling via HttpResponseError with
        # ``status_code`` 503 and a specific ``error_code``. Check for
        # both the 429 and 503 shapes.
        status = getattr(exc, "status_code", None)
        if status in (429, 503):
            return UploadQuotaError(f"Azure throttling (HTTP {status})", sdk_exc=exc)
        if status in (401, 403):
            return UploadCredentialError(f"Azure rejected auth (HTTP {status})", sdk_exc=exc)
        return UploadNetworkError(f"Azure request failed: {cls_name}", sdk_exc=exc)
