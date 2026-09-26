"""ADR-038 E3: server-originated consumer turns are authorized and persisted.

Ticks, ``server_push``, ``db_notify`` → ``handle_info``, NOTIFY-released
activity events and the background work they start all mutate an explicit
root outside the runtime's event turn. They ran with the mount-time principal
and never saved declared state, so a revoked session kept receiving renders
and a reconnect restored stale state. Decision D-l: each turn is authorized
against a fresh session before its application hook, declared state is
committed before its frame, and a revoked turn gets the foreground denial.

Every view here is explicit from mount; a mid-session policy flip fails fresh
authorization first and would make these tests vacuous.
"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.websocket import LiveViewConsumer

from ._ws_frames import WAIT_S, drain_extra, has_type, receive_until
from .test_exposure_runtime import make_request

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

RAN = []


class TurnView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")
    _listen_channels = frozenset({"exposure_turns"})

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    def handle_info(self, message):
        RAN.append("notify")
        self.count = message["payload"].get("count", self.count)

    def handle_push(self, count=0, **kwargs):
        RAN.append("push")
        self.count = count


class TickView(TurnView):
    count = state(0, persist="server")
    tick_interval = 300

    def handle_tick(self):
        RAN.append("tick")
        self.count = 3


class ActivityTurnView(TurnView):
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.set_activity_visible("panel", False)

    def handle_info(self, message):
        RAN.append("notify")
        self.set_activity_visible("panel", True)

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("bump")
        self.count = 11

    @event_handler()
    def spawn(self, **kwargs):
        RAN.append("spawn")
        self.start_async(self._work)

    def _work(self):
        RAN.append("work")
        return 13

    def handle_async_result(self, name, result=None, error=None):
        if error is None:
            self.count = result


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    RAN.clear()


SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


async def _connect(request, view_class):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope["session"] = SessionStore(request.session.session_key)
    socket.scope["user"] = AnonymousUser()
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
    )
    frame = await socket.receive_json_from(timeout=3)
    assert frame["type"] == "mount", frame
    return socket, frame


_UPDATE = has_type("patch", "html_update")


def _update_from(source):
    return lambda frames: any(
        f.get("type") in {"patch", "html_update"} and f.get("source") == source for f in frames
    )


async def _collect(socket, until):
    """The turn's frames and its close, event-driven (#3130).

    Waits until ``until(frames)`` holds (the turn's own frame arrived), then a
    trailing quiet window collects anything else. The window can only miss a
    late extra frame; it never cuts the expected one short.
    """
    frames = await receive_until(socket, until, what=getattr(until, "__name__", "the turn"))
    if not (frames and frames[-1].get("type") == "websocket.close"):
        frames += await drain_extra(socket)
    closes = [f for f in frames if f.get("type") == "websocket.close"]
    return [f for f in frames if f not in closes], (closes[0] if closes else None)


async def _restored_count(request, view_class):
    fresh = await sync_to_async(make_request)(request.session.session_key)
    socket, frame = await _connect(fresh, view_class)
    await socket.disconnect()
    return frame["html"]


async def _notify(payload):
    await get_channel_layer().group_send(
        "djust_db_notify_exposure_turns",
        {"type": "db_notify", "channel": "exposure_turns", "payload": payload},
    )


async def _wait_for(label):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_S
    while label not in RAN:
        assert loop.time() < deadline, f"{label!r} never ran: {RAN}"
        await asyncio.sleep(0.02)


async def test_db_notify_mutation_is_persisted():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, TurnView)
        try:
            await _notify({"count": 9})
            frames, closed = await _collect(socket, _UPDATE)
            assert RAN == ["notify"] and closed is None, (RAN, frames)
            assert any(f.get("type") in {"patch", "html_update"} for f in frames), frames
        finally:
            await socket.disconnect()
        assert ">9<" in await _restored_count(request, TurnView), "NOTIFY state not persisted"


async def test_db_notify_on_a_revoked_session_is_denied():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, TurnView)
        try:
            await sync_to_async(SessionStore(request.session.session_key).delete)()
            await _notify({"count": 9})
            frames, closed = await _collect(
                socket, lambda frames: frames[-1:] and frames[-1]["type"] == "websocket.close"
            )
            assert RAN == [], "handle_info ran without current authorization"
            assert not [f for f in frames if f.get("type") in {"patch", "html_update"}], frames
            assert [f.get("code") for f in frames if f.get("type") == "error"] == [
                "permission_denied"
            ]
            assert closed == {"type": "websocket.close", "code": 4403}
        finally:
            await socket.disconnect()


async def test_server_push_mutation_is_persisted():
    from djust.push import apush_to_view

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, TurnView)
        try:
            await apush_to_view(__name__ + ".TurnView", handler="handle_push", payload={"count": 4})
            await _wait_for("push")
            frames, _ = await _collect(socket, _UPDATE)
            assert RAN == ["push"], (RAN, frames)
        finally:
            await socket.disconnect()
        assert ">4<" in await _restored_count(request, TurnView), "push state not persisted"


async def test_tick_mutation_is_persisted():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, TickView)
        try:
            await _wait_for("tick")
            frames, _ = await _collect(socket, _update_from("tick"))
            assert "tick" in RAN, (RAN, frames)
        finally:
            await socket.disconnect()
        assert ">3<" in await _restored_count(request, TickView), "tick state not persisted"


@pytest.mark.parametrize("event,value", [("bump", 11), ("spawn", 13)])
async def test_released_activity_event_and_its_work_are_persisted(event, value):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, ActivityTurnView)
        try:
            await socket.send_json_to(
                {"type": "event", "event": event, "params": {"_activity": "panel"}}
            )
            await _collect(socket, has_type("noop"))  # the queued event's ack
            assert event not in RAN, "the event must be queued while the panel is hidden"
            await _notify({})
            await _wait_for("work" if event == "spawn" else event)
            frames, closed = await _collect(
                socket, _update_from("async" if event == "spawn" else "event")
            )
            assert event in RAN and closed is None, (RAN, frames)
        finally:
            await socket.disconnect()
        restored = await _restored_count(request, ActivityTurnView)
        assert f">{value}<" in restored, f"{event} state not persisted: {restored}"


class NotifyWorkView(TurnView):
    count = state(0, persist="server")

    def handle_info(self, message):
        RAN.append("notify")
        self.start_async(self._work)

    def _work(self):
        RAN.append("work")
        return 21

    def handle_async_result(self, name, result=None, error=None):
        if error is None:
            self.count = result


class LegacyNotifyWorkView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ count }}</span></div>"
    _listen_channels = frozenset({"exposure_turns"})

    def mount(self, request, **kwargs):
        self.count = 0

    def handle_info(self, message):
        RAN.append("notify")
        self.start_async(self._work)

    def _work(self):
        RAN.append("work")
        return 21

    def handle_async_result(self, name, result=None, error=None):
        if error is None:
            self.count = result


@pytest.mark.parametrize("view_class", [NotifyWorkView, LegacyNotifyWorkView])
async def test_start_async_from_a_server_originated_turn_runs(view_class):
    """``start_async`` queued in ``handle_info`` (and likewise ``handle_tick`` or
    a ``server_push`` handler) was never dispatched: those turns do not drain
    the queue. It now runs and renders, and an explicit root persists it."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, view_class)
        try:
            await _notify({})
            await _wait_for("work")
            frames, closed = await _collect(socket, _update_from("async"))
            assert RAN[:2] == ["notify", "work"], (RAN, frames)
            results = [f for f in frames if f.get("source") == "async"]
            assert results and "21" in json.dumps(results), frames
        finally:
            await socket.disconnect()
        if view_class is NotifyWorkView:
            assert ">21<" in await _restored_count(request, NotifyWorkView)


class HotReloadFailureView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0)

    def get_context_data(self, **kwargs):
        if getattr(self, "_fail_render", False):
            raise ValueError("HOTRELOAD_RENDER_SENTINEL")
        return super().get_context_data(count=self.count, **kwargs)


