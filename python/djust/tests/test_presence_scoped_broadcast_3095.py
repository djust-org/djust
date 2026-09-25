"""Presence broadcasts respect ``push_scope`` (#3095).

``PresenceMixin`` pushes ``_on_presence_change`` to peer sessions on every
join and leave so they refresh ``online_count``. Before #3095 that push
reached every session of the view in every room. A view that sets
``push_scope`` (or ``presence_broadcast_scoped = True``) now wakes only the
sessions that share the sender's presence key; ``presence_broadcast_scoped =
False`` and views without ``push_scope`` keep the view-wide broadcast.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.presence import PresenceMixin
from djust.push import presence_scope_group_name, push_scope_group_name

pytest.importorskip("channels")

MOD = __name__


def _tpl(name):
    return f'<div dj-root dj-view="{MOD}.{name}">{{{{ room }}}}:{{{{ online_count }}}}:{{{{ changes }}}}</div>'


class _ScopedRoomView(PresenceMixin, LiveView):
    """Snake's shape: push_scope = room, presence key per room."""

    template = _tpl("_ScopedRoomView")
    presence_key = "room3095:{room}"
    presence_unique_per_connection = True

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "lobby")
        self.push_scope = self.room
        self.changes = 0
        self.online_count = 0
        self.track_presence()

    @event_handler
    def _on_presence_change(self, **kwargs):
        self.changes += 1
        super()._on_presence_change(**kwargs)


class _UnscopedRoomView(_ScopedRoomView):
    """Same, without push_scope: the broadcast stays view-wide."""

    template = _tpl("_UnscopedRoomView")

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "lobby")
        self.changes = 0
        self.online_count = 0
        self.track_presence()


class _OptOutRoomView(_ScopedRoomView):
    """push_scope set, but the view keeps the view-wide broadcast."""

    template = _tpl("_OptOutRoomView")
    presence_broadcast_scoped = False


class _ViewerRoomView(_ScopedRoomView):
    """Watches a room without tracking its own presence (``?watch=1``)."""

    template = _tpl("_ViewerRoomView")

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "lobby")
        self.push_scope = self.room
        self.changes = 0
        self.online_count = 0
        if not request.GET.get("watch"):
            self.track_presence()


class _SitePresenceView(_ScopedRoomView):
    """Presence key broader than push_scope: one site-wide presence."""

    template = _tpl("_SitePresenceView")
    presence_key = "site3095"


# ---------------------------------------------------------------------------
# Unit
# ---------------------------------------------------------------------------


def test_presence_scope_group_is_a_valid_distinct_channels_group():
    from channels.layers import BaseChannelLayer

    view = f"{MOD}._ScopedRoomView"
    a = presence_scope_group_name(view, "room3095:a")
    assert a == presence_scope_group_name(view, "room3095:a")
    assert a != presence_scope_group_name(view, "room3095:b")
    assert a != presence_scope_group_name(f"{MOD}._OptOutRoomView", "room3095:a")
    # Never the group of a push_scope with the same text.
    assert a != push_scope_group_name(view, "room3095:a")
    assert BaseChannelLayer().require_valid_group_name(a)


@pytest.mark.parametrize(
    "flag, push_scope, scoped",
    [
        (None, None, False),
        (None, "r1", True),
        (None, ["r1", "r2"], True),
        (None, 3.5, False),  # invalid push_scope: stay view-wide
        (True, None, True),
        (False, "r1", False),
    ],
)
def test_when_the_broadcast_is_scoped(flag, push_scope, scoped):
    view = _ScopedRoomView()
    view.presence_broadcast_scoped = flag
    view.push_scope = push_scope
    assert view._presence_broadcast_is_scoped() is scoped


def test_scoped_broadcast_goes_to_the_presence_key_group(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "djust.presence.push_to_presence_scope", lambda *a, **k: sent.append(("scoped", a, k))
    )
    monkeypatch.setattr("djust.presence.push_to_view", lambda *a, **k: sent.append(("view", a, k)))
    view = _ScopedRoomView()
    view.room = "den"
    view.push_scope = "den"
    view._broadcast_presence_change()
    path = f"{MOD}._ScopedRoomView"
    assert sent == [
        ("scoped", (path, "room3095:den"), {"handler": "_on_presence_change", "payload": {}})
    ]

    sent.clear()
    view.push_scope = None
    view._broadcast_presence_change()
    assert sent == [("view", (path,), {"handler": "_on_presence_change", "payload": {}})]


