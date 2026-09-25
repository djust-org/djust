"""The PWA sync endpoint's ``errors`` carry the exception class, not its text (#2950).

``sync_endpoint_view`` returns ``SyncResult.errors`` in its JSON response. The
batch loop and the per-action create/update/delete helpers put ``str(e)`` there,
so a handler or database exception's text (an ``IntegrityError`` can quote
other rows' values) reached the client. The detail stays in the server log.
"""

import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from djust.pwa import sync as pwa_sync

ENDPOINT_SENTINEL = "PWA_ENDPOINT_SENTINEL"


def test_sync_endpoint_batch_error_is_value_free(monkeypatch):
    calls = []

    def _boom(self, batch, action_type, model_name):
        calls.append(model_name)
        raise ValueError(ENDPOINT_SENTINEL)

    monkeypatch.setattr(pwa_sync.SyncManager, "_sync_batch", _boom)
    request = RequestFactory().post(
        "/sync/",
        data=json.dumps(
            {
                "actions": [
                    {"id": "a1", "type": "create", "model": "Note", "data": {}, "timestamp": 1}
                ]
            }
        ),
        content_type="application/json",
    )
    request.user = type("U", (AnonymousUser,), {"is_authenticated": True})()
    response = pwa_sync.sync_endpoint_view(request)
    assert calls == ["Note"], "the batch never ran; the test would be vacuous"
    body = json.loads(response.content)
    assert ENDPOINT_SENTINEL not in response.content.decode()
    assert body["errors"] == ["Batch sync error: ValueError"]
    assert body["failed_count"] == 1


# delete never reads action.data, so it cannot raise from it; not a case here.
@pytest.mark.parametrize("stage", ["create", "update"])
def test_sync_batch_helper_errors_are_value_free(stage):
    """The per-action batch helpers' errors reach the sync endpoint's JSON
    response. Like _perform_sync they carry the exception class only;
    this plain Django endpoint has no view policy to consult."""
    from djust.pwa.sync import SyncManager
    from djust.pwa.storage import OfflineAction

    class ExplodingData(dict):
        def copy(self):
            raise ValueError("BATCH_HELPER_SENTINEL")

        def get(self, *args, **kwargs):
            raise ValueError("BATCH_HELPER_SENTINEL")

    action = OfflineAction(
        id="a1", type=stage, model="Note", data=ExplodingData(id=1), timestamp=0.0
    )
    result = getattr(SyncManager(), f"_sync_{stage}_batch")([action], "Note")
    assert result["failed"] == 1
    assert "BATCH_HELPER_SENTINEL" not in repr(result["errors"])
    # Each error names the action and the exception class, nothing more.
    for error in result["errors"]:
        assert re.fullmatch(r"\w+ failed for action a1: \w+", error), error
