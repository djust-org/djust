"""djust.contrib.uploads — first-class object-storage UploadWriter backends.

Ships three sibling modules:

- ``djust.contrib.uploads.s3_presigned`` (#820) — pre-signed S3 PUT URLs
  for client-direct uploads. djust only signs the URL and observes the
  completion via an S3 event webhook (SNS/SQS). The client never streams
  bytes through the djust WebSocket.
- ``djust.contrib.uploads.gcs`` (#822) — ``GCSMultipartWriter``: a
  ``UploadWriter`` subclass that pipes chunks into a GCS resumable-upload
  session.
- ``djust.contrib.uploads.azure`` (#822) — ``AzureBlockBlobWriter``: a
  ``UploadWriter`` subclass that stages blocks and commits them as a
  block blob.

All three share a single error taxonomy rooted at
``djust.uploads.UploadError`` so application code can handle
object-storage failures generically regardless of backend. See
``errors.py`` for the class hierarchy.

Install the optional extras you need::

    pip install djust[s3]       # boto3
    pip install djust[gcs]      # google-cloud-storage
    pip install djust[azure]    # azure-storage-blob
"""

from djust.contrib.uploads.errors import (
    UploadCredentialError,
    UploadError,
    UploadNetworkError,
    UploadQuotaError,
)

__all__ = [
    "UploadError",
    "UploadNetworkError",
    "UploadCredentialError",
    "UploadQuotaError",
]
