"""The staged explicit policy must not enter unsupported actor mounts."""

import asyncio

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


class _FakeActorHandle:
    session_id = "fake-actor"

    def __init__(self, calls):
        self._calls = calls

    async def event(self, *args, **kwargs):
        self._calls.append("actor_handle_event")
        return {"html": "<p>ACTOR_EVENT_SENTINEL</p>", "version": 2}

    async def shutdown(self):
        pass


class TransitionActorView(LiveView):
    exposure_policy = "legacy"
    use_actors = True
    template = "<div dj-root>actor</div>"

    def bump(self, **kwargs):
        pass


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
async def test_actor_event_refuses_after_a_late_policy_transition(monkeypatch, policy):
    """ADR-038 E1: the event-side actor refusal (runtime.py, "Explicit actor
    events are not yet supported") re-checks the current policy per event.

    The mount refusal stops a nonlegacy view from ever mounting through the
    actor system, so the event refusal is reachable only when the policy
    changes after a legacy actor mount. ``legacy`` is the control: it must
    reach the actor, which proves the refusal cases entered the actor branch
    rather than bypassing it.
    """
    from djust.decorators import event_handler

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(TransitionActorView, "exposure_policy", "legacy")
    monkeypatch.setattr(TransitionActorView, "bump", event_handler()(TransitionActorView.bump))
    calls = []

    async def actor_mount(self, view, data):
        # Reproduce the real mount's side effects that uses_actors() reads.
        self._consumer.use_actors = True
        self._consumer.actor_handle = _FakeActorHandle(calls)
        return {"html": "<div dj-root>actor</div>", "version": 1}

    async def actor_event(self, view, *args, **kwargs):
        calls.append("dispatch_actor_event")

    monkeypatch.setattr(WSConsumerTransport, "dispatch_actor_mount", actor_mount)
    monkeypatch.setattr(WSConsumerTransport, "dispatch_actor_event", actor_event)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await communicator.connect())[0]
        await communicator.receive_json_from(timeout=3)
        try:
            await communicator.send_json_to(
                {"type": "mount", "view": __name__ + ".TransitionActorView", "url": "/actor/"}
            )
            mounted = await communicator.receive_json_from(timeout=3)
            assert mounted["type"] == "mount", mounted

            monkeypatch.setattr(TransitionActorView, "exposure_policy", policy)
            await communicator.send_json_to({"type": "event", "event": "bump", "params": {}})

            if policy == "legacy":
                for _ in range(60):
                    if "dispatch_actor_event" in calls:
                        break
                    await asyncio.sleep(0.05)
                assert calls == ["dispatch_actor_event"]
            else:
                frame = await communicator.receive_json_from(timeout=3)
                assert frame["type"] == "error", frame
                assert frame["error"] == "Explicit actor events are not yet supported"
                assert "SENTINEL" not in str(frame)
                closed = await communicator.receive_output(timeout=3)
                assert closed == {"type": "websocket.close", "code": 4403}
                assert calls == []
        finally:
            await communicator.disconnect()
