"""ADR-038 E1: consumer message hooks that call application code.

``presence_heartbeat`` and ``cursor_move`` are dispatched by
``LiveViewConsumer.receive`` straight to view methods an application may
override (``update_presence_heartbeat``, ``handle_cursor_move``). Their catches
log the exception. For a nonlegacy view that is the same leak ``handle_exception``
refuses: undeclared state can occur in an exception's message.
"""

import logging

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView
from djust.websocket import LiveViewConsumer

from .test_exposure_runtime import make_request


class HookFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>hooks</div>"

    def update_presence_heartbeat(self):
        raise ValueError("PRESENCE_HOOK_SENTINEL")

    def handle_cursor_move(self, x, y):
        raise ValueError("CURSOR_HOOK_SENTINEL")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
@pytest.mark.parametrize(
    "message,sentinel",
    [
        ({"type": "presence_heartbeat"}, "PRESENCE_HOOK_SENTINEL"),
        ({"type": "cursor_move", "x": 1, "y": 2}, "CURSOR_HOOK_SENTINEL"),
    ],
    ids=["presence_heartbeat", "cursor_move"],
)
async def test_hook_failure_logs_are_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy, message, sentinel
):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".HookFailureView", "url": "/hooks/"}
            )
            mounted = await socket.receive_json_from(timeout=3)
            assert mounted["type"] == "mount", mounted

            monkeypatch.setattr(HookFailureView, "exposure_policy", policy)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to(message)
                # The hooks send nothing back; a ping round-trip orders the log.
                await socket.send_json_to({"type": "ping"})
                assert (await socket.receive_json_from(timeout=3))["type"] == "pong"

            if policy == "legacy":
                # Control: the hook ran and legacy logging is unchanged.
                assert sentinel in caplog.text
            else:
                assert sentinel not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


class PushFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>push</div>"

    def handle_push(self, **kwargs):
        raise ValueError("PUSH_HOOK_SENTINEL")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
async def test_server_push_failure_logs_are_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy
):
    """``server_push`` runs an application handler from the channel layer
    (Celery, management commands) and its catch logged the exception with a
    traceback. The push arrives on the channel-layer queue, not the socket's,
    so the log is polled for either outcome rather than ordered by a ping."""
    import asyncio

    from djust.push import apush_to_view

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".PushFailureView", "url": "/push/"}
            )
            mounted = await socket.receive_json_from(timeout=3)
            assert mounted["type"] == "mount", mounted

            monkeypatch.setattr(PushFailureView, "exposure_policy", policy)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await apush_to_view(__name__ + ".PushFailureView", handler="handle_push")
                for _ in range(60):
                    text = caplog.text
                    if "PUSH_HOOK_SENTINEL" in text or "Protected view operation failed" in text:
                        break
                    await asyncio.sleep(0.05)

            if policy == "legacy":
                # Unchanged legacy output: message and traceback.
                assert "Error in server_push: PUSH_HOOK_SENTINEL" in caplog.text
                assert "Traceback (most recent call last)" in caplog.text
            else:
                assert "PUSH_HOOK_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()
