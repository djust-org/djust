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

from ._ws_frames import receive_type, receive_until  # noqa: E402

VIEW = f"{__name__}._RoomView"


class _RoomView(LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_scoped_push_3004._RoomView">'
        "{{ room }}:{{ pings }}:{{ tag }}</div>"
    )

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "lobby")
        self.pings = 0
        self.tag = ""
        self.push_scope = self.room

    def handle_ping(self, tag: str = "", **kwargs):
        self.pings += 1
        self.tag = tag

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


class _TickView(LiveView):
    """Moves itself from room t1 to t2 on its first tick."""

    template = '<div dj-root dj-view="djust.tests.test_scoped_push_3004._TickView">{{ room }}:{{ pings }}:{{ tag }}</div>'
    tick_interval = 50

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "t1")
        self.pings = 0
        self.tag = ""
        self.push_scope = self.room

    def handle_tick(self):
        if self.room == "t1":
            self.room = "t2"
            self.push_scope = "t2"
        else:
            self._skip_render = True

    def handle_ping(self, tag: str = "", **kwargs):
        self.pings += 1
        self.tag = tag


class _InfoView(LiveView):
    """Moves to the room a db_notify payload names (``handle_info``)."""

    template = '<div dj-root dj-view="djust.tests.test_scoped_push_3004._InfoView">{{ room }}:{{ pings }}</div>'

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "i1")
        self.pings = 0
        self.push_scope = self.room
        self._listen_channels = {"moves_3004"}

    def handle_info(self, message):
        self.room = message["payload"]["room"]
        self.push_scope = self.room

    def handle_ping(self, **kwargs):
        self.pings += 1


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
    """The patch a push produced: waits for it, event-driven (#3130)."""
    return (await receive_type(communicator, "patch"))[-1]


OLD, NEW = "OLD_SCOPE_PING", "NEW_SCOPE_PING"


async def _moved_away(communicator, view, old, new):
    """Push to the session's old scope, then its new one; the session must see
    only the new one's patch.

    Each push carries a tag the view renders. The consumer handles its channel
    messages one at a time, in order, so if the session is still in the old
    scope's group, the OLD push is handled, and its patch sent, before the NEW
    one. Waiting for the NEW patch is therefore a barrier: every frame the old
    push could produce is already in hand, however slow the pushes are. The
    plain quiet-window check it replaced was outlived by a slow push (#3130
    review). Returns the NEW patch.
    """
    await apush_to_view(view, handler="handle_ping", payload={"tag": OLD}, scope=old)
    await apush_to_view(view, handler="handle_ping", payload={"tag": NEW}, scope=new)
    frames = await receive_until(
        communicator,
        lambda fs: any(f.get("type") == "patch" and NEW in _patch_text(f) for f in fs),
        what="the new scope's patch",
    )
    stale = [f for f in frames if OLD in _patch_text(f)]
    assert not stale, f"the session still got its old scope's push: {stale!r}"
    return frames[-1]


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
            await apush_to_view(VIEW, handler="handle_ping", payload={"tag": OLD}, scope="r1")
            fa, fb = await asyncio.gather(_pinged(a), _pinged(b))
            assert fa is not None and "r1:1" in _patch_text(fa)
            assert fb is not None and "r1:1" in _patch_text(fb)

            # A push without scope is unchanged: every session of the view. It
            # is also the barrier for c: c handles its channel messages in
            # order, so an r1 push that wrongly reached it is in hand before
            # this one's patch (see _moved_away).
            await apush_to_view(VIEW, handler="handle_ping", payload={"tag": NEW})
            fa, fb = await asyncio.gather(_pinged(a), _pinged(b))
            assert fa and fb
            frames = await receive_until(
                c,
                lambda fs: any(f.get("type") == "patch" and NEW in _patch_text(f) for f in fs),
                what="the unscoped push's patch",
            )
            stale = [f for f in frames if OLD in _patch_text(f)]
            assert not stale, f"a session in r2 got r1's push: {stale!r}"
            assert "r2:1" in _patch_text(frames[-1])
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

            frame = await _moved_away(a, VIEW, "r1", "r9")
            assert "r9:1" in _patch_text(frame)
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
            frame = await _moved_away(a, VIEW, "r1", "r5")
            assert "r5:1" in _patch_text(frame)
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_tick_that_changes_push_scope_moves_the_session():
    view = f"{__name__}._TickView"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect(view, "t1")
        try:
            moved = await _receive_until(a, "patch")  # the first tick's render
            assert "t2" in _patch_text(moved)
            await apush_to_view(view, handler="handle_ping", scope="t2")
            frame = await _pinged(a)
            assert frame is not None and "t2:1" in _patch_text(frame)
            frame = await _moved_away(a, view, "t1", "t2")
            assert "t2:2" in _patch_text(frame)
        finally:
            await a.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_handle_info_that_changes_push_scope_moves_the_session():
    from channels.layers import get_channel_layer

    view = f"{__name__}._InfoView"
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        a = await _connect(view, "i1")
        try:
            await get_channel_layer().group_send(
                "djust_db_notify_moves_3004",
                {"type": "db_notify", "channel": "moves_3004", "payload": {"room": "i2"}},
            )
            moved = await _receive_until(a, "patch")
            assert "i2" in _patch_text(moved)
            await apush_to_view(view, handler="handle_ping", scope="i2")
            frame = await _pinged(a)
            assert frame is not None and "i2:1" in _patch_text(frame)
        finally:
            await a.disconnect()


