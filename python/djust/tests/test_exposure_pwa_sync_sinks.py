"""ADR-038 E1-2: PWA offline-sync exception text outside log calls.

Two destinations carried ``str(exc)`` without passing a log call, so the
log-exposure pin could not see them:

* ``SyncMixin._process_sync_queue`` pushed ``{"error": str(e)}`` to the client
  as an ``offline:sync_error`` push event;
* the create/update/delete sync branches stored ``str(e)`` in the sync queue
  through ``mark_failed``.

Both now carry a value-free message for nonlegacy views; legacy output is
unchanged. The sync runs inside a real runtime event, and the assertions read
the WebSocket frames and the queue's stored JSON bytes.

The ownerless ``sync_endpoint_view`` returned ``Batch sync error: <str(e)>`` in
its JSON body. It has no view to read a policy from, so it is value-free for
every caller (asserted at the response bytes).
"""

import re
import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from djust import LiveView, event_handler
from djust.pwa import sync as pwa_sync
from djust.pwa.mixins import OfflineMixin, SyncMixin
from djust.pwa.storage import OfflineAction

from .test_exposure_runtime import make_request, mount

QUEUE_SENTINEL = "PWA_QUEUE_SENTINEL"
ACTION_SENTINEL = "PWA_ACTION_SENTINEL"
CALLS = []


class _FailingQueue:
    def get_pending(self):
        CALLS.append("get_pending")
        raise ValueError(QUEUE_SENTINEL)


class SyncRuntimeView(SyncMixin, OfflineMixin, LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>sync</div>"
    offline_storage = "adr038_pwa_sync"

    def sync_create_note(self, data):
        CALLS.append("create")
        raise ValueError(ACTION_SENTINEL + " create " + data["body"])

    def sync_update_note(self, action_id, data):
        CALLS.append("update")
        raise ValueError(ACTION_SENTINEL + " update " + data["body"])

    def sync_delete_note(self, action_id):
        CALLS.append("delete")
        raise ValueError(ACTION_SENTINEL + " delete")

    @event_handler()
    def sync_now(self, stage: str = "queue"):
        if stage == "queue":
            self._sync_queue = _FailingQueue()
        else:
            for kind in ("create", "update", "delete"):
                self.sync_queue.add(
                    OfflineAction(
                        id=kind + "-1",
                        type=kind,
                        model="note",
                        data={"body": "OFFLINE_BODY_SENTINEL"},
                    )
                )
        self._process_sync_queue()
        if stage == "queue":
            self._sync_queue = None  # render reads the queue again; use a real one
        # The queue lives in server memory; expose its stored bytes to the test.
        type(self).stored = (
            "" if stage == "queue" else "".join(self.sync_queue._storage._get_js_bridge().values())
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["queue", "actions"])
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_sync_failures_reach_frames_and_queue_value_free(monkeypatch, stage, policy):
    CALLS.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(SyncRuntimeView, "exposure_policy", policy)
    monkeypatch.setattr(SyncRuntimeView, "stored", "", raising=False)
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, SyncRuntimeView, view=__name__ + ".SyncRuntimeView")
    transport.sent.clear()
    await runtime.dispatch_event(
        {"type": "event", "event": "sync_now", "params": {"stage": stage}, "ref": 3}
    )
    for _ in range(5):  # push_event frames are sent fire-and-forget
        await asyncio.sleep(0)
    frames = json.dumps(transport.sent)
    stored = SyncRuntimeView.stored
    legacy = policy == "legacy"
    if stage == "queue":
        assert CALLS == ["get_pending"], "the sync never ran; the test would be vacuous"
        assert "offline:sync_error" in frames, transport.sent
        assert (QUEUE_SENTINEL in frames) == legacy
        if not legacy:
            assert "Offline sync failed" in frames
    else:
        assert sorted(CALLS) == ["create", "delete", "update"], CALLS
        assert "offline:sync_complete" in frames, transport.sent
        assert stored.count('"status": "failed"') == 3, stored
        assert stored.count(ACTION_SENTINEL) == (3 if legacy else 0)
        if not legacy:
            assert stored.count('"error": "ValueError"') == 3, stored
        # The client-queued data itself stays in the queue; only error text is gated.
        assert "OFFLINE_BODY_SENTINEL" in stored
        assert ACTION_SENTINEL not in frames


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
    response. Like _perform_sync (#2950) they carry the exception class only;
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
