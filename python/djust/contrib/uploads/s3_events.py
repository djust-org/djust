"""S3 event webhook — receives ``ObjectCreated`` notifications and fires
the ``on_upload_complete(...)`` hook on subscribed views (#820).

Companion to ``djust.contrib.uploads.s3_presigned``: after the client
PUTs bytes directly to S3, S3 emits an ``s3:ObjectCreated:*`` event
(via SNS → HTTPS, or SQS poll, or EventBridge → HTTP). This module
parses the SNS HTTP notification shape, validates the signature, and
fires a hook.

Wiring::

    # urls.py
    from djust.contrib.uploads.s3_events import s3_event_webhook
    urlpatterns = [
        path("webhooks/s3/", s3_event_webhook, name="djust_s3_webhook"),
    ]

    # settings.py
    DJUST_S3_WEBHOOK_SECRET = "<shared-secret-or-signing-cert>"

The view subscribes to uploads by overriding ``on_upload_complete``::

    class ReportView(LiveView):
        def on_upload_complete(
            self,
            upload_id: str,
            s3_key: str,
            size: int,
            etag: str,
        ) -> None:
            self.report_url = f"s3://{self.bucket}/{s3_key}"
            self.report_size = size

This module's hook dispatcher is deliberately generic: it doesn't know
which view owns the upload. Applications are expected to either
(a) encode the view identity into the S3 key prefix and route by prefix,
or (b) store an upload-id → view mapping at presign time. See
``UPLOAD_ID_VIEW_REGISTRY`` below for a tiny in-process registry
suitable for single-process deploys.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Hook registry
# ----------------------------------------------------------------------
#
# Dispatchers register by upload_id (returned at presign time). The
# registry is intentionally in-process — multi-process deploys should
# persist this in the session / DB / Redis, routing on upload_id.

_HOOK_REGISTRY: Dict[str, Callable[..., None]] = {}


def register_upload_hook(upload_id: str, hook: Callable[..., None]) -> None:
    """Register a callback for a given ``upload_id``.

    The hook is called with keyword args::

        hook(upload_id=..., s3_key=..., size=..., etag=...)

    ``upload_id`` is whatever the application passed at presign time
    (commonly the djust upload ref, sometimes a cryptographically-random
    nonce for opaque tracking).
    """
    _HOOK_REGISTRY[upload_id] = hook


def unregister_upload_hook(upload_id: str) -> None:
    """Remove a previously-registered hook. No-op if not registered."""
    _HOOK_REGISTRY.pop(upload_id, None)


def _fire_hook(upload_id: str, s3_key: str, size: int, etag: str) -> bool:
    """Fire the hook for ``upload_id`` if one is registered. Returns True
    if a hook was found and invoked (regardless of whether it raised —
    hook exceptions are logged, not re-raised, to prevent a webhook
    retry storm)."""
    hook = _HOOK_REGISTRY.get(upload_id)
    if hook is None:
        logger.warning(
            "No hook registered for upload_id=%s (s3_key=%s)",
            upload_id,
            s3_key,
        )
        return False
    try:
        hook(upload_id=upload_id, s3_key=s3_key, size=size, etag=etag)
    except Exception:  # noqa: BLE001 — hook must never break webhook
        logger.exception(
            "on_upload_complete hook raised for upload_id=%s (s3_key=%s)",
            upload_id,
            s3_key,
        )
    return True


# ----------------------------------------------------------------------
# Signature verification
# ----------------------------------------------------------------------


def _verify_hmac_signature(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 comparison.

    We support a simple shared-secret HMAC mode rather than full SNS
    certificate verification, because:

    1. Full SNS cert verification requires downloading and parsing the
       signing cert URL per-request, which needs a network fetch and a
       cert-pinning story. Complexity > benefit for most deployments.
    2. Production users behind Cloudflare / API Gateway / a Lambda
       fronting the webhook typically do HMAC in-between anyway.
    3. Apps that want full SNS cert verification can implement it in
       their own view and delegate to ``parse_s3_event`` directly.
    """
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_s3_event(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract ``(upload_id, s3_key, size, etag)`` tuples from an SNS/S3 event.

    S3 event notifications have two common shapes depending on delivery:

    1. **Direct (S3 → Lambda / S3 → HTTP via EventBridge)** — the body is
       the raw event: ``{"Records": [{"s3": {...}}, ...]}``.
    2. **SNS-wrapped** — the SNS HTTPS delivery POSTs a JSON with a
       ``Message`` field that *contains* the above as a JSON string.

    Returns a list of dicts::

        [
            {
                "upload_id": "<from metadata or key prefix>",
                "s3_key": "<full S3 key>",
                "size": 12345,
                "etag": "abc123...",
            },
            ...
        ]

    ``upload_id`` is extracted from the S3 object key's first path
    segment if it looks UUID-shaped; otherwise it's the full key. Apps
    that embed upload_id elsewhere (e.g. x-amz-meta-upload-id) can
    parse ``payload`` themselves and bypass this helper.
    """
    import re as _re

    # Unwrap SNS envelope if present.
    if isinstance(payload, dict) and "Message" in payload and "Records" not in payload:
        try:
            payload = json.loads(payload["Message"])
        except (TypeError, ValueError):
            logger.warning("S3 webhook: SNS Message field was not valid JSON")
            return []

    records = payload.get("Records", []) if isinstance(payload, dict) else []
    uuid_re = _re.compile(r"^[0-9a-fA-F-]{32,36}$")
    out: List[Dict[str, Any]] = []
    for rec in records:
        s3 = rec.get("s3") if isinstance(rec, dict) else None
        if not isinstance(s3, dict):
            continue
        obj = s3.get("object", {})
        key = obj.get("key", "")
        size = int(obj.get("size", 0))
        etag = obj.get("eTag", "").strip('"')
        # Derive upload_id from first path segment if UUID-shaped.
        segments = key.split("/")
        upload_id = key
        for seg in segments:
            if uuid_re.match(seg):
                upload_id = seg
                break
        out.append(
            {
                "upload_id": upload_id,
                "s3_key": key,
                "size": size,
                "etag": etag,
            }
        )
    return out


# ----------------------------------------------------------------------
# Django view
# ----------------------------------------------------------------------


def s3_event_webhook(request: Any) -> Any:
    """Django view that accepts S3 event notifications and fires hooks.

    Request contract:

    - ``POST`` only; other methods return 405.
    - Body is JSON (SNS or raw S3 event shape — ``parse_s3_event`` unwraps).
    - ``X-Djust-Signature`` header carries an HMAC-SHA256 hex digest of
      the raw body, keyed with ``settings.DJUST_S3_WEBHOOK_SECRET``.
      Missing or mismatched signature returns 403.
    - Returns 200 with a JSON body describing how many hooks fired, even
      when no hooks were registered (so the event source doesn't retry).

    Importing ``django.http`` / ``django.conf`` at module top would make
    this module unimportable in non-Django contexts (e.g. a pure boto3
    Lambda). Instead we import inside the function so the module stays
    useful as a helper library.
    """
    from django.conf import settings
    from django.http import HttpResponse, JsonResponse

    if request.method != "POST":
        return HttpResponse(status=405)

    secret: Optional[str] = getattr(settings, "DJUST_S3_WEBHOOK_SECRET", None)
    if not secret:
        logger.error("s3_event_webhook: DJUST_S3_WEBHOOK_SECRET not configured")
        return HttpResponse(status=500)

    body: bytes = request.body
    signature = request.META.get("HTTP_X_DJUST_SIGNATURE", "")
    if not signature or not _verify_hmac_signature(body, signature, secret):
        logger.warning("s3_event_webhook: signature validation failed")
        return HttpResponse(status=403)

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return HttpResponse(status=400)

    fired = 0
    for event in parse_s3_event(payload):
        if _fire_hook(**event):
            fired += 1

    return JsonResponse({"ok": True, "fired": fired})
