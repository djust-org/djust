"""ADR-038 E3: a queued activity event released by a NOTIFY must be freshly
authorized for an explicit view.

An event sent to a hidden activity is queued without validation; the contract
in ``ActivityMixin._queue_deferred_activity_event`` is that validation runs when
the event is dispatched. For an explicit view that includes ADR-038's fresh
``authorize_event``. The runtime's own drain runs inside a turn that already
passed it, but ``db_notify`` drains through the consumer's
``_dispatch_single_event`` with no authorized turn at all.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.websocket import LiveViewConsumer

from .test_exposure_runtime import make_request

BUMPS = []


class ActivityNotifyView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")
    # Class-level so the consumer joins the NOTIFY group at wiring time.
    _listen_channels = frozenset({"exposure_activity_auth"})

    def mount(self, request, **kwargs):
        self.set_activity_visible("panel", False)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        BUMPS.append(True)

    def handle_info(self, message):
        self.set_activity_visible("panel", True)


async def _collect(socket, quiet=0.8):
    """Frames until the socket goes quiet or closes; returns (frames, close)."""
    frames, closed = [], None
    while not await socket.receive_nothing(timeout=quiet):
        out = await socket.receive_output(timeout=3)
        if out["type"] == "websocket.close":
            closed = out
            break
        frames.append(json.loads(out["text"]))
    return frames, closed


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("session", ["intact", "deleted"])
async def test_notify_released_activity_event_requires_fresh_authorization(monkeypatch, session):
    BUMPS.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".ActivityNotifyView", "url": "/a/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"

            # Sent while the activity is hidden: queued, not dispatched.
            await socket.send_json_to(
                {"type": "event", "event": "bump", "params": {"_activity": "panel"}}
            )
            await _collect(socket, quiet=0.4)
            assert BUMPS == [], "the event must be queued, not dispatched"

            if session == "deleted":
                await sync_to_async(request.session.delete)()

            await get_channel_layer().group_send(
                "djust_db_notify_exposure_activity_auth",
                {"type": "db_notify", "channel": "exposure_activity_auth", "payload": {}},
            )
            frames, closed = await _collect(socket)

            if session == "intact":
                # Control: the NOTIFY releases the queued event.
                assert BUMPS == [True]
                assert closed is None
            else:
                # ADR-038: a deleted session fails fresh authorization, so the
                # released event must not reach its handler, and the socket
                # gets the runtime's static denial.
                assert BUMPS == [], "queued event dispatched without fresh authorization"
                errors = [f for f in frames if f.get("type") == "error"]
                assert [e["error"] for e in errors] == [
                    "Event authorization failed. Please reload the page."
                ]
                assert closed == {"type": "websocket.close", "code": 4403}
        finally:
            await socket.disconnect()


RAN = []


class ActivityFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")
    _listen_channels = frozenset({"exposure_activity_fail"})

    def mount(self, request, **kwargs):
        self._fail_render = False
        self.set_activity_visible("panel", False)

    def get_context_data(self, **kwargs):
        if getattr(self, "_fail_render", False):
            raise ValueError("ACT_RENDER_SENTINEL")
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def explode(self, **kwargs):
        RAN.append("explode")
        raise ValueError("ACT_HANDLER_SENTINEL")

    @event_handler()
    def bad_render(self, **kwargs):
        RAN.append("bad_render")
        self.count += 1
        self._fail_render = True

    @event_handler()
    def spawn(self, **kwargs):
        RAN.append("spawn")
        self.start_async(self._work)

    def _work(self):
        RAN.append("work")
        raise ValueError("ACT_ASYNC_SENTINEL")

    def handle_info(self, message):
        self.set_activity_visible("panel", True)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [True, False])
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
@pytest.mark.parametrize(
    "event,ran,sentinel",
    [
        ("explode", "explode", "ACT_HANDLER_SENTINEL"),
        ("bad_render", "bad_render", "ACT_RENDER_SENTINEL"),
        # Reachable since #2946: the released event's start_async work runs.
        ("spawn", "work", "ACT_ASYNC_SENTINEL"),
    ],
)
async def test_notify_released_event_failures_log_value_free_for_explicit_views(
    monkeypatch, caplog, policy, debug, event, ran, sentinel
):
    """Once a released event is authorized, the consumer's dispatcher runs its
    handler, re-render and any ``start_async`` work; each catch logged the
    exception with its traceback for any policy. Under ``DEBUG=False`` an
    explicit view's log is value-free; under ``DEBUG=True`` it carries the
    exception like a legacy view's (ADR-038 D-a, revised 2026-09-22)."""
    import asyncio
    import logging

    RAN.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(ActivityFailureView, "exposure_policy", policy)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=debug, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".ActivityFailureView", "url": "/af/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            await socket.send_json_to(
                {"type": "event", "event": event, "params": {"_activity": "panel"}}
            )
            await _collect(socket, quiet=0.4)
            assert RAN == [], "the event must be queued, not dispatched"
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await get_channel_layer().group_send(
                    "djust_db_notify_exposure_activity_fail",
                    {"type": "db_notify", "channel": "exposure_activity_fail", "payload": {}},
                )
                await _collect(socket)
                for _ in range(40):
                    if ran in RAN and (sentinel in caplog.text or "Protected" in caplog.text):
                        break
                    await asyncio.sleep(0.05)
            assert ran in RAN, f"{ran} never ran; the test would be vacuous"
            if policy == "legacy" or debug:
                assert sentinel in caplog.text
                assert "Traceback" in caplog.text
                assert "Protected" not in caplog.text
            else:
                assert sentinel not in caplog.text
                assert "Protected" in caplog.text
        finally:
            await socket.disconnect()
