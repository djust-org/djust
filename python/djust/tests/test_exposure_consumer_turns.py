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


class _PushPaths:
    """Which server-push turn path a test runs (#4152), and a count of the
    worker pool's one-hop turns, so a "pool" case cannot silently run stock."""

    def __init__(self, size):
        from django.conf import settings

        self.size = size
        # ``override_settings`` reloads djust's config, so the pool is set
        # there, not by patching the loaded config.
        self.settings = {
            "LIVEVIEW_CONFIG": {
                **(getattr(settings, "LIVEVIEW_CONFIG", None) or {}),
                "worker_threads": size,
            }
        }
        self.offloaded = 0
        #: The one-hop tick and db_notify turns (#4166).
        self.offloaded_tick = 0
        self.offloaded_notify = 0
        #: For each server push the socket received: was the session on the pool?
        self.pushes_on_pool: list = []


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    """Both server-push turn paths: stock, and the worker pool's one-hop turn,
    which authorizes an explicit root inside its hop (#4152)."""
    from djust import worker_pool

    monkeypatch.setattr(worker_pool, "_pool", [])
    paths = _PushPaths(request.param)
    real = LiveViewConsumer._run_server_push_turn_offloaded

    async def offloaded(self, *args, **kwargs):
        paths.offloaded += 1
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(LiveViewConsumer, "_run_server_push_turn_offloaded", offloaded)
    for name, counter in (
        ("_run_tick_turn_offloaded", "offloaded_tick"),
        ("_run_db_notify_turn_offloaded", "offloaded_notify"),
    ):
        real_turn = getattr(LiveViewConsumer, name, None)
        if real_turn is None:
            continue  # not offloaded at all: the count stays 0

        def counted(real_turn=real_turn, counter=counter):
            async def turn(self, *args, **kwargs):
                setattr(paths, counter, getattr(paths, counter) + 1)
                return await real_turn(self, *args, **kwargs)

            return turn

        monkeypatch.setattr(LiveViewConsumer, name, counted())
    real_push = LiveViewConsumer.server_push

    async def server_push(self, *args, **kwargs):
        from djust.worker_pool import offload_enabled

        paths.pushes_on_pool.append(offload_enabled())
        return await real_push(self, *args, **kwargs)

    monkeypatch.setattr(LiveViewConsumer, "server_push", server_push)
    return paths


SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


async def _connect(request, view_class):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope["session"] = SessionStore(request.session.session_key)
    socket.scope["user"] = AnonymousUser()
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
    )
    frame = await socket.receive_json_from()
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


async def test_db_notify_mutation_is_persisted(pool):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
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
    assert pool.offloaded_notify == (1 if pool.size else 0), pool.offloaded_notify


async def test_db_notify_on_a_revoked_session_is_denied(pool):
    """The pool's one-hop turn authorizes inside its hop (#4166)."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
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
    assert pool.offloaded_notify == (1 if pool.size else 0), pool.offloaded_notify


async def test_server_push_mutation_is_persisted(pool):
    from djust.push import apush_to_view

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
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
    assert pool.offloaded == (1 if pool.size else 0), pool.offloaded


async def test_server_push_on_a_revoked_session_is_denied(pool):
    """A push to an explicit root whose session was revoked runs no hook, sends
    no render and gets the foreground denial, on both push-turn paths (#4152:
    the pool's turn authorizes inside its hop, before the hook)."""
    from djust.push import apush_to_view

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, TurnView)
        try:
            await sync_to_async(SessionStore(request.session.session_key).delete)()
            await apush_to_view(__name__ + ".TurnView", handler="handle_push", payload={"count": 4})
            frames, closed = await _collect(
                socket, lambda frames: frames[-1:] and frames[-1]["type"] == "websocket.close"
            )
            assert RAN == [], "the push hook ran without current authorization"
            assert not [f for f in frames if f.get("type") in {"patch", "html_update"}], frames
            assert [f.get("code") for f in frames if f.get("type") == "error"] == [
                "permission_denied"
            ]
            assert closed == {"type": "websocket.close", "code": 4403}
        finally:
            await socket.disconnect()
    assert pool.offloaded == (1 if pool.size else 0), pool.offloaded


