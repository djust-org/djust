"""Real-cloud smoke test for the Azure Blob upload writer (#963)."""

from __future__ import annotations

import os
import uuid

import pytest

from .conftest import require_azure


@pytest.fixture
def azure_container():
    require_azure()
    return os.environ["DJUST_CLOUD_INT_AZURE_CONTAINER"]


def test_azure_happy_path_round_trip(azure_container):
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        pytest.skip("azure-storage-blob not installed")

    conn = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    svc = BlobServiceClient.from_connection_string(conn)
    blob_name = f"djust-cloud-int/{uuid.uuid4()}/smoke.bin"
    blob = svc.get_blob_client(container=azure_container, blob=blob_name)
    body = b"x" * (1 * 1024 * 1024)

    try:
        blob.upload_blob(body)
        assert blob.exists()
        downloaded = blob.download_blob().readall()
        assert downloaded == body
    finally:
        blob.delete_blob()
