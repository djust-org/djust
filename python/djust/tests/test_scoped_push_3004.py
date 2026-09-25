"""Scoped server push: ``push_to_view(..., scope=...)`` + ``LiveView.push_scope`` (#3004).

A view that serves many independent rooms sets ``push_scope`` (usually in
mount); a push with ``scope=`` then reaches only the sessions in that scope,
instead of every session of the view in every room. A push without ``scope``
is unchanged and still reaches every session.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.push import (
    apush_to_view,
    push_scope_group_name,
    push_to_view,
    view_group_name,
    view_push_scopes,
)

pytest.importorskip("channels")

VIEW = f"{__name__}._RoomView"


class _RoomView(LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_scoped_push_3004._RoomView">'
        "{{ room }}:{{ pings }}</div>"
    )

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "lobby")
        self.pings = 0
        self.push_scope = self.room

    def handle_ping(self, **kwargs):
        self.pings += 1

    def handle_move(self, room: str = "", **kwargs):
        self.room = room
        self.push_scope = room

    @event_handler()
    def move(self, room: str = "", **kwargs):
        self.room = room
        self.push_scope = room


class _OtherView(LiveView):
    template = '<div dj-root dj-view="djust.tests.test_scoped_push_3004._OtherView">other</div>'

    def mount(self, request, **kwargs):
        pass


class _BadScopeView(LiveView):
    template = '<div dj-root dj-view="djust.tests.test_scoped_push_3004._BadScopeView">x</div>'

    def mount(self, request, **kwargs):
        self.push_scope = 3.5  # not a str / int


# ---------------------------------------------------------------------------
# Group names and validation
# ---------------------------------------------------------------------------


def test_scope_group_name_is_a_valid_distinct_channels_group():
    import re

    names = {
        push_scope_group_name(VIEW, "room 1"),
        push_scope_group_name(VIEW, "room_1"),
        push_scope_group_name(VIEW, "room-1/ü"),
        push_scope_group_name("other.views.View", "room 1"),
    }
    assert len(names) == 4  # sanitising would have merged "room 1" / "room_1"
    for name in names:
        assert re.fullmatch(r"[A-Za-z0-9_.-]{1,99}", name), name
        assert name != view_group_name(VIEW)
    # An int scope and its string spelling are the same scope.
    assert push_scope_group_name(VIEW, 42) == push_scope_group_name(VIEW, "42")


@pytest.mark.parametrize("bad", [True, 1.5, None, ["r"], b"r"])
def test_push_rejects_a_scope_that_is_not_str_or_int(bad):
    with pytest.raises(TypeError):
        push_scope_group_name(VIEW, bad)


def test_push_rejects_an_empty_scope():
    with pytest.raises(ValueError):
        push_scope_group_name(VIEW, "")


def test_view_push_scopes_spellings():
    class V:
        push_scope = None

    v = V()
    assert view_push_scopes(v) == frozenset()
    v.push_scope = "r1"
    assert view_push_scopes(v) == {"r1"}
    v.push_scope = 7
    assert view_push_scopes(v) == {"7"}
    v.push_scope = ["r1", 7, "r1"]
    assert view_push_scopes(v) == {"r1", "7"}
    # A generator (or map/filter) would be used up by the first sync and the
    # next one would leave every group, so only list/tuple/set are accepted.
    for bad in (1.5, {"r": 1}, b"r", [""], (r for r in ["r1"]), map(str, [1])):
        v.push_scope = bad
        with pytest.raises((TypeError, ValueError)):
            view_push_scopes(v)
    from djust.push import MAX_PUSH_SCOPES

    v.push_scope = [str(i) for i in range(MAX_PUSH_SCOPES)]
    assert len(view_push_scopes(v)) == MAX_PUSH_SCOPES
    v.push_scope = [str(i) for i in range(MAX_PUSH_SCOPES + 1)]
    with pytest.raises(ValueError, match="at most"):
        view_push_scopes(v)


def test_push_to_view_sends_to_the_scope_group_or_the_view_group(monkeypatch):
    sent = []

    class _Layer:
        async def group_send(self, group, message):
            sent.append((group, message))

    monkeypatch.setattr("djust.push.get_channel_layer", lambda: _Layer())
    push_to_view(VIEW, handler="handle_ping", scope="r1")
    push_to_view(VIEW, handler="handle_ping")
    asyncio.run(apush_to_view(VIEW, state={"x": 1}, scope=5))
    assert [g for g, _ in sent] == [
        push_scope_group_name(VIEW, "r1"),
        view_group_name(VIEW),
        push_scope_group_name(VIEW, 5),
    ]
    # The message itself is the unscoped message: same keys, same handling.
    assert set(sent[0][1]) == set(sent[1][1])


# ---------------------------------------------------------------------------
# End to end over real WebSocket consumers and the in-memory channel layer
# ---------------------------------------------------------------------------


async def _connect(view, room):
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
    await communicator.receive_json_from(timeout=3)  # connect frame
    await communicator.send_json_to({"type": "mount", "view": view, "url": f"/r/?room={room}"})
    frame = await _receive_until(communicator, "mount")
    assert frame.get("type") == "mount", frame
    return communicator


async def _receive_until(communicator, wanted, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted:
            return last
    return last


async def _pinged(communicator):
    """The patch a push produced, or None when nothing arrives.

    ``receive_nothing`` waits without cancelling the application (a
    ``receive_json_from`` timeout would cancel it).
    """
    if await communicator.receive_nothing(timeout=0.5, interval=0.02):
        return None
    frame = await _receive_until(communicator, "patch", tries=3, timeout=3)
    return frame if frame.get("type") == "patch" else None


def _patch_text(frame):
    import json

    return json.dumps(frame.get("patches"))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scoped_push_reaches_only_the_sessions_in_that_scope():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect(VIEW, "r1")
        b = await _connect(VIEW, "r1")
        c = await _connect(VIEW, "r2")
        try:
            await apush_to_view(VIEW, handler="handle_ping", scope="r1")
            fa, fb, fc = await asyncio.gather(_pinged(a), _pinged(b), _pinged(c))
            assert fa is not None and "r1:1" in _patch_text(fa)
            assert fb is not None and "r1:1" in _patch_text(fb)
            assert fc is None, f"a session in r2 got r1's push: {fc!r}"

            # A push without scope is unchanged: every session of the view.
            await apush_to_view(VIEW, handler="handle_ping")
            fa, fb, fc = await asyncio.gather(_pinged(a), _pinged(b), _pinged(c))
            assert fa and fb and fc
            assert "r2:1" in _patch_text(fc)
        finally:
            for comm in (a, b, c):
                await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_handler_that_changes_push_scope_moves_the_session():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect(VIEW, "r1")
        try:
            await a.send_json_to(
                {"type": "event", "event": "move", "params": {"room": "r9"}, "ref": 1}
            )
            moved = await _receive_until(a, "patch")
            assert "r9" in _patch_text(moved)

            await apush_to_view(VIEW, handler="handle_ping", scope="r1")
            assert await _pinged(a) is None, "the session still got its old scope's push"
            await apush_to_view(VIEW, handler="handle_ping", scope="r9")
            frame = await _pinged(a)
            assert frame is not None and "r9:1" in _patch_text(frame)
        finally:
            await a.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_disconnect_leaves_the_scope_groups():
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    group = push_scope_group_name(VIEW, "r-leave")
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect(VIEW, "r-leave")
        assert len(layer.groups.get(group, {})) == 1
        await a.disconnect()
        await asyncio.sleep(0.05)
    assert not layer.groups.get(group)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_invalid_push_scope_is_logged_and_the_mount_still_works(caplog):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        with caplog.at_level("WARNING", logger="djust.push"):
            a = await _connect(f"{__name__}._BadScopeView", "x")
            await a.disconnect()
    assert "push_scope is invalid" in caplog.text


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_push_hook_that_changes_push_scope_moves_the_session():
    """The server-push turn syncs the scopes a push's own hook changed."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect(VIEW, "r1")
        try:
            await apush_to_view(VIEW, handler="handle_move", payload={"room": "r5"}, scope="r1")
            assert await _pinged(a) is not None
            await apush_to_view(VIEW, handler="handle_ping", scope="r1")
            assert await _pinged(a) is None
            await apush_to_view(VIEW, handler="handle_ping", scope="r5")
            frame = await _pinged(a)
            assert frame is not None and "r5:1" in _patch_text(frame)
        finally:
            await a.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_live_redirect_leaves_the_old_views_scope_groups():
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    group = push_scope_group_name(VIEW, "r-redirect")
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect(VIEW, "r-redirect")
        try:
            assert len(layer.groups.get(group, {})) == 1
            await a.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": f"{__name__}._OtherView",
                    "url": "/other/",
                    "params": {},
                }
            )
            await _receive_until(a, "mount")
            assert not layer.groups.get(group), "the old view's scope group was not left"
        finally:
            await a.disconnect()