class AsyncCheckTurnView(TurnView):
    count = state(0, persist="server")
    allowed = True

    async def check_permissions(self, request):
        return AsyncCheckTurnView.allowed


@pytest.mark.parametrize("allowed", [True, False], ids=["allowed", "refused"])
async def test_push_to_a_view_with_async_check_permissions(pool, allowed):
    """An explicit root whose ``check_permissions`` is ``async def`` is
    authorized on the loop before its hooks, on both push-turn paths: the
    pool's one-hop turn leaves it to the stock path (#4152)."""
    from djust.push import apush_to_view

    path = __name__ + ".AsyncCheckTurnView"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, AsyncCheckTurnView)
        try:
            AsyncCheckTurnView.allowed = allowed
            await apush_to_view(path, handler="handle_push", payload={"count": 6})
            if allowed:
                frames, closed = await _collect(socket, _UPDATE)
                assert RAN == ["push"] and closed is None, (RAN, frames)
            else:
                frames, closed = await _collect(
                    socket, lambda frames: frames[-1:] and frames[-1]["type"] == "websocket.close"
                )
                assert RAN == [], "the push hook ran on a refused async check_permissions"
                assert closed == {"type": "websocket.close", "code": 4403}
                assert not [f for f in frames if f.get("type") in {"patch", "html_update"}]
        finally:
            AsyncCheckTurnView.allowed = True
            await socket.disconnect()
    # The pool was on for the pool case, and still no push took the one-hop turn.
    assert pool.pushes_on_pool == [bool(pool.size)], pool.pushes_on_pool
    assert pool.offloaded == 0, pool.offloaded


class LabelTurnView(TurnView):
    template = "<div dj-root><span>{{ count }}</span><i>{{ label }}</i></div>"
    count = state(0, persist="server")
    label = state("a", persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(label=self.label, **kwargs)

    def handle_label(self, label="", **kwargs):
        RAN.append("label")
        self.label = label


async def test_server_push_whose_save_fails_withholds_the_frame(pool, monkeypatch):
    """A push turn whose commit fails sends ``state_error`` and no render, and
    the next frame still brings the page up to the view. The pool's turn
    renders in its hop before the commit (#4152), so it must withhold that
    render AND drop its diff baseline: a later patch diffed against the
    withheld render would leave that turn's change off the page."""
    from djust.push import apush_to_view
    from djust.runtime import ViewRuntime

    real_save = ViewRuntime._save_explicit_root
    fail = {"on": True}

    def save(self, view, request):
        if fail["on"]:
            raise RuntimeError("storage down")
        return real_save(self, view, request)

    monkeypatch.setattr(ViewRuntime, "_save_explicit_root", save)
    path = __name__ + ".LabelTurnView"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, LabelTurnView)
        try:
            await apush_to_view(path, handler="handle_push", payload={"count": 4})
            frames, closed = await _collect(socket, has_type("error"))
            assert RAN == ["push"] and closed is None, (RAN, frames)
            assert [f.get("code") for f in frames if f.get("type") == "error"] == ["state_error"]
            assert not [f for f in frames if f.get("type") in {"patch", "html_update"}], frames
            fail["on"] = False
            # This push changes only the label; the withheld count must still land.
            await apush_to_view(path, handler="handle_label", payload={"label": "b"})
            frames, _ = await _collect(socket, _UPDATE)
            updates = [f for f in frames if f.get("type") in {"patch", "html_update"}]
            assert len(updates) == 1, frames
            assert "4" in json.dumps(updates[0]), updates
        finally:
            await socket.disconnect()
        restored = await _restored_count(request, LabelTurnView)
        assert ">4<" in restored and ">b<" in restored, restored
    assert pool.offloaded == (2 if pool.size else 0), pool.offloaded


