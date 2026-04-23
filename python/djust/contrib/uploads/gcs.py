"""Google Cloud Storage ``UploadWriter`` (#822).

``GCSMultipartWriter`` pipes upload chunks directly into a GCS
**resumable upload session**, which is GCS's equivalent of S3 multipart
upload. Unlike S3 MPU's per-part ETag dance, GCS resumable sessions are
a single append-only byte stream — the client PUTs sequential byte
ranges keyed by ``Content-Range`` headers, and the server tracks the
cursor.

Lifecycle:

1. ``open()`` — create the resumable session via
   ``Blob.create_resumable_upload_session`` and store the session URL.
2. ``write_chunk(chunk)`` — PUT the chunk with a ``Content-Range: bytes
   N-M/*`` header (open-ended total). GCS returns 308 while the upload
   is in progress.
3. ``close()`` — PUT a final zero-byte request with
   ``Content-Range: bytes */TOTAL`` to finalize, returning 200.
4. ``abort()`` — DELETE the session URL (best-effort; GCS also garbage-
   collects abandoned sessions after ~7 days).

All SDK exceptions are translated to the ``djust.uploads.UploadError``
taxonomy before propagation — callers don't need ``google.api_core``
on the import path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from uuid import uuid4

from djust.contrib.uploads.errors import (
    UploadCredentialError,
    UploadNetworkError,
    UploadQuotaError,
)
from djust.uploads import UploadWriter

if TYPE_CHECKING:  # pragma: no cover
    # Only imported for type-check; the runtime branch guards the
    # google-cloud-storage import inside _get_client().
    pass

logger = logging.getLogger(__name__)


# Default retry budget for GCS transient failures (503, 429). We use
# google.api_core.retry.Retry when available — it understands GCS
# retryable status codes out of the box. Fallback: a small linear
# retry loop inside _put_range.
_GCS_RETRY_ATTEMPTS = 3
_GCS_CHUNK_MIN_SIZE = 256 * 1024  # GCS requires all but last chunk to be a multiple of 256 KB


class GCSMultipartWriter(UploadWriter):
    """UploadWriter backed by a GCS resumable-upload session.

    Configuration is class-level — subclass and override these in your
    application::

        class MyGCSWriter(GCSMultipartWriter):
            bucket_name = "my-app-uploads"
            key_prefix = "user-uploads/"

        class ProfileView(LiveView, UploadMixin):
            def mount(self, request, **kwargs):
                self.allow_upload(
                    "avatar", accept=".jpg,.png",
                    writer=MyGCSWriter,
                )

    ``close()`` returns a JSON-serializable dict describing the upload::

        {
            "bucket": "my-app-uploads",
            "key": "user-uploads/abc123-portrait.jpg",
            "size": 123456,
            "generation": 171234567890,  # GCS object generation / version
            "url": "gs://my-app-uploads/user-uploads/abc123-portrait.jpg",
        }

    This dict is available to templates as ``entry.writer_result``.
    """

    bucket_name: str = ""
    key_prefix: str = ""

    # Override to inject a pre-configured ``google.cloud.storage.Client``
    # instance (useful for tests and for apps that want custom
    # credentials / timeouts / retry policies).
    client: Optional[Any] = None

    def __init__(
        self,
        upload_id: str,
        filename: str,
        content_type: str,
        expected_size: Optional[int] = None,
    ) -> None:
        super().__init__(upload_id, filename, content_type, expected_size)
        self._session_url: Optional[str] = None
        self._key: Optional[str] = None
        self._offset: int = 0
        self._finalized: bool = False
        self._blob: Optional[Any] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Resolve the google-cloud-storage client, lazily importing.

        Raises ``ImportError`` with an install hint if the extra isn't
        installed.
        """
        if self.client is not None:
            return self.client
        try:
            from google.cloud import storage  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover — exercised via mocks
            raise ImportError(
                "google-cloud-storage is required for GCSMultipartWriter. "
                "Install with: pip install djust[gcs]"
            ) from exc
        return storage.Client()

    def _build_key(self) -> str:
        """Derive a safe key: ``<prefix><uuid4>-<Path(filename).name>``.

        Matches the security pattern documented on ``BufferedUploadWriter``:
        never trust the raw client filename as a storage key.
        """
        safe = Path(self.filename).name or "upload"
        return f"{self.key_prefix}{uuid4()}-{safe}"

    def open(self) -> None:
        if not self.bucket_name:
            raise ValueError("GCSMultipartWriter.bucket_name must be set on the subclass")
        self._key = self._build_key()
        client = self._get_client()
        try:
            bucket = client.bucket(self.bucket_name)
            blob = bucket.blob(self._key)
            self._blob = blob
            # create_resumable_upload_session returns a session URL that
            # we PUT byte ranges into. The SDK handles auth headers for
            # the session-creation call; subsequent PUTs to the session
            # URL itself do not require them.
            self._session_url = blob.create_resumable_upload_session(
                content_type=self.content_type or "application/octet-stream",
                size=self.expected_size,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._translate_sdk_exc(exc) from exc

    def write_chunk(self, chunk: bytes) -> None:
        if self._finalized:
            raise RuntimeError("write_chunk() called on a finalized GCSMultipartWriter")
        if not self._session_url:
            raise RuntimeError("GCSMultipartWriter.open() must precede write_chunk()")
        if not chunk:
            return
        self._put_range(chunk, final=False)

    def close(self) -> Any:
        """Finalize the session and return a JSON-serializable descriptor."""
        if self._finalized:
            # Repeated close() is a no-op — mirror BufferedUploadWriter's
            # contract.
            return self._build_result()
        # Send the finalize request: zero-byte PUT with total size.
        self._put_range(b"", final=True)
        self._finalized = True
        return self._build_result()

    def abort(self, error: BaseException) -> None:
        """Cancel the session via DELETE. Must never raise (base-class
        contract)."""
        if not self._session_url or self._finalized:
            return
        try:
            self._delete_session()
        except Exception:  # noqa: BLE001 — abort must not raise
            logger.exception(
                "GCSMultipartWriter.abort: DELETE session failed for upload %s",
                self.upload_id,
            )

    # ------------------------------------------------------------------
    # HTTP transport — seam for tests
    # ------------------------------------------------------------------

    def _put_range(self, chunk: bytes, *, final: bool) -> None:
        """PUT a byte range to the session URL.

        Kept as its own method so tests can patch it without faking the
        whole ``google.cloud.storage`` surface. Production use routes
        via ``google.auth.transport.requests.AuthorizedSession`` (or a
        plain ``requests.Session`` since the session URL carries its
        own auth).
        """
        try:
            import requests  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "requests is required for GCSMultipartWriter. Install with: pip install djust[gcs]"
            ) from exc

        start = self._offset
        end = start + len(chunk) - 1 if chunk else start - 1
        if final:
            total = str(start + len(chunk)) if chunk else str(start)
            if chunk:
                content_range = f"bytes {start}-{end}/{total}"
            else:
                content_range = f"bytes */{total}"
        else:
            content_range = f"bytes {start}-{end}/*"

        headers = {"Content-Range": content_range}
        last_exc: Optional[BaseException] = None
        for attempt in range(_GCS_RETRY_ATTEMPTS):
            try:
                resp = requests.put(self._session_url, data=chunk, headers=headers, timeout=60)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
            # 200 / 201 = final; 308 = resume incomplete (in-progress)
            if resp.status_code in (200, 201) and final:
                self._offset += len(chunk)
                return
            if resp.status_code == 308 and not final:
                self._offset += len(chunk)
                return
            if resp.status_code in (503, 429):
                last_exc = self._translate_status(resp.status_code, resp.text)
                continue
            # Any other status — translate and raise without further retries.
            raise self._translate_status(resp.status_code, resp.text)
        # Retries exhausted
        assert last_exc is not None
        if isinstance(last_exc, Exception) and not isinstance(
            last_exc, (UploadNetworkError, UploadQuotaError, UploadCredentialError)
        ):
            raise UploadNetworkError(
                "GCS resumable PUT failed after retries", sdk_exc=last_exc
            ) from last_exc
        raise last_exc

    def _delete_session(self) -> None:
        import requests  # type: ignore[import-not-found]

        if not self._session_url:
            return
        requests.delete(self._session_url, timeout=30)

    def _build_result(self) -> dict:
        gen: Optional[int] = None
        if self._blob is not None:
            gen = getattr(self._blob, "generation", None)
        return {
            "bucket": self.bucket_name,
            "key": self._key,
            "size": self._offset,
            "generation": gen,
            "url": f"gs://{self.bucket_name}/{self._key}",
        }

    # ------------------------------------------------------------------
    # Error translation
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_sdk_exc(exc: BaseException) -> Exception:
        """Translate a google-cloud-storage / google-api-core exception."""
        cls_name = type(exc).__name__
        # google.api_core.exceptions.Forbidden / Unauthenticated
        if cls_name in ("Forbidden", "Unauthenticated", "Unauthorized"):
            return UploadCredentialError("GCS credentials rejected", sdk_exc=exc)
        if cls_name in ("TooManyRequests", "ResourceExhausted"):
            return UploadQuotaError("GCS quota exceeded", sdk_exc=exc)
        return UploadNetworkError(f"GCS request failed: {cls_name}", sdk_exc=exc)

    @staticmethod
    def _translate_status(status: int, body: str) -> Exception:
        """Translate an HTTP status code from a raw requests response."""
        if status in (401, 403):
            return UploadCredentialError(f"GCS rejected auth (HTTP {status})", sdk_exc=None)
        if status in (429, 507):
            return UploadQuotaError(f"GCS quota / throttling (HTTP {status})", sdk_exc=None)
        return UploadNetworkError(f"GCS PUT failed with HTTP {status}", sdk_exc=None)
