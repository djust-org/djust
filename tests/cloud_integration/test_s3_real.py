"""Real-cloud smoke test for the S3 upload writer (#963).

Uploads a 1 MB blob via the real AWS S3 API, reads it back, deletes
it, and verifies the round-trip. Auto-skips when
``DJUST_CLOUD_INTEGRATION != 'aws'`` or the AWS creds aren't in the
environment. Never runs on PR CI.
"""

from __future__ import annotations

import os
import uuid

import pytest

from .conftest import require_aws


@pytest.fixture
def aws_bucket():
    require_aws()
    return os.environ["DJUST_CLOUD_INT_AWS_BUCKET"]


def test_s3_happy_path_round_trip(aws_bucket):
    """Upload 1 MB, HEAD, GET, DELETE. If any step fails, the weekly
    workflow's failure handler opens a tech-debt issue."""
    try:
        import boto3
    except ImportError:
        pytest.skip("boto3 not installed")

    client = boto3.client("s3")
    key = f"djust-cloud-int/{uuid.uuid4()}/smoke.bin"
    body = b"x" * (1 * 1024 * 1024)  # 1 MB

    # 1. PUT
    client.put_object(Bucket=aws_bucket, Key=key, Body=body)

    try:
        # 2. HEAD — verify presence + size
        head = client.head_object(Bucket=aws_bucket, Key=key)
        assert head["ContentLength"] == len(body)

        # 3. GET — verify bytes round-trip
        got = client.get_object(Bucket=aws_bucket, Key=key)
        assert got["Body"].read() == body
    finally:
        # 4. DELETE — always clean up, even on assertion failure
        client.delete_object(Bucket=aws_bucket, Key=key)