class _FlakyLayer:
    """A channel layer whose first group_discard fails."""

    def __init__(self):
        self.groups: dict = {}
        self.fail_next_discard = True

    async def group_add(self, group, channel):
        self.groups.setdefault(group, set()).add(channel)

    async def group_discard(self, group, channel):
        if self.fail_next_discard:
            self.fail_next_discard = False
            raise ConnectionError("layer down")
        self.groups.get(group, set()).discard(channel)


class _FakeConsumer:
    def __init__(self):
        self.channel_layer = _FlakyLayer()
        self.channel_name = "chan-1"
        self._view_path = VIEW


class _FakeView:
    push_scope = "a"


def test_a_failed_leave_is_retried_on_the_next_sync():
    from djust.push import sync_push_scope_groups

    consumer, view = _FakeConsumer(), _FakeView()
    asyncio.run(sync_push_scope_groups(consumer, view))
    group_a = push_scope_group_name(VIEW, "a")
    assert consumer.channel_layer.groups[group_a] == {"chan-1"}
    view.push_scope = "b"
    asyncio.run(sync_push_scope_groups(consumer, view))  # the leave fails
    assert consumer.channel_layer.groups[group_a] == {"chan-1"}
    assert "a" in consumer._push_scope_groups  # still recorded ...
    asyncio.run(sync_push_scope_groups(consumer, view))  # ... so it is retried
    assert consumer.channel_layer.groups[group_a] == set()
    assert set(consumer._push_scope_groups) == {"b"}


def test_an_invalid_push_scope_warns_once_until_it_is_valid_again(caplog):
    from djust.push import sync_push_scope_groups

    consumer, view = _FakeConsumer(), _FakeView()
    consumer.channel_layer.fail_next_discard = False
    view.push_scope = 1.5
    with caplog.at_level("WARNING", logger="djust.push"):
        for _ in range(5):  # e.g. five ticks
            asyncio.run(sync_push_scope_groups(consumer, view))
        assert caplog.text.count("push_scope is invalid") == 1
        view.push_scope = "ok"
        asyncio.run(sync_push_scope_groups(consumer, view))
        view.push_scope = 1.5
        asyncio.run(sync_push_scope_groups(consumer, view))
    assert caplog.text.count("push_scope is invalid") == 2