class SnapshotPushView(TurnView):
    count = state(0, persist="server")
    navigation = state("initial", persist="client", client=True)

    def handle_push(self, count=0, **kwargs):
        RAN.append("push")
        self.navigation = "from-push"
        self.count = count


async def test_push_turn_refreshes_the_client_snapshot(pool):
    """A push that changes a ``persist="client"`` field carries the refreshed
    signed token, on both push-turn paths (ADR-038 E3, #4152)."""
    from djust.push import apush_to_view

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, mount_frame = await _connect(request, SnapshotPushView)
        try:
            await apush_to_view(
                __name__ + ".SnapshotPushView", handler="handle_push", payload={"count": 2}
            )
            frames, _ = await _collect(socket, _UPDATE)
        finally:
            await socket.disconnect()
    updates = [f for f in frames if f.get("type") in {"patch", "html_update"}]
    assert updates, frames
    token = updates[-1].get("state_snapshot_signed")
    assert isinstance(token, str) and token and token != mount_frame.get("state_snapshot_signed")
    assert updates[-1]["view"] == __name__ + ".SnapshotPushView"
    assert pool.offloaded == (1 if pool.size else 0), pool.offloaded


async def test_tick_mutation_is_persisted(pool):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, TickView)
        try:
            await _wait_for("tick")
            frames, _ = await _collect(socket, _update_from("tick"))
            assert "tick" in RAN, (RAN, frames)
        finally:
            await socket.disconnect()
        assert ">3<" in await _restored_count(request, TickView), "tick state not persisted"
    assert pool.offloaded_tick >= (1 if pool.size else 0), pool.offloaded_tick
    if not pool.size:
        assert pool.offloaded_tick == 0, pool.offloaded_tick


async def test_tick_on_a_revoked_session_is_denied(pool):
    """A tick on an explicit root whose session was revoked runs no
    ``handle_tick``, sends no render and gets the foreground denial, on both
    tick paths (#4166: the pool's turn authorizes inside its hop)."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, TickView)
        try:
            await sync_to_async(SessionStore(request.session.session_key).delete)()
            frames, closed = await _collect(
                socket, lambda frames: frames[-1:] and frames[-1]["type"] == "websocket.close"
            )
            assert "tick" not in RAN, "handle_tick ran without current authorization"
            assert not [f for f in frames if f.get("type") in {"patch", "html_update"}], frames
            assert [f.get("code") for f in frames if f.get("type") == "error"] == [
                "permission_denied"
            ]
            assert closed == {"type": "websocket.close", "code": 4403}
        finally:
            await socket.disconnect()
    assert pool.offloaded_tick == (1 if pool.size else 0), pool.offloaded_tick


class LabelTickView(LabelTurnView):
    count = state(0, persist="server")
    label = state("a", persist="server")
    tick_interval = 300

    def handle_tick(self):
        if self.count != 4:
            RAN.append("tick")
            self.count = 4


def _failing_save(monkeypatch):
    from djust.runtime import ViewRuntime

    real_save = ViewRuntime._save_explicit_root
    fail = {"on": True}

    def save(self, view, request):
        if fail["on"]:
            raise RuntimeError("storage down")
        return real_save(self, view, request)

    monkeypatch.setattr(ViewRuntime, "_save_explicit_root", save)
    return fail


async def test_tick_whose_save_fails_withholds_the_frame(pool, monkeypatch):
    """A tick whose commit fails sends ``state_error`` and no render; the next
    tick (forced to full HTML by the failure) brings the page up to the view.
    The pool's turn rendered in its hop before the commit, so it must withhold
    that render (#4166)."""
    fail = _failing_save(monkeypatch)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, LabelTickView)
        try:
            frames = await receive_until(socket, has_type("error"), what="the tick's state_error")
            fail["on"] = False
            assert RAN == ["tick"], (RAN, frames)
            assert [f.get("code") for f in frames if f.get("type") == "error"] == ["state_error"]
            assert not [f for f in frames if f.get("type") in {"patch", "html_update"}], frames
            frames = await receive_until(socket, _update_from("tick"), what="the next tick")
            updates = [f for f in frames if f.get("source") == "tick"]
            last = updates[-1]
            assert "4" in json.dumps(last.get("patches") or last.get("html")), updates
        finally:
            await socket.disconnect()
        restored = await _restored_count(request, LabelTickView)
        assert ">4<" in restored, restored
    assert pool.offloaded_tick >= (1 if pool.size else 0), pool.offloaded_tick
    if not pool.size:
        assert pool.offloaded_tick == 0, pool.offloaded_tick


