"""Regression tests for pre-signed S3 PUT URLs (#820).

Covers both the signing helper (``PresignedS3Upload`` /
``sign_put_url`` / ``build_upload_spec``) and the webhook dispatcher
(``s3_event_webhook`` + ``parse_s3_event`` + registry).

boto3 is mocked at the ``client=`` injection seam — we don't require
the ``s3`` extra to run these tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest

from djust.contrib.uploads import (
    UploadCredentialError,
    UploadError,
    UploadNetworkError,
)
from djust.contrib.uploads.s3_events import (
    _HOOK_REGISTRY,
    parse_s3_event,
    register_upload_hook,
    s3_event_webhook,
    unregister_upload_hook,
)
from djust.contrib.uploads.s3_presigned import (
    PresignedS3Upload,
    _render_key,
    build_upload_spec,
    sign_put_url,
)


# ----------------------------------------------------------------------
# Signing
# ----------------------------------------------------------------------


def _fake_client(url: str = "https://s3.amazonaws.com/bucket/key?Signature=XYZ") -> MagicMock:
    client = MagicMock()
    client.generate_presigned_url = MagicMock(return_value=url)
    return client


class TestSignPutUrl:
    def test_sign_put_url_returns_valid_url_shape(self):
        """Returns the URL emitted by boto3, with the call params wired."""
        client = _fake_client("https://s3.amazonaws.com/foo/bar?X-Amz-Signature=abc")
        signer = PresignedS3Upload(bucket="foo", client=client)
        url = signer.sign_put_url("foo", "bar", expires_in=1800, content_type="image/png")
        assert url.startswith("https://")
        assert "X-Amz-Signature" in url or "Signature" in url
        # Verify boto3 call shape.
        client.generate_presigned_url.assert_called_once()
        args, kwargs = client.generate_presigned_url.call_args
        assert args[0] == "put_object"
        assert kwargs["Params"] == {
            "Bucket": "foo",
            "Key": "bar",
            "ContentType": "image/png",
        }
        assert kwargs["ExpiresIn"] == 1800
        assert kwargs["HttpMethod"] == "PUT"

    def test_sign_put_url_uses_expires_in(self):
        """``expires_in`` is passed through to boto3 verbatim."""
        client = _fake_client()
        signer = PresignedS3Upload(bucket="foo", client=client)
        signer.sign_put_url("foo", "key", expires_in=300)
        _, kwargs = client.generate_presigned_url.call_args
        assert kwargs["ExpiresIn"] == 300

    def test_sign_put_url_omits_content_type_when_unset(self):
        client = _fake_client()
        signer = PresignedS3Upload(bucket="foo", client=client)
        signer.sign_put_url("foo", "key")
        _, kwargs = client.generate_presigned_url.call_args
        assert "ContentType" not in kwargs["Params"]

    def test_sign_put_url_translates_credential_error(self):
        """A 403-shaped botocore exception → UploadCredentialError."""
        client = MagicMock()
        exc = Exception("signing failed")
        exc.response = {"Error": {"Code": "SignatureDoesNotMatch"}}
        client.generate_presigned_url = MagicMock(side_effect=exc)
        signer = PresignedS3Upload(bucket="foo", client=client)
        with pytest.raises(UploadCredentialError):
            signer.sign_put_url("foo", "key")

    def test_sign_put_url_translates_generic_error_to_network(self):
        client = MagicMock()
        client.generate_presigned_url = MagicMock(side_effect=ValueError("boom"))
        signer = PresignedS3Upload(bucket="foo", client=client)
        with pytest.raises(UploadNetworkError):
            signer.sign_put_url("foo", "key")

    def test_module_level_sign_put_url_wraps(self):
        client = _fake_client("https://example.com/signed")
        url = sign_put_url("b", "k", client=client)
        assert url == "https://example.com/signed"


# ----------------------------------------------------------------------
# build_upload_spec
# ----------------------------------------------------------------------


class TestBuildUploadSpec:
    def test_build_upload_spec_round_trips_key_template(self):
        """Rendered key replaces {filename} with Path(filename).name and
        preserves the static prefix."""
        client = _fake_client()
        signer = PresignedS3Upload(bucket="b", client=client)
        spec = signer.build_upload_spec(
            bucket="b",
            key_template="uploads/{filename}",
            filename="/etc/../report.pdf",
            content_type="application/pdf",
        )
        # Path(filename).name strips any directory components — SAFE
        assert spec["key"] == "uploads/report.pdf"
        assert spec["mode"] == "presigned"
        assert spec["url"].startswith("https://")
        assert spec["fields"] == {"Content-Type": "application/pdf"}

    def test_build_upload_spec_uuid_placeholder(self):
        import re

        client = _fake_client()
        signer = PresignedS3Upload(bucket="b", client=client)
        spec = signer.build_upload_spec(
            bucket="b",
            key_template="u/{uuid}/{filename}",
            filename="x.jpg",
        )
        # Path: u/<uuid4>/x.jpg
        parts = spec["key"].split("/")
        assert parts[0] == "u"
        assert re.match(r"^[0-9a-f\-]{36}$", parts[1])
        assert parts[2] == "x.jpg"

    def test_build_upload_spec_merges_extra_fields(self):
        client = _fake_client()
        signer = PresignedS3Upload(bucket="b", client=client)
        spec = signer.build_upload_spec(
            bucket="b",
            key_template="k-{uuid}",
            filename="f.bin",
            content_type="application/octet-stream",
            extra_fields={"x-amz-meta-upload-id": "abc"},
        )
        assert spec["fields"]["x-amz-meta-upload-id"] == "abc"
        assert spec["fields"]["Content-Type"] == "application/octet-stream"

    def test_key_template_unknown_placeholder_raises(self):
        with pytest.raises(KeyError):
            _render_key("uploads/{unknown}", uuid="x", filename="y")

    def test_module_level_build_upload_spec_wraps(self):
        client = _fake_client()
        spec = build_upload_spec(
            bucket="b", key_template="p/{filename}", filename="a.pdf", client=client
        )
        assert spec["key"] == "p/a.pdf"


# ----------------------------------------------------------------------
# Webhook
# ----------------------------------------------------------------------


def _sign_body(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _make_request(method: str = "POST", body: bytes = b"", headers=None):
    """Build a minimal Django-like request object for the webhook view."""
    req = MagicMock()
    req.method = method
    req.body = body
    req.META = headers or {}
    return req


class TestS3EventWebhook:
    def setup_method(self, method):
        _HOOK_REGISTRY.clear()

    def teardown_method(self, method):
        _HOOK_REGISTRY.clear()

    def test_webhook_fires_on_upload_complete_hook(self, settings):
        """Valid signature + S3 event payload → registered hook fires
        with the expected kwargs."""
        settings.DJUST_S3_WEBHOOK_SECRET = "test-secret"
        called = {}

        def hook(**kwargs):
            called.update(kwargs)

        # parse_s3_event extracts the first UUID-shaped path segment as
        # upload_id. We encode the djust upload-ref (a UUID4) as the
        # first segment, per the documented convention in s3_events.py.
        upload_id = "11111111-2222-3333-4444-555555555555"
        register_upload_hook(upload_id, hook)

        payload = {
            "Records": [
                {
                    "s3": {
                        "object": {
                            "key": f"uploads/{upload_id}/report.pdf",
                            "size": 4096,
                            "eTag": '"deadbeef"',
                        }
                    }
                }
            ]
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, "test-secret")
        req = _make_request(body=body, headers={"HTTP_X_DJUST_SIGNATURE": sig})
        resp = s3_event_webhook(req)
        assert resp.status_code == 200
        assert called["upload_id"] == upload_id
        assert called["s3_key"] == f"uploads/{upload_id}/report.pdf"
        assert called["size"] == 4096
        assert called["etag"] == "deadbeef"

    def test_webhook_rejects_invalid_signature(self, settings):
        settings.DJUST_S3_WEBHOOK_SECRET = "real-secret"
        body = json.dumps({"Records": []}).encode()
        req = _make_request(
            body=body,
            headers={"HTTP_X_DJUST_SIGNATURE": "not-the-right-hmac"},
        )
        resp = s3_event_webhook(req)
        assert resp.status_code == 403

    def test_webhook_rejects_non_post(self, settings):
        settings.DJUST_S3_WEBHOOK_SECRET = "s"
        req = _make_request(method="GET")
        resp = s3_event_webhook(req)
        assert resp.status_code == 405

    def test_webhook_requires_secret_configured(self, settings):
        # No DJUST_S3_WEBHOOK_SECRET in settings
        if hasattr(settings, "DJUST_S3_WEBHOOK_SECRET"):
            del settings.DJUST_S3_WEBHOOK_SECRET
        req = _make_request(body=b"{}", headers={"HTTP_X_DJUST_SIGNATURE": "x"})
        resp = s3_event_webhook(req)
        assert resp.status_code == 500

    def test_webhook_unwraps_sns_envelope(self, settings):
        settings.DJUST_S3_WEBHOOK_SECRET = "s"
        inner = {"Records": [{"s3": {"object": {"key": "k/id/f.bin", "size": 1, "eTag": '"t"'}}}]}
        sns = {"Type": "Notification", "Message": json.dumps(inner)}
        body = json.dumps(sns).encode()
        got = parse_s3_event(json.loads(body))
        assert got and got[0]["s3_key"] == "k/id/f.bin"

    def test_unregister_upload_hook(self):
        register_upload_hook("x", lambda **_: None)
        assert "x" in _HOOK_REGISTRY
        unregister_upload_hook("x")
        assert "x" not in _HOOK_REGISTRY

    def test_hook_exception_does_not_break_webhook(self, settings):
        """A raising hook is logged, not re-raised — so S3/SNS don't retry."""
        settings.DJUST_S3_WEBHOOK_SECRET = "s"

        def bad_hook(**_):
            raise RuntimeError("boom")

        uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        register_upload_hook(uid, bad_hook)
        payload = {"Records": [{"s3": {"object": {"key": f"{uid}/x", "size": 1, "eTag": "t"}}}]}
        body = json.dumps(payload).encode()
        sig = _sign_body(body, "s")
        req = _make_request(body=body, headers={"HTTP_X_DJUST_SIGNATURE": sig})
        resp = s3_event_webhook(req)
        assert resp.status_code == 200


# ----------------------------------------------------------------------
# Error taxonomy smoke test — keeps the root import path live
# ----------------------------------------------------------------------


def test_error_taxonomy_reexported_from_djust_uploads():
    """``from djust.uploads import UploadError`` must resolve (the
    CHANGELOG promises this API surface)."""
    from djust.uploads import UploadError as UE
    from djust.uploads import UploadCredentialError as UCE

    assert UE is UploadError
    assert issubclass(UCE, UE)
