"""A templated ``presence_key`` joins the FORMATTED presence group (#3202).

``presence_key = "chat:{room}"`` interpolates attributes that ``mount()`` sets.
The WebSocket transport used to join the presence group in
``on_view_mounted``, which runs BEFORE ``mount()``: ``get_presence_key()`` then
fell back to the unformatted key, every room shared the group
``djust_presence_chat:{room}``, and ``broadcast_to_presence()`` (sent after
mount, to ``djust_presence_chat:w1``) reached nobody.

The join now happens after mount() / session restore, in
``on_mount_render_ready``. The consumer records every presence group it joined
(a ``mount_batch`` can join several), and both ``disconnect`` and a
``live_redirect`` teardown leave all of them.
"""

from __future__ import annotations

import asyncio

import pytest
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.presence import PresenceManager, PresenceMixin

pytest.importorskip("channels")

pytestmark = pytest.mark.django_db


class _RoomChatView(PresenceMixin, LiveView):
    login_required = False
    presence_key = "chat:{room}"
    template = '<div dj-view="chat" dj-id="0">{{ room }}</div>'

    def mount(self, request, room="lobby", **kwargs):
        self.room = room
        self.track_presence(meta={})

    def get_presence_user_id(self):
        # The bare test socket carries no auth middleware (no request.user).
        return f"conn_{self._websocket_session_id}"

    @event_handler()
    def wave(self, **kwargs):
        self.broadcast_to_presence("wave", {"room": self.room})


def _path() -> str:
    return f"{__name__}.{_RoomChatView.__name__}"


def _members(group: str) -> list:
    from channels.layers import get_channel_layer

    return list(get_channel_layer().groups.get(group, {}).keys())


def _group(key: str) -> str:
    return PresenceManager.presence_group_name(key)


async def _connect():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    return communicator


async def _mount(room: str):
    communicator = await _connect()
    await communicator.send_json_to(
        {"type": "mount", "view": _path(), "url": f"/chat/{room}/", "params": {"room": room}}
    )
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError("no mount frame")


async def _drain(communicator) -> list:
    """Every frame that arrives until the socket goes quiet."""
    frames = []
    # receive_nothing() does not cancel the application on a timeout, unlike
    # receive_json_from().
    while not await communicator.receive_nothing(timeout=0.5):
        frames.append(await communicator.receive_json_from(timeout=2))
    return frames


async def _close(communicator) -> None:
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


@pytest.mark.asyncio
async def test_broadcast_reaches_only_the_same_rooms_sessions():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _mount("w1")
        b = await _mount("w1")
        c = await _mount("w2")
        try:
            # The formatted groups hold the room's sessions; the unformatted
            # group, shared by every room before the fix, holds nobody.
            assert len(_members(_group("chat:w1"))) == 2
            assert len(_members(_group("chat:w2"))) == 1
            assert _members(_group("chat:{room}")) == []

            for socket in (a, b, c):
                await _drain(socket)

            await a.send_json_to({"type": "event", "event": "wave", "params": {}, "ref": 1})

            def _waves(frames):
                return [f for f in frames if f.get("type") == "presence_event"]

            got_a, got_b, got_c = [_waves(await _drain(s)) for s in (a, b, c)]
            assert [f["payload"] for f in got_a] == [{"room": "w1"}]
            assert [f["payload"] for f in got_b] == [{"room": "w1"}]
            assert got_c == []
        finally:
            for socket in (a, b, c):
                await _close(socket)


@pytest.mark.asyncio
async def test_disconnect_leaves_the_formatted_group():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _mount("leave-room")
        assert len(_members(_group("chat:leave-room"))) == 1
        await _close(a)
        assert _members(_group("chat:leave-room")) == []


class _OtherRoomChatView(_RoomChatView):
    """A second presence view with the same templated key, for redirects and batches."""


def _other_path() -> str:
    return f"{__name__}.{_OtherRoomChatView.__name__}"


@pytest.mark.asyncio
async def test_live_redirect_leaves_the_old_rooms_group():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _mount("r1")
        peer = None
        try:
            await a.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": _other_path(),
                    "url": "/chat/r2/",
                    "params": {"room": "r2"},
                }
            )
            for _ in range(8):
                if (await a.receive_json_from(timeout=3)).get("type") == "mount":
                    break
            await _drain(a)
            assert _members(_group("chat:r1")) == []
            assert len(_members(_group("chat:r2"))) == 1

            peer = await _mount("r1")
            await _drain(a)
            await _drain(peer)
            await peer.send_json_to({"type": "event", "event": "wave", "params": {}, "ref": 1})
            got = [f for f in await _drain(a) if f.get("type") == "presence_event"]
            assert got == [], "the session that moved to r2 still gets r1's broadcasts"
        finally:
            await _close(a)
            if peer is not None:
                await _close(peer)
        assert _members(_group("chat:r1")) == []
        assert _members(_group("chat:r2")) == []


@pytest.mark.asyncio
async def test_mount_batch_with_two_presence_views_leaves_both_groups():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect()
        try:
            await a.send_json_to(
                {
                    "type": "mount_batch",
                    "views": [
                        {
                            "view": _path(),
                            "url": "/c/b1/",
                            "params": {"room": "b1"},
                            "target_id": "t1",
                        },
                        {
                            "view": _other_path(),
                            "url": "/c/b2/",
                            "params": {"room": "b2"},
                            "target_id": "t2",
                        },
                    ],
                }
            )
            await _drain(a)
            assert len(_members(_group("chat:b1"))) == 1
            assert len(_members(_group("chat:b2"))) == 1
        finally:
            await _close(a)
        assert _members(_group("chat:b1")) == []
        assert _members(_group("chat:b2")) == []


def test_only_views_with_presence_join_a_presence_group():
    """TenantMixin defines get_presence_key too; a TenantMixin-only view joins
    nothing, so its key is not read (a thread hop per mount). A view with its
    own get_presence_key, or with PresenceMixin, still joins."""
    from djust.runtime import _joins_presence_group
    from djust.tenants.mixin import TenantMixin

    class _TenantOnly(TenantMixin):
        pass

    class _TenantPresence(PresenceMixin, TenantMixin):
        pass

    class _OwnKey:
        def get_presence_key(self):
            return "k"

    assert _joins_presence_group(object.__new__(_TenantOnly)) is False
    assert _joins_presence_group(object.__new__(_TenantPresence)) is True
    assert _joins_presence_group(_OwnKey()) is True
    assert _joins_presence_group(object()) is False