def test_scoped_broadcast_uses_the_tracked_key(monkeypatch):
    sent = []
    monkeypatch.setattr("djust.presence.push_to_presence_scope", lambda *a, **k: sent.append(a))
    view = _ScopedRoomView()
    view.room = "new"
    view.push_scope = "new"
    view._presence_scope_key = "room3095:old"  # what track_presence recorded
    view._broadcast_presence_change()
    assert sent == [(f"{MOD}._ScopedRoomView", "room3095:old")]


def test_a_failing_broadcast_never_breaks_the_caller(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("layer down")

    monkeypatch.setattr("djust.presence.push_to_presence_scope", boom)
    view = _ScopedRoomView()
    view.room = "den"
    view.push_scope = "den"
    view._broadcast_presence_change()  # swallowed and logged at debug


# ---------------------------------------------------------------------------
# End to end over real WebSocket consumers and the in-memory channel layer
# ---------------------------------------------------------------------------


async def _connect(view, query):
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
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
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)  # connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{MOD}.{view}", "url": f"/r/?{query}"}
    )
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


async def _drain(communicator):
    """Every frame that arrives within 0.5 s (patches from presence pushes)."""
    frames = []
    while not await communicator.receive_nothing(timeout=0.5, interval=0.02):
        frames.append(await communicator.receive_json_from(timeout=3))
    return frames


def _text(frames):
    return json.dumps([f.get("patches") for f in frames if f.get("type") == "patch"])


async def _settle(*comms):
    return await asyncio.gather(*(_drain(c) for c in comms))


@pytest.fixture
def _fresh_presence():
    from djust.backends import registry

    registry.reset_presence_backend()
    yield
    registry.reset_presence_backend()