async def test_db_notify_whose_save_fails_withholds_the_frame(pool, monkeypatch):
    """As for push and tick: a NOTIFY turn whose commit fails withholds its
    frame and, on the pool, its diff baseline (#4166)."""
    from djust.push import apush_to_view

    fail = _failing_save(monkeypatch)
    path = __name__ + ".LabelTurnView"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, LabelTurnView)
        try:
            await _notify({"count": 4})
            frames, closed = await _collect(socket, has_type("error"))
            assert RAN == ["notify"] and closed is None, (RAN, frames)
            assert [f.get("code") for f in frames if f.get("type") == "error"] == ["state_error"]
            assert not [f for f in frames if f.get("type") in {"patch", "html_update"}], frames
            fail["on"] = False
            await apush_to_view(path, handler="handle_label", payload={"label": "b"})
            frames, _ = await _collect(socket, _UPDATE)
            updates = [f for f in frames if f.get("type") in {"patch", "html_update"}]
            assert len(updates) == 1, frames
            assert "4" in json.dumps(updates[0]), updates
        finally:
            await socket.disconnect()
        restored = await _restored_count(request, LabelTurnView)
        assert ">4<" in restored and ">b<" in restored, restored
    assert pool.offloaded_notify == (1 if pool.size else 0), pool.offloaded_notify


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


async def test_start_async_from_a_server_originated_turn_runs():
    """``start_async`` queued in ``handle_info`` (and likewise ``handle_tick`` or
    a ``server_push`` handler) was never dispatched: those turns do not drain
    the queue. It now runs and renders, and an explicit root persists it."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, NotifyWorkView)
        try:
            await _notify({})
            await _wait_for("work")
            frames, closed = await _collect(socket, _update_from("async"))
            assert RAN[:2] == ["notify", "work"], (RAN, frames)
            results = [f for f in frames if f.get("source") == "async"]
            assert results and "21" in json.dumps(results), frames
        finally:
            await socket.disconnect()
        assert ">21<" in await _restored_count(request, NotifyWorkView)


class HotReloadFailureView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0)

    def get_context_data(self, **kwargs):
        if getattr(self, "_fail_render", False):
            raise ValueError("HOTRELOAD_RENDER_SENTINEL")
        return super().get_context_data(count=self.count, **kwargs)


@pytest.mark.parametrize("debug", [True, False])
async def test_hot_reload_render_failure_is_value_free_for_explicit_views(debug, caplog):
    """Dev hot reload re-renders the mounted view, which runs its
    ``get_context_data``. Its catch-all logged the exception, with the
    traceback. Under ``DEBUG=False`` an explicit view's failure must be
    value-free; under ``DEBUG=True`` it logs the exception (ADR-038 D-a,
    revised 2026-09-22)."""
    import logging

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **dict(SETTINGS, DEBUG=debug)):
        request = await sync_to_async(make_request)()
        socket, _ = await _connect(request, HotReloadFailureView)
        try:
            # Fail the hot-reload render from the outside, as a code edit might.
            HotReloadFailureView._fail_render = True
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
            HotReloadFailureView._fail_render = False
            await socket.disconnect()
    if debug:
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


async def test_notify_turn_refreshes_the_client_snapshot(pool):
    """A NOTIFY turn that changes a ``persist="client"`` field carries the
    refreshed signed token for the primary view (ADR-038 E3), on both
    db_notify paths (#4166)."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS, **pool.settings):
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
    assert pool.offloaded_notify == (1 if pool.size else 0), pool.offloaded_notify
