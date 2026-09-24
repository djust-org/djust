"""ADR-038 E1: PWA offline-sync handlers are application code.

``SyncMixin`` replays client-queued offline actions through a view's
``sync_create_<model>`` (and update/delete) methods and logged their failures
with the exception text — which can echo the client-supplied data. Driven
directly: the sync runs in the mixin, outside any runtime turn.
"""

import logging

import pytest

from djust.pwa.mixins import SyncMixin
from djust.pwa.storage import OfflineAction

CALLS = []


class NoteSyncView(SyncMixin):
    exposure_policy = "legacy"

    def sync_create_note(self, data):
        CALLS.append(True)
        raise ValueError("PWA_SYNC_SENTINEL " + data["body"])


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_offline_sync_handler_failure_is_value_free_for_explicit_views(caplog, policy):
    CALLS.clear()
    from types import SimpleNamespace

    view = NoteSyncView()
    view.exposure_policy = policy
    recorded = []
    view.sync_queue = SimpleNamespace(
        mark_failed=lambda action_id, error: recorded.append(error),
        mark_completed=lambda action_id: None,
    )
    action = OfflineAction(
        id="a1", type="create", model="note", data={"body": "OFFLINE_DATA_SENTINEL"}, timestamp=0.0
    )
    with caplog.at_level(logging.DEBUG):
        processed, failed = view._sync_create_actions([action])
    assert (processed, failed) == (0, 1)
    assert CALLS, "the sync handler never ran; the test would be vacuous"
    # The failure branch stores an error in the sync queue (E1-2): legacy keeps
    # str(exc); nonlegacy views store only the exception class name.
    assert len(recorded) == 1
    if policy == "legacy":
        assert "OFFLINE_DATA_SENTINEL" in recorded[0]
        assert "OFFLINE_DATA_SENTINEL" in caplog.text
    else:
        assert recorded == ["ValueError"]
        assert "OFFLINE_DATA_SENTINEL" not in caplog.text
        assert "PWA_SYNC_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