class LegacyHotReloadFailureView(HotReloadFailureView):
    exposure_policy = "legacy"


@pytest.mark.parametrize("debug", [True, False])
@pytest.mark.parametrize("view_class", [HotReloadFailureView, LegacyHotReloadFailureView])
async def test_hot_reload_render_failure_is_value_free_for_explicit_views(
    view_class, debug, caplog
):
    """Dev hot reload re-renders the mounted view, which runs its
    ``get_context_data``. Its catch-all logged the exception, with the
    traceback, for any policy. Under ``DEBUG=False`` an explicit view's failure
    must be value-free; under ``DEBUG=True`` it logs like a legacy view's
    (ADR-038 D-a, revised 2026-09-22)."""
    import logging

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **dict(SETTINGS, DEBUG=debug)):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, view_class)
        try:
            # Fail the hot-reload render from the outside, as a code edit might.
            view_class._fail_render = True
            with caplog.at_level(logging.DEBUG):
                await get_channel_layer().group_send(
                    "djust_hotreload", {"type": "hotreload", "file": "exposure_hotreload.py"}
                )
                deadline = asyncio.get_running_loop().time() + WAIT_S
                while not (
                    "HOTRELOAD_RENDER_SENTINEL" in caplog.text or "Protected" in caplog.text
                ):
                    assert asyncio.get_running_loop().time() < deadline, caplog.text
                    await asyncio.sleep(0.02)
        finally:
            view_class._fail_render = False
            await socket.disconnect()
    if view_class is LegacyHotReloadFailureView or debug:
        assert "HOTRELOAD_RENDER_SENTINEL" in caplog.text
        assert "Traceback" in caplog.text
        assert "Protected view operation failed" not in caplog.text
    else:
        assert "HOTRELOAD_RENDER_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text


class SnapshotNotifyView(TurnView):
    count = state(0, persist="server")
    navigation = state("initial", persist="client", client=True)

    def handle_info(self, message):
        RAN.append("notify")
        self.navigation = "from-notify"
        self.count = 1


async def test_notify_turn_refreshes_the_client_snapshot():
    """A NOTIFY turn that changes a ``persist="client"`` field carries the
    refreshed signed token for the primary view (ADR-038 E3)."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, mount_frame = await _connect(request, SnapshotNotifyView)
        try:
            await _notify({})
            frames, _ = await _collect(socket, _UPDATE)
        finally:
            await socket.disconnect()
    updates = [f for f in frames if f.get("type") in {"patch", "html_update"}]
    assert updates, frames
    token = updates[-1].get("state_snapshot_signed")
    assert isinstance(token, str) and token and token != mount_frame.get("state_snapshot_signed")
    assert updates[-1]["view"] == __name__ + ".SnapshotNotifyView"