async def _join_wakes(view, *, same_room_query, other_room_query, joiner_query):
    """Connect one session in room A and one in room B, then join room A.

    Returns the frames (A, B) the join produced.
    """
    a = await _connect(view, same_room_query)
    b = await _connect(view, other_room_query)
    comms = [a, b]
    try:
        await _settle(a, b)
        c = await _connect(view, joiner_query)
        comms.append(c)
        fa, fb = await _settle(a, b)
        return fa, fb
    finally:
        for comm in comms:
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_join_wakes_only_the_room_that_shares_the_presence_key(_fresh_presence):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        fa, fb = await _join_wakes(
            "_ScopedRoomView",
            same_room_query="room=a",
            other_room_query="room=b",
            joiner_query="room=a",
        )
    assert "a:2:" in _text(fa), f"room a did not see its count change: {fa!r}"
    assert not [f for f in fb if f.get("type") == "patch"], f"room b was woken: {fb!r}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_without_push_scope_every_room_is_woken_as_before(_fresh_presence):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        fa, fb = await _join_wakes(
            "_UnscopedRoomView",
            same_room_query="room=a",
            other_room_query="room=b",
            joiner_query="room=a",
        )
    assert "a:2:" in _text(fa)
    # Room b's handler ran (its change counter moved) although its count did not.
    assert "b:1:" in _text(fb), f"room b was not woken: {fb!r}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_presence_broadcast_scoped_false_keeps_the_view_wide_broadcast(_fresh_presence):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        fa, fb = await _join_wakes(
            "_OptOutRoomView",
            same_room_query="room=a",
            other_room_query="room=b",
            joiner_query="room=a",
        )
    assert "a:2:" in _text(fa)
    assert "b:1:" in _text(fb), f"room b was not woken: {fb!r}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_viewer_that_does_not_track_still_sees_its_room_change(_fresh_presence):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        fa, fb = await _join_wakes(
            "_ViewerRoomView",
            same_room_query="room=a&watch=1",
            other_room_query="room=b&watch=1",
            joiner_query="room=a",
        )
    assert "a:1:1" in _text(fa), f"the watching session was not refreshed: {fa!r}"
    assert not [f for f in fb if f.get("type") == "patch"], f"room b was woken: {fb!r}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_presence_key_broader_than_push_scope_still_reaches_every_room(
    _fresh_presence,
):
    """Scoping by the presence key, not by push_scope: a site-wide presence
    with per-room push_scope must refresh every room's count."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        fa, fb = await _join_wakes(
            "_SitePresenceView",
            same_room_query="room=a",
            other_room_query="room=b",
            joiner_query="room=c",
        )
    assert "a:3:" in _text(fa), fa
    assert "b:3:" in _text(fb), fb


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_leave_wakes_the_room_and_disconnect_leaves_the_group(_fresh_presence):
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    group = presence_scope_group_name(f"{MOD}._ScopedRoomView", "room3095:leave")
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        a = await _connect("_ScopedRoomView", "room=leave")
        b = await _connect("_ScopedRoomView", "room=leave")
        try:
            await _settle(a, b)
            assert len(layer.groups.get(group, {})) == 2
            await b.disconnect()
            (fa,) = await _settle(a)
            assert "leave:1:" in _text(fa), f"the leave did not refresh the room: {fa!r}"
            assert len(layer.groups.get(group, {})) == 1
        finally:
            await a.disconnect()
        await asyncio.sleep(0.05)
    assert not layer.groups.get(group)


# ---------------------------------------------------------------------------
# Group sync with a fake consumer
# ---------------------------------------------------------------------------


class _Layer:
    def __init__(self):
        self.groups: dict = {}

    async def group_add(self, group, channel):
        self.groups.setdefault(group, set()).add(channel)

    async def group_discard(self, group, channel):
        self.groups.get(group, set()).discard(channel)


class _Consumer:
    def __init__(self, view_path):
        self.channel_layer = _Layer()
        self.channel_name = "chan-1"
        self._view_path = view_path


class _KeyView:
    push_scope = None
    presence_broadcast_scoped = None

    def __init__(self, key="k1", fail=False):
        self.key = key
        self.fail = fail
        self.calls = 0

    def get_presence_key(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no key")
        return self.key


VIEW_PATH = f"{MOD}._KeyView"


def _members(consumer, key):
    return consumer.channel_layer.groups.get(presence_scope_group_name(VIEW_PATH, key), set())


def test_sync_joins_the_key_group_once_and_follows_a_new_tracked_key():
    from djust.push import leave_push_scope_groups, sync_push_scope_groups

    consumer, view = _Consumer(VIEW_PATH), _KeyView()
    for _ in range(3):  # e.g. three ticks
        asyncio.run(sync_push_scope_groups(consumer, view))
    assert view.calls == 1, "get_presence_key must be computed once, then cached"
    assert _members(consumer, "k1") == {"chan-1"}

    view._presence_scope_key = "k2"  # track_presence under a new key
    asyncio.run(sync_push_scope_groups(consumer, view))
    assert _members(consumer, "k1") == set()
    assert _members(consumer, "k2") == {"chan-1"}

    asyncio.run(leave_push_scope_groups(consumer))
    assert _members(consumer, "k2") == set()
    assert consumer._presence_scope_group is None


def test_opting_out_leaves_and_joins_nothing():
    from djust.push import sync_push_scope_groups

    consumer, view = _Consumer(VIEW_PATH), _KeyView()
    asyncio.run(sync_push_scope_groups(consumer, view))
    assert _members(consumer, "k1") == {"chan-1"}
    view.presence_broadcast_scoped = False
    asyncio.run(sync_push_scope_groups(consumer, view))
    assert _members(consumer, "k1") == set()


def test_a_view_without_presence_joins_nothing():
    from djust.push import sync_push_scope_groups

    class _Plain:
        push_scope = None

    consumer = _Consumer(VIEW_PATH)
    asyncio.run(sync_push_scope_groups(consumer, _Plain()))
    assert consumer.channel_layer.groups == {}
    assert getattr(consumer, "_presence_scope_group", None) is None


def test_a_failing_get_presence_key_warns_once_per_view(caplog):
    from djust.push import sync_push_scope_groups

    consumer, view = _Consumer(VIEW_PATH), _KeyView(fail=True)
    with caplog.at_level("WARNING", logger="djust.push"):
        for _ in range(5):
            asyncio.run(sync_push_scope_groups(consumer, view))
    assert caplog.text.count("get_presence_key() failed") == 1
    assert view.calls == 1
    assert consumer.channel_layer.groups == {}
