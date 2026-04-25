"""Shared fixtures/gates for the real-cloud integration suite (#963)."""

from __future__ import annotations

import os

import pytest


def _want(provider: str) -> bool:
    """Return True iff the current run is exercising ``provider``.

    The weekly-cloud-uploads workflow sets ``DJUST_CLOUD_INTEGRATION``
    to one of `aws` / `gcp` / `azure` before invoking pytest on the
    provider's test file. In local / PR-CI runs the env var is unset
    and all provider tests auto-skip.
    """
    return os.environ.get("DJUST_CLOUD_INTEGRATION") == provider


def require_aws():
    if not _want("aws"):
        pytest.skip("DJUST_CLOUD_INTEGRATION != 'aws' — real AWS smoke test skipped")
    missing = [
        k
        for k in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "DJUST_CLOUD_INT_AWS_BUCKET",
        )
        if not os.environ.get(k)
    ]
    if missing:
        pytest.skip(f"AWS credentials missing: {missing}")


def require_gcp():
    if not _want("gcp"):
        pytest.skip("DJUST_CLOUD_INTEGRATION != 'gcp' — real GCS smoke test skipped")
    if not os.environ.get("DJUST_CLOUD_INT_GCP_BUCKET"):
        pytest.skip("DJUST_CLOUD_INT_GCP_BUCKET not set")
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        pytest.skip("GOOGLE_APPLICATION_CREDENTIALS not set")


def require_azure():
    if not _want("azure"):
        pytest.skip("DJUST_CLOUD_INTEGRATION != 'azure' — real Azure smoke test skipped")
    missing = [
        k
        for k in ("AZURE_STORAGE_CONNECTION_STRING", "DJUST_CLOUD_INT_AZURE_CONTAINER")
        if not os.environ.get(k)
    ]
    if missing:
        pytest.skip(f"Azure credentials missing: {missing}")
