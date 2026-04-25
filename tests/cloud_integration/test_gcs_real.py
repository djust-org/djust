"""Real-cloud smoke test for the GCS upload writer (#963)."""

from __future__ import annotations

import os
import uuid

import pytest

from .conftest import require_gcp


@pytest.fixture
def gcs_bucket():
    require_gcp()
    return os.environ["DJUST_CLOUD_INT_GCP_BUCKET"]


def test_gcs_happy_path_round_trip(gcs_bucket):
    try:
        from google.cloud import storage
    except ImportError:
        pytest.skip("google-cloud-storage not installed")

    client = storage.Client()
    bucket = client.bucket(gcs_bucket)
    blob_name = f"djust-cloud-int/{uuid.uuid4()}/smoke.bin"
    blob = bucket.blob(blob_name)
    body = b"x" * (1 * 1024 * 1024)

    try:
        blob.upload_from_string(body)
        assert bucket.blob(blob_name).exists()
        assert bucket.blob(blob_name).download_as_bytes() == body
    finally:
        blob.delete()
