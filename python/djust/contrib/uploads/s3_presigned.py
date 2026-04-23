"""Pre-signed S3 PUT URLs — client-direct S3 uploads (#820).

This module implements a fundamentally different upload flow from
``UploadWriter`` subclasses: the client uploads bytes *directly* to S3
via a pre-signed PUT URL, bypassing the djust WebSocket entirely. djust
only:

1. **Signs the URL** — fast (pure-crypto, no network I/O).
2. **Observes completion** — via an S3 event notification POSTed by
   S3/SNS to a webhook endpoint; djust fires an
   ``on_upload_complete(...)`` hook on the subscribed view.

Flow::

    ┌────────┐  1. register upload     ┌────────┐
    │ client │ ──────────────────────► │ djust  │
    │        │                         │        │
    │        │ ◄────────────────────── │        │  2. {url, key, fields}
    │        │                         └────────┘
    │        │
    │        │       3. PUT bytes      ┌────────┐
    │        │ ──────────────────────► │   S3   │
    │        │ ◄────────────────────── │        │  4. 200 OK
    └────────┘                         └────────┘
                                            │
                                            │  5. ObjectCreated event
                                            ▼
                                       ┌────────┐
                                       │ djust  │  6. on_upload_complete(...)
                                       │webhook │
                                       └────────┘

Why: for large files (>50 MB, video, ZIP archives, AI model checkpoints),
streaming bytes through the djust WebSocket saturates the app server
and ties up a worker for the duration of the upload. With presigned
PUTs, the client talks directly to S3 — djust never sees the bytes and
the WebSocket stays free for other view events.

This module has two public surfaces:

- ``PresignedS3Upload`` — stateless helper to sign URLs and build the
  client spec. Not a ``UploadWriter`` subclass (no chunks, no lifecycle
  from djust's side).
- ``sign_put_url(bucket, key, ...)``, ``build_upload_spec(bucket,
  key_template, ...)`` — convenience module-level functions over a
  default ``PresignedS3Upload`` instance.

For the webhook side, see ``djust.contrib.uploads.s3_events``.

Install the ``s3`` extra::

    pip install djust[s3]
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from djust.contrib.uploads.errors import (
    UploadCredentialError,
    UploadError,
    UploadNetworkError,
)

if TYPE_CHECKING:  # pragma: no cover
    # boto3's own stubs; we only import at type-check time so the module
    # stays import-clean without the ``s3`` extra installed.
    pass

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Key-template rendering
# ----------------------------------------------------------------------

# Keys are {name}-style placeholders. We do NOT support arbitrary format
# specs — keeping the mini-language tight avoids surprises like
# ``{filename!r}`` injecting Python repr into an S3 key. Whitelist just
# bare {name}.
_KEY_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _render_key(key_template: str, **fields: Any) -> str:
    """Render a key template like ``uploads/{uuid}-{filename}``.

    Only bare ``{name}`` placeholders are supported. Unknown placeholders
    raise ``KeyError`` (fail loud — a silent empty key is worse than a
    stacktrace at dev time).

    Security: ``filename`` is attacker-controlled — the caller is
    responsible for using ``Path(filename).name`` before passing it in,
    matching the pattern in ``BufferedUploadWriter``'s docstring.
    """

    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in fields:
            raise KeyError(f"key_template references unknown field: {name!r}")
        return str(fields[name])

    return _KEY_PLACEHOLDER.sub(_replace, key_template)


# ----------------------------------------------------------------------
# PresignedS3Upload
# ----------------------------------------------------------------------


class PresignedS3Upload:
    """Signs pre-signed S3 PUT URLs and builds client-side upload specs.

    Intentionally *not* a ``UploadWriter`` subclass — the flow is
    fundamentally different (no chunks, no per-chunk lifecycle, no
    server-side buffering). Application code instantiates this helper
    once (e.g. at module scope) and reuses it across views.

    Example::

        from djust.contrib.uploads.s3_presigned import PresignedS3Upload

        signer = PresignedS3Upload(bucket="my-uploads", region="us-east-1")

        class ReportView(LiveView):
            def request_upload(self, filename: str, size: int, **_):
                spec = signer.build_upload_spec(
                    bucket=signer.bucket,
                    key_template="reports/{uuid}-{filename}",
                    filename=filename,
                    content_type="application/pdf",
                )
                # spec = {"mode": "presigned", "url": ..., "key": ..., "fields": {...}}
                self.upload_spec = spec

    The corresponding JS (in ``static/djust/src/15-uploads.js``) receives
    ``spec`` and does ``fetch(spec.url, {method: 'PUT', body: file})``,
    with progress reported via ``XMLHttpRequest.upload.onprogress`` (fetch
    streams don't expose upload progress yet).
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        *,
        client: Any = None,
        default_expires_in: int = 3600,
    ) -> None:
        self.bucket = bucket
        self.region = region
        self._client = client
        self.default_expires_in = default_expires_in

    def _get_client(self) -> Any:
        """Lazy boto3 client init — keeps import clean without ``boto3``.

        If boto3 isn't installed but a signing call is made, raise
        ``ImportError`` with the right ``pip install`` hint.
        """
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover — exercised in tests via mock
            raise ImportError(
                "boto3 is required for PresignedS3Upload. Install with: pip install djust[s3]"
            ) from exc
        self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def sign_put_url(
        self,
        bucket: str,
        key: str,
        *,
        expires_in: int = 3600,
        content_type: Optional[str] = None,
    ) -> str:
        """Return a presigned PUT URL for ``bucket/key``.

        The URL is valid for ``expires_in`` seconds. If ``content_type``
        is set, the signature binds it — the client's PUT request MUST
        include matching ``Content-Type`` or S3 will reject with 403.
        """
        client = self._get_client()
        params: Dict[str, Any] = {"Bucket": bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        try:
            url = client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expires_in,
                HttpMethod="PUT",
            )
        except Exception as exc:  # noqa: BLE001 — translate every SDK error
            raise self._translate_sdk_exc(exc) from exc
        return url

    def build_upload_spec(
        self,
        bucket: str,
        key_template: str,
        *,
        filename: str = "",
        content_type: Optional[str] = None,
        expires_in: Optional[int] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Render ``key_template``, sign a PUT URL, and return a client spec.

        The returned dict is shaped for the ``mode: 'presigned'`` branch
        in ``static/djust/src/15-uploads.js``::

            {
                "mode": "presigned",
                "url": "<signed-put-url>",
                "key": "<rendered-s3-key>",
                "fields": {
                    "Content-Type": "<content_type>"  # if set
                    ...any extra_fields
                }
            }

        ``key_template`` placeholders:

        - ``{uuid}`` — server-generated ``uuid4`` (not derived from
          client input; SAFE as a namespacing prefix)
        - ``{filename}`` — ``Path(filename).name``, stripped of any
          directory components. Still attacker-controlled so don't trust
          it for ACL decisions, but safe as a display suffix.
        - ``{content_type}`` — if passed; may contain ``/`` so don't use
          this in path-sensitive positions.

        Any ``extra_fields`` get merged into the returned ``fields``
        dict verbatim.
        """
        safe_filename = Path(filename).name if filename else ""
        rendered_key = _render_key(
            key_template,
            uuid=uuid.uuid4(),
            filename=safe_filename,
            content_type=content_type or "",
        )
        expires = expires_in if expires_in is not None else self.default_expires_in
        url = self.sign_put_url(
            bucket,
            rendered_key,
            expires_in=expires,
            content_type=content_type,
        )
        fields: Dict[str, Any] = {}
        if content_type:
            fields["Content-Type"] = content_type
        if extra_fields:
            fields.update(extra_fields)
        return {
            "mode": "presigned",
            "url": url,
            "key": rendered_key,
            "fields": fields,
        }

    @staticmethod
    def _translate_sdk_exc(exc: BaseException) -> UploadError:
        """Translate a boto3/botocore exception into our error taxonomy.

        Deliberately conservative: botocore error shapes vary across SDK
        versions, so we inspect ``response["Error"]["Code"]`` when
        available and fall back to network-error shape otherwise.
        """
        code: Optional[str] = None
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = response.get("Error", {}).get("Code")
        if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied", "403"):
            return UploadCredentialError("S3 credentials rejected by the service", sdk_exc=exc)
        return UploadNetworkError(f"S3 presign failed: {type(exc).__name__}", sdk_exc=exc)


# ----------------------------------------------------------------------
# Module-level convenience wrappers
# ----------------------------------------------------------------------


def sign_put_url(
    bucket: str,
    key: str,
    *,
    expires_in: int = 3600,
    content_type: Optional[str] = None,
    region: str = "us-east-1",
    client: Any = None,
) -> str:
    """Convenience: one-shot ``PresignedS3Upload.sign_put_url`` call.

    Construct your own ``PresignedS3Upload`` for multi-URL workflows —
    it caches the boto3 client.
    """
    return PresignedS3Upload(bucket=bucket, region=region, client=client).sign_put_url(
        bucket, key, expires_in=expires_in, content_type=content_type
    )


def build_upload_spec(
    bucket: str,
    key_template: str,
    *,
    filename: str = "",
    content_type: Optional[str] = None,
    expires_in: int = 3600,
    region: str = "us-east-1",
    client: Any = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience: one-shot ``PresignedS3Upload.build_upload_spec`` call."""
    return PresignedS3Upload(
        bucket=bucket, region=region, client=client, default_expires_in=expires_in
    ).build_upload_spec(
        bucket=bucket,
        key_template=key_template,
        filename=filename,
        content_type=content_type,
        expires_in=expires_in,
        extra_fields=extra_fields,
    )
