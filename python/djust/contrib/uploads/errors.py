"""Shared error taxonomy for object-storage UploadWriter backends.

All writer backends in ``djust.contrib.uploads`` translate SDK-specific
exceptions into this taxonomy before raising, so application code and
the upload manager's abort path can handle failures generically without
a runtime dependency on any vendor SDK.

Hierarchy::

    UploadError                  # root; already failure-shaped
    ├── UploadNetworkError       # transport-layer / retryable
    ├── UploadCredentialError    # auth / permissions / 401/403
    └── UploadQuotaError         # storage / rate / 429 / 507 / quota

``UploadError`` itself is re-exported from ``djust.uploads`` so
applications can ``from djust.uploads import UploadError`` without
having to know about the contrib subpackage.
"""

from __future__ import annotations


class UploadError(Exception):
    """Base class for object-storage upload failures.

    Backends subclass this (``UploadNetworkError``, ``UploadCredentialError``,
    ``UploadQuotaError``) and raise the most specific subclass that applies.
    The upload manager's ``_add_chunk_via_writer`` / ``_finalize_writer``
    path already catches any exception and funnels it into ``writer.abort()``;
    this taxonomy only narrows the shape so callers can ``except
    UploadCredentialError`` without importing ``botocore``.

    Attributes:
        message: Human-readable, safe-to-log summary. Never contains
            secrets — backends strip credentials / signed URLs before
            populating this. See ``from_sdk_exc`` in each backend.
        sdk_exc: The underlying vendor exception, for debugging. Do not
            surface this to clients; it may contain IAM ARNs, bucket
            names, or signed-URL query params.
    """

    def __init__(self, message: str, *, sdk_exc: BaseException | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.sdk_exc = sdk_exc

    def __repr__(self) -> str:  # pragma: no cover — trivial
        return f"{type(self).__name__}({self.message!r})"


class UploadNetworkError(UploadError):
    """Transport-layer failure: connection reset, DNS, TLS, 5xx, timeout.

    Distinct from credential / quota errors because the correct remediation
    is usually "retry with backoff", not "fix config". The writer's
    ``abort()`` will be called by the upload manager; it should release
    any partial server-side resources (MPU, resumable session) before
    returning.
    """


class UploadCredentialError(UploadError):
    """Authentication / authorization failure: 401, 403, missing credentials,
    expired tokens, insufficient IAM permissions.

    Typically unrecoverable without operator intervention. Backends should
    be careful to strip signed-URL query params and IAM ARNs from the
    ``message`` field — those leak into the client-facing error surface.
    """


class UploadQuotaError(UploadError):
    """Quota / rate-limit / capacity failure: 429, 507 Insufficient Storage,
    bucket-quota exceeded, service throttling.

    A retry *may* succeed after a cooldown, but the upload manager doesn't
    currently retry on its own — applications that want retry semantics
    should wrap the writer or retry at the user-facing upload-submission
    layer.
    """
