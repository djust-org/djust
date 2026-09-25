"""Event-loop offload with the worker pool on (#3074 4/7).

With ``LIVEVIEW_CONFIG["worker_threads"]`` on, djust moves per-frame work off
the asyncio event loop onto the session's pool thread:

* the pre-event assigns snapshot runs in the SAME hop as a sync handler;
* a server-push turn (Django's connection check, hooks, state sync, render,
  diff) is ONE hop, and the Rust patch JSON is spliced into the frame;
* ``dispatch`` does not make Channels' separate ``close_old_connections`` hop
  for a ``server_push`` message; the turn runs the check itself.

With the setting off, all three stay exactly as before. Each test runs both
ways against real ``WebsocketCommunicator`` sessions.
"""

from __future__ import annotations

import json
import threading

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.decorators import event_handler
from djust.push import apush_to_view

pytest.importorskip("channels")

VIEW = f"{__name__}._OffloadView"
_SEEN: dict = {}


def _note(key):
    _SEEN.setdefault(key, []).append(threading.get_ident())


class _OffloadView(LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_event_loop_offload_3074._OffloadView">'
        "<b>{{ count }}</b><i>{{ label }}</i></div>"
    )

    def mount(self, request, **kwargs):
        self.count = 0
        self.label = "start"

    @event_handler()
    def bump(self, **kwargs):
        _note("handler")
        self.count += 1

    def handle_push(self, label: str = "", **kwargs):
        _note("hook")
        self.label = label

    def handle_join(self, room: str = "", **kwargs):
        self.label = room
        self.push_scope = room

    def _sync_state_to_rust(self, *args, **kwargs):
        _note("render")
        return super()._sync_state_to_rust(*args, **kwargs)


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    _SEEN.clear()
    yield request.param
    _SEEN.clear()


@pytest.fixture
def spies(monkeypatch):
    """Record which thread snapshots and connection checks run on."""
    import channels.consumer
    import django.db

    import djust.websocket as ws

    calls = {"aclose": 0}
    real_snapshot = ws._snapshot_assigns
    real_close = django.db.close_old_connections
    real_aclose = channels.consumer.aclose_old_connections

    def snapshot(view):
        _note("snapshot")
        return real_snapshot(view)

    def close_old_connections(**kwargs):
        _note("close_old_connections")
        return real_close(**kwargs)

    async def aclose_old_connections():
        calls["aclose"] += 1
        return await real_aclose()

    monkeypatch.setattr(ws, "_snapshot_assigns", snapshot)
    monkeypatch.setattr(django.db, "close_old_connections", close_old_connections)
    monkeypatch.setattr(channels.consumer, "aclose_old_connections", aclose_old_connections)
    return calls


async def _connect():
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    key = await sync_to_async(_create_session)()

    class _ScopeSession:
        session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to({"type": "mount", "view": VIEW, "url": "/offload/"})
    frame = await _receive_until(communicator, "mount")
    assert frame.get("type") == "mount", frame
    return communicator


async def _receive_until(communicator, wanted, *, tries=8, timeout=5):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted:
            return last
    return last


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pre_event_snapshot_runs_with_the_handler(pool, spies):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            _SEEN.clear()
            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
            frame = await _receive_until(comm, "patch")
        finally:
            await comm.disconnect()
    assert "1" in json.dumps(frame["patches"])
    pre_snapshot = _SEEN["snapshot"][0]  # the first snapshot is the pre-handler one
    (handler,) = _SEEN["handler"]
    if pool:
        assert pre_snapshot == handler != loop_thread
    else:
        assert pre_snapshot == loop_thread != handler


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_server_push_turn_is_one_hop_with_the_pool(pool, spies):
    loop_thread = threading.get_ident()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            _SEEN.clear()
            before = spies["aclose"]
            await apush_to_view(VIEW, handler="handle_push", payload={"label": "pushed"})
            frame = await _receive_until(comm, "patch")
            aclose_calls = spies["aclose"] - before
        finally:
            await comm.disconnect()
    assert frame["broadcast"] is True and frame["source"] == "broadcast"
    assert "pushed" in json.dumps(frame["patches"])
    (hook,) = _SEEN["hook"]
    (render,) = _SEEN["render"]
    assert hook != loop_thread
    if pool:
        # Connection check, hook and render on the session's thread, and no
        # separate Channels hop for the push message.
        assert _SEEN["close_old_connections"] == [hook]
        assert render == hook
        assert aclose_calls == 0
    else:
        # Stock: Channels' dispatch made its own hop for the message.
        assert aclose_calls == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pushes_keep_their_order_and_versions(pool):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            frames = []
            for i in range(5):
                await apush_to_view(VIEW, handler="handle_push", payload={"label": f"L{i}"})
                frames.append(await _receive_until(comm, "patch"))
            # A user event after the pushes continues the same version sequence.
            await comm.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 9})
            frames.append(await _receive_until(comm, "patch"))
        finally:
            await comm.disconnect()
    versions = [f["version"] for f in frames]
    assert versions == list(range(versions[0], versions[0] + 6)), versions
    for i in range(5):
        assert f"L{i}" in json.dumps(frames[i]["patches"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_offloaded_push_frame_matches_the_stock_frame(monkeypatch):
    """The spliced frame is the same JSON object the stock path sends."""
    from djust import worker_pool

    frames = {}
    for mode, size in (("stock", None), ("pool", 2)):
        monkeypatch.setitem(djust_config._config, "worker_threads", size)
        monkeypatch.setattr(worker_pool, "_pool", [])
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            comm = await _connect()
            try:
                await apush_to_view(VIEW, handler="handle_push", payload={"label": "same"})
                frames[mode] = await _receive_until(comm, "patch")
            finally:
                await comm.disconnect()
    assert frames["pool"] == frames["stock"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_push_hook_that_changes_push_scope_moves_the_session(pool):
    """Both push-turn paths sync the scopes a hook changed (#3004)."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        comm = await _connect()
        try:
            await apush_to_view(VIEW, handler="handle_join", payload={"room": "z1"})
            await _receive_until(comm, "patch")
            await apush_to_view(VIEW, handler="handle_push", payload={"label": "in-z1"}, scope="z1")
            frame = await _receive_until(comm, "patch")
            assert "in-z1" in json.dumps(frame["patches"])
        finally:
            await comm.disconnect()
