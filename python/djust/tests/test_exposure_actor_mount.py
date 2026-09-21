"""The staged explicit policy must not enter unsupported actor mounts."""

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView
from djust.runtime import WSConsumerTransport
from djust.websocket import LiveViewConsumer

from .test_exposure_runtime import make_request


class ExplicitActorView(LiveView):
    exposure_policy = "explicit"
    use_actors = True
    template = "<div dj-root>actor</div>"


class SurvivorView(LiveView):
    template = "<div dj-root>survivor</div>"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("policy", ["explicit", None, "invalid"])
async def test_actor_mount_rejects_before_lifecycle_and_preserves_socket(
    monkeypatch, batch, policy
):
    # Only bypass construction, as in the other staged exposure tests.
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(ExplicitActorView, "exposure_policy", policy)
    calls = []

    def mount(self, request, **kwargs):
        calls.append("mount")

    async def actor_mount(self, view, data):
        calls.append("actor")
        return {"html": "<p>ACTOR_CONTEXT_SENTINEL</p>", "version": 1}

    monkeypatch.setattr(ExplicitActorView, "mount", mount)
    monkeypatch.setattr(WSConsumerTransport, "dispatch_actor_mount", actor_mount)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await communicator.connect())[0]
        await communicator.receive_json_from(timeout=3)
        try:
            denied = {"view": __name__ + ".ExplicitActorView", "url": "/actor/"}
            if batch:
                await communicator.send_json_to(
                    {
                        "type": "mount_batch",
                        "views": [
                            {**denied, "target_id": "denied"},
                            {
                                "view": __name__ + ".SurvivorView",
                                "url": "/survivor/",
                                "target_id": "survivor",
                            },
                        ],
                    }
                )
            else:
                await communicator.send_json_to({"type": "mount", **denied})
            frame = await communicator.receive_json_from(timeout=3)
            assert calls == [], frame
            assert "ACTOR_CONTEXT_SENTINEL" not in str(frame)
            if batch:
                assert frame["type"] == "mount_batch"
                assert [item["target_id"] for item in frame["views"]] == ["survivor"]
                assert [item["target_id"] for item in frame["failed"]] == ["denied"]
            else:
                assert frame["type"] == "error"
                assert frame["error"] == "Explicit actor mounts are not yet supported"
            await communicator.send_json_to({"type": "ping"})
            assert (await communicator.receive_json_from(timeout=3))["type"] == "pong"
        finally:
            await communicator.disconnect()
