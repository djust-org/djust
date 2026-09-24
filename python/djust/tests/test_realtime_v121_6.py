"""v1.2.1-6 — realtime / multiplayer correctness and DX.

* #2968 — the client's 30 s ``ping`` refreshes a tracked presence, so users
  no longer drop out of ``list_presences()`` after ~60 s on an open page.
* #3007 — the lifecycle schema no longer advertises LiveView hooks the
  server never calls.
* #3003 — ``@rate_limit`` documents that rejections count toward the 4429
  disconnect.

(#3002, V004 and ``handle_*``, is pinned in ``python/tests/test_checks.py``.)
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.presence import PresenceManager, PresenceMixin

_MOD = "djust.tests.test_realtime_v121_6"


class RoomView(PresenceMixin, LiveView):
    template = "<div dj-root><p>{{ online }}</p></div>"
    presence_key = "room_2968"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.online = 0
        self.track_presence(meta={"name": "p"})

    def get_presence_user_id(self) -> str:
        return "player-2968"


class PlainView(LiveView):
    template = "<div dj-root><p>plain</p></div>"

    def mount(self, request: Any, **kwargs: Any) -> None:
        pass


class _ScopeSession:
    def __init__(self, key: str) -> None:
        self.session_key = key


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _connect_and_mount(view_path: str):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": "/room/"})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", mount_frame
    return communicator


def _heartbeats(key: str):
    backend = PresenceManager._backend()
    return {uid: ts for (k, uid), ts in backend._heartbeats.items() if k == key}


@pytest.mark.django_db(transaction=True)
class TestPingIsThePresenceHeartbeat:
    @pytest.mark.asyncio
    async def test_ping_refreshes_a_tracked_presence(self):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
            communicator = await _connect_and_mount(f"{_MOD}.RoomView")
            try:
                beats = _heartbeats("room_2968")
                assert len(beats) == 1, beats
                (uid,) = beats
                backend = PresenceManager._backend()
                # Age the heartbeat to just under the 60 s timeout.
                backend._heartbeats[("room_2968", uid)] = time.time() - 59
                await communicator.send_json_to({"type": "ping"})
                pong = await _receive_until(communicator, "pong")
                assert pong["type"] == "pong"
                assert time.time() - _heartbeats("room_2968")[uid] < 5
                # Still listed after the old heartbeat would have expired.
                assert uid in {p["id"] for p in PresenceManager.list_presences("room_2968")}
            finally:
                await communicator.disconnect()

    @pytest.mark.asyncio
    async def test_ping_on_a_plain_view_is_just_a_pong(self):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
            communicator = await _connect_and_mount(f"{_MOD}.PlainView")
            try:
                await communicator.send_json_to({"type": "ping"})
                pong = await _receive_until(communicator, "pong")
                assert pong == {"type": "pong"}
                assert await communicator.receive_nothing(timeout=0.2)
            finally:
                await communicator.disconnect()


class TestLifecycleSchema:
    def test_no_phantom_view_hooks(self):
        from djust.schema import LIFECYCLE_METHODS

        names = {m["name"] for m in LIFECYCLE_METHODS}
        assert not names & {"connected", "disconnected", "unmount"}, names
        assert {"mount", "get_context_data", "handle_params", "handle_tick"} <= names

    def test_every_listed_hook_is_called_by_the_framework(self):
        """Each schema entry names a method the framework really invokes —
        guards against the #3007 class of phantom entries coming back."""
        import pathlib

        import djust
        from djust.schema import LIFECYCLE_METHODS

        root = pathlib.Path(djust.__file__).parent
        sources = "\n".join(
            p.read_text(encoding="utf-8")
            for p in root.rglob("*.py")
            if "tests" not in p.parts and p.name != "schema.py"
        )
        for method in LIFECYCLE_METHODS:
            name = method["name"]
            assert f".{name}(" in sources or f'"{name}"' in sources, name


class TestRateLimitDocstring:
    def test_documents_the_disconnect(self):
        from djust.decorators import rate_limit

        doc = rate_limit.__doc__ or ""
        assert "4429" in doc and "max_warnings" in doc
