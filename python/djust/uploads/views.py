"""HTTP views for upload status queries (ADR-010, issue #821).

The resumable-upload client uses :class:`UploadStatusView` on page load
(or reconnect) to decide whether a locally-cached ``upload_id`` is still
resumable server-side. Endpoint shape::

    GET /djust/uploads/<upload_id>/status
    → 200 { upload_id, status, bytes_received, chunks_received,
            filename, expected_size }
    → 404 { status: "not_found" }

Access control:

1. Session cookie is required (anonymous clients → 404).
2. The state entry's ``session_key`` must match the requester's session
   — we return ``404`` on mismatch so cross-user probes can't detect
   whether an ``upload_id`` exists.

Wire the view up via ``django.urls.path``::

    from djust.uploads.views import UploadStatusView

    urlpatterns = [
        path(
            "djust/uploads/<str:upload_id>/status",
            UploadStatusView.as_view(),
            name="djust-upload-status",
        ),
    ]

Or use :func:`upload_status_urlpatterns` to get the patterns list.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from django.http import JsonResponse
from django.urls import path
from django.views import View

from .._log_utils import sanitize_for_log
from .resumable import expand_ranges
from .storage import UploadStateStore, get_default_store

logger = logging.getLogger(__name__)


# UUID4 — what the client generates via crypto.getRandomValues.
# Accept any RFC 4122 UUID (including hyphenated canonical form).
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _not_found_response() -> JsonResponse:
    """Canonical 404 payload — identical whether the ID is missing or
    cross-session. Prevents existence-leak probes."""
    return JsonResponse(
        {"status": "not_found"},
        status=404,
    )


class UploadStatusView(View):
    """GET endpoint returning state-store contents for a single upload_id.

    Subclass to inject a custom :class:`UploadStateStore`::

        class MyStatusView(UploadStatusView):
            state_store = my_redis_store

    By default the view uses :func:`get_default_store` — which is the
    process-wide in-memory store unless overridden at app startup.
    """

    http_method_names = ["get"]

    #: Set on the subclass to override the default store resolution.
    state_store: Optional[UploadStateStore] = None

    def _get_store(self) -> UploadStateStore:
        return self.state_store or get_default_store()

    def _get_session_key(self, request) -> Optional[str]:
        """Return the requester's session key, or None if anonymous."""
        session = getattr(request, "session", None)
        if session is None:
            return None
        # ``session_key`` is None on a fresh session that hasn't been
        # saved yet. Treat that as anonymous.
        return session.session_key

    def get(self, request, upload_id: str, *args, **kwargs):
        if not _UUID_RE.match(upload_id):
            # Malformed IDs can't be in the store — 404 without even
            # hitting the backend.
            return _not_found_response()

        session_key = self._get_session_key(request)
        if not session_key:
            # No session → no way to authorize. Return 404 so anonymous
            # probes can't distinguish "no session" from "id missing".
            return _not_found_response()

        try:
            entry = self._get_store().get(upload_id)
        except Exception as exc:  # noqa: BLE001
            # sanitize_for_log() breaks the taint chain from the URL
            # param; _UUID_RE.match() above already rejects anything but
            # hex+hyphens, so this is belt-and-suspenders against a
            # future regex loosening.
            logger.warning(
                "UploadStatusView: state store read failed for %s: %s",
                sanitize_for_log(upload_id),
                sanitize_for_log(str(exc)),
            )
            return _not_found_response()

        if entry is None:
            return _not_found_response()

        stored_session = entry.get("session_key")
        if stored_session is None or stored_session != session_key:
            # Cross-session probe — same response as missing.
            return _not_found_response()

        payload = {
            "upload_id": entry.get("upload_id", upload_id),
            "status": "uploading",
            "bytes_received": int(entry.get("bytes_received", 0)),
            "chunks_received": expand_ranges(entry.get("chunks_received_ranges", [])),
            "filename": entry.get("filename"),
            "expected_size": entry.get("expected_size"),
        }
        return JsonResponse(payload, status=200)


def upload_status_urlpatterns(prefix: str = "djust/uploads") -> List:
    """Return URL patterns that mount :class:`UploadStatusView`.

    Include in your project's ``urls.py``::

        from djust.uploads.views import upload_status_urlpatterns

        urlpatterns = [
            *upload_status_urlpatterns(),
            # ...
        ]

    The default prefix is ``djust/uploads``; the resulting route is
    ``djust/uploads/<upload_id>/status``.
    """
    return [
        path(
            f"{prefix.rstrip('/')}/<str:upload_id>/status",
            UploadStatusView.as_view(),
            name="djust-upload-status",
        ),
    ]


__all__ = [
    "UploadStatusView",
    "upload_status_urlpatterns",
]
