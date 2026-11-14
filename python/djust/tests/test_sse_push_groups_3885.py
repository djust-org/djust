"""#3885: ``push_to_view`` groups, push scopes and ``db_notify`` reach SSE sessions.

#3809 slice 1 gave a view its own ``server_push`` and ``tick_interval`` over
SSE; what arrives through the channel layer stayed WebSocket-only, because an
SSE session joined no channel-layer group. These tests drive the real SSE
stream and message views and a real ``WebsocketCommunicator`` side by side:
the same push, scoped push or NOTIFY must change the page on both transports,
and be refused on both when it is not the view's.

Delivery goes through the channel layer (``group_send`` -> the session's own
channel -> its receive loop), never an in-process shortcut, so the same path
works when the publisher is another process (the multinode harness covers that
with Redis). Ordering is asserted by message order, never by a duration: a
push that must NOT arrive is followed by one that must, and the channel layer
delivers a channel's messages in order.
"""

import asyncio
import json
import threading
import uuid

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import path

from djust import LiveView, apush_to_view, event_handler
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.websocket import LiveViewConsumer

from ._ws_frames import receive_until, wait_until
from .test_exposure_sse_resilience_3200 import _fresh_key, _request

pytestmark = pytest.mark.django_db(transaction=True)

MOD = "djust.tests.test_sse_push_groups_3885."


class GroupPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ n }}</b></div>"

    def mount(self, request, **kwargs):
        self.n = 0
        self.log = []

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    def handle_set(self, n=0, **kwargs):
        self.log.append(n)
        self.n = n

    @event_handler()
    def join_room(self, room="", **kwargs):
        self.push_scope = room

    @event_handler()
    def push_to_my_class(self, n=0, **kwargs):
        """A handler that pushes to its own class: the sender skips its own echo (#1677)."""
        from djust import push_to_view

        self.n = n
        push_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": n + 1})


class RoomOnePage(GroupPage):
    push_scope = "room-1"


class LoginPage(GroupPage):
    login_required = True


class NotifyPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><b>{{ n }}</b></div>"
    # Class-level: ``listen()`` needs PostgreSQL, the group join does not.
    _listen_channels = {"orders_3885"}

    def mount(self, request, **kwargs):
        self.n = 0
        self.seen = []

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    def handle_info(self, message):
        self.seen.append(message)
        self.n += 1

    def handle_bump(self, **kwargs):
        self.n += 100


#: Opened by a test to let a blocked ``mount()`` finish.
GATE = threading.Event()
#: The views the blocking pages mounted, newest last.
BLOCKED = []


class BlockingPage(GroupPage):
    """``mount()`` waits for ``GATE``: a mount still running when something arrives."""

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        BLOCKED.append(self)
        GATE.wait(30)


class BlockingTickPage(BlockingPage):
    tick_interval = 10

    def handle_tick(self):
        self.n += 1


class BlockingDeniedPage(BlockingPage):
    """Admitted, then refused by the object-permission check after ``mount()``."""

    def get_object(self):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("not yours")


urlpatterns = [
    path("group/", GroupPage.as_view()),
    path("room/", RoomOnePage.as_view()),
    path("notify/", NotifyPage.as_view()),
    path("login/", LoginPage.as_view()),
    path("blocking/", BlockingPage.as_view()),
    path("blockingtick/", BlockingTickPage.as_view()),
    path("blockingdenied/", BlockingDeniedPage.as_view()),
]


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        ROOT_URLCONF=__name__,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
        DEBUG=False,
        DJUST_TENANTS=None,
    ):
        yield
    GATE.set()
    BLOCKED.clear()
    for session in list(_sse_sessions.values()):
        session.shutdown()
    _sse_sessions.clear()


# ---------------------------------------------------------------------------
# Harnesses: the real SSE views and a real WebsocketCommunicator
# ---------------------------------------------------------------------------


async def _sse(view, url):
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + view, "_djust_url": url}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]
    assert session.view_instance is not None, "the view did not mount"
    while not session.queue.empty():
        session.queue.get_nowait()
    return session, key


async def _sse_event(session, key, event, params=None):
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "event", "event": event, "params": params or {}},
        key,
    )
    response = await DjustSSEMessageView().post(post, session_id=session.session_id)
    assert response.status_code == 200


async def _sse_frames(session, done, timeout=10.0):
    frames = []

    async def pump():
        while not done(frames):
            frame = await session.queue.get()
            if frame is None:
                return
            frames.append(frame)

    await asyncio.wait_for(pump(), timeout)
    return frames


def _shows(n):
    """``done``: a broadcast frame that renders the number ``n``."""
    return lambda frames: any(
        f.get("source") == "broadcast" and f'"text": "{n}"' in json.dumps(f) for f in frames
    )


CONSUMERS = []


class RecordingConsumer(LiveViewConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


def _saved_session():
    session = SessionStore()
    session.save()
    return session


async def _ws(view, url):
    socket = WebsocketCommunicator(RecordingConsumer.as_asgi(), "/ws/")
    socket.scope.update(
        session=await sync_to_async(_saved_session)(), user=AnonymousUser(), tenant=None
    )
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": MOD + view, "url": url})
    assert (await socket.receive_json_from())["type"] == "mount"
    return socket, CONSUMERS[-1]


async def _ws_frames(socket, done):
    return await receive_until(socket, done, what="the broadcast frame")


def _member(session):
    """The SSE view's member of the channel layer (its channel and groups)."""
    return session.runtime.transport.channel_member


def _all_members(layer, channel):
    return {group for group, members in layer.groups.items() if channel in members}


# ---------------------------------------------------------------------------
# push_to_view: the view-wide group
# ---------------------------------------------------------------------------


async def test_push_to_view_reaches_an_sse_session():
    session, _key = await _sse("GroupPage", "/group/")
    view = session.view_instance
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 7})
    await _sse_frames(session, _shows(7))
    assert view.log == [7]


async def test_push_to_view_reaches_a_websocket_session():
    """The WebSocket twin of the test above: the parity it is measured against."""
    socket, consumer = await _ws("GroupPage", "/group/")
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 7})
    await _ws_frames(socket, _shows(7))
    assert consumer.view_instance.log == [7]
    await socket.disconnect()


async def test_a_push_through_the_channel_layer_reaches_sse_not_an_in_process_call():
    """Delivery is the channel layer's: a raw ``group_send`` (what a push from
    another process looks like here) changes the SSE page."""
    session, _key = await _sse("GroupPage", "/group/")
    from djust.push import view_group_name

    await get_channel_layer().group_send(
        view_group_name(MOD + "GroupPage"),
        {
            "type": "server_push",
            "state": None,
            "handler": "handle_set",
            "payload": {"n": 3},
            "group": view_group_name(MOD + "GroupPage"),
            "sender_channel": None,
        },
    )
    await _sse_frames(session, _shows(3))
    assert session.view_instance.log == [3]


async def test_a_push_reaches_only_the_view_class_it_names_over_sse():
    group, _ = await _sse("GroupPage", "/group/")
    room, _ = await _sse("RoomOnePage", "/room/")
    await apush_to_view(MOD + "RoomOnePage", handler="handle_set", payload={"n": 1})
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 2})
    await _sse_frames(group, _shows(2))
    await _sse_frames(room, _shows(1))
    assert group.view_instance.log == [2]
    assert room.view_instance.log == [1]


async def test_a_push_to_a_handler_the_websocket_refuses_is_refused_over_sse(caplog):
    session, _key = await _sse("GroupPage", "/group/")
    # A private attribute is not writable through a push, on either transport
    # (``_prepare_server_push``'s ``safe_setattr``).
    await apush_to_view(MOD + "GroupPage", state={"_secret": 1, "n": 6})
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 8})
    await _sse_frames(session, _shows(8))
    assert not hasattr(session.view_instance, "_secret")


async def test_the_sending_sessions_own_echo_is_skipped_over_sse():
    """A handler that pushes to its own class: the other session gets it, the
    sender does not apply its own echo (#1677), as on the WebSocket."""
    sender, key = await _sse("GroupPage", "/group/")
    other, _ = await _sse("GroupPage", "/group/")
    await _sse_event(sender, key, "push_to_my_class", {"n": 10})
    await _sse_frames(other, _shows(11))
    # A later push from outside any handler reaches the sender, in order: had
    # the echo been applied, its 11 would be in the log before this 20.
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 20})
    await _sse_frames(sender, _shows(20))
    assert sender.view_instance.log == [20]
    assert other.view_instance.log == [11, 20]


# ---------------------------------------------------------------------------
# Push scopes
# ---------------------------------------------------------------------------


async def test_a_scoped_push_reaches_only_the_sse_sessions_in_that_scope():
    inside, _ = await _sse("RoomOnePage", "/room/")
    await apush_to_view(MOD + "RoomOnePage", handler="handle_set", payload={"n": 2}, scope="room-2")
    await apush_to_view(MOD + "RoomOnePage", handler="handle_set", payload={"n": 1}, scope="room-1")
    await _sse_frames(inside, _shows(1))
    assert inside.view_instance.log == [1], "the room-2 push must not reach a room-1 session"


async def test_a_scoped_push_reaches_only_the_websocket_sessions_in_that_scope():
    socket, consumer = await _ws("RoomOnePage", "/room/")
    await apush_to_view(MOD + "RoomOnePage", handler="handle_set", payload={"n": 2}, scope="room-2")
    await apush_to_view(MOD + "RoomOnePage", handler="handle_set", payload={"n": 1}, scope="room-1")
    await _ws_frames(socket, _shows(1))
    assert consumer.view_instance.log == [1]
    await socket.disconnect()


async def test_an_sse_view_that_changes_its_push_scope_moves_groups():
    session, key = await _sse("GroupPage", "/group/")
    await _sse_event(session, key, "join_room", {"room": "lobby"})
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 1}, scope="lobby")
    await _sse_frames(session, _shows(1))
    await _sse_event(session, key, "join_room", {"room": "hall"})
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 2}, scope="lobby")
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 3}, scope="hall")
    await _sse_frames(session, _shows(3))
    assert session.view_instance.log == [1, 3], "the lobby push after leaving must not arrive"


async def test_a_push_stamped_for_another_tenant_is_dropped_over_sse(monkeypatch):
    """The tenant check of the WebSocket push (``push_tenant_matches``) applies."""
    from djust import push as push_module

    session, _key = await _sse("GroupPage", "/group/")
    monkeypatch.setattr(push_module, "view_tenant_scope", lambda view: "tenant:a")
    group = push_module.view_group_name(MOD + "GroupPage")
    layer = get_channel_layer()
    for n, stamp in ((4, "tenant:b"), (5, "tenant:a")):
        await layer.group_send(
            group,
            {
                "type": "server_push",
                "state": None,
                "handler": "handle_set",
                "payload": {"n": n},
                "group": group,
                "sender_channel": None,
                "tenant_scope": stamp,
            },
        )
    await _sse_frames(session, _shows(5))
    assert session.view_instance.log == [5]


# ---------------------------------------------------------------------------
# db_notify
# ---------------------------------------------------------------------------


def _notify(channel, payload):
    return get_channel_layer().group_send(
        f"djust_db_notify_{channel}",
        {"type": "db_notify", "channel": channel, "payload": payload},
    )


async def test_db_notify_re_renders_an_sse_session():
    session, _key = await _sse("NotifyPage", "/notify/")
    await _notify("orders_3885", {"id": 1})
    await _sse_frames(session, _shows(1))
    assert session.view_instance.seen == [
        {"type": "db_notify", "channel": "orders_3885", "payload": {"id": 1}}
    ]


async def test_db_notify_re_renders_a_websocket_session():
    socket, consumer = await _ws("NotifyPage", "/notify/")
    await _notify("orders_3885", {"id": 1})
    await _ws_frames(socket, _shows(1))
    assert consumer.view_instance.seen[0]["payload"] == {"id": 1}
    await socket.disconnect()


async def test_db_notify_on_another_channel_does_not_reach_the_sse_view():
    session, _key = await _sse("NotifyPage", "/notify/")
    await _notify("other_3885", {"id": 9})
    await _notify("orders_3885", {"id": 2})
    await _sse_frames(session, _shows(1))
    assert [m["payload"] for m in session.view_instance.seen] == [{"id": 2}]


# ---------------------------------------------------------------------------
# Membership cleanup
# ---------------------------------------------------------------------------


async def test_closing_the_stream_leaves_every_group_and_stops_the_receiver():
    session, key = await _sse("GroupPage", "/group/")
    await _sse_event(session, key, "join_room", {"room": "lobby"})
    member = _member(session)
    channel = member.channel_name
    layer = get_channel_layer()
    joined = _all_members(layer, channel)
    assert len(joined) == 2, joined  # the view-wide group and the lobby scope
    receiver = member._receive_task
    session.shutdown()
    await wait_until(lambda: not _all_members(layer, channel), what="the groups to be left")
    await wait_until(receiver.done, what="the receive loop to stop")


async def test_a_navigation_leaves_the_old_views_groups_and_joins_the_new_ones():
    session, key = await _sse("GroupPage", "/group/")
    old_channel = _member(session).channel_name
    layer = get_channel_layer()
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "live_redirect_mount", "url": "/notify/", "params": {}},
        key,
    )
    assert (
        await DjustSSEMessageView().post(post, session_id=session.session_id)
    ).status_code == 200
    await wait_until(
        lambda: isinstance(session.view_instance, NotifyPage), what="the destination view"
    )
    await wait_until(lambda: not _all_members(layer, old_channel), what="the old groups to go")
    await _notify("orders_3885", {"id": 1})
    await _sse_frames(session, _shows(1))
    assert len(session.view_instance.seen) == 1


async def test_a_refused_mount_joins_no_group():
    from djust.push import view_group_name

    layer = get_channel_layer()
    group = view_group_name(MOD + "LoginPage")
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + "LoginPage", "_djust_url": "/login/"}, key
    )
    await DjustSSEStreamView().get(request, session_id=sid)
    assert not layer.groups.get(group), "an anonymous session was refused: it joins nothing"


async def test_closing_from_a_worker_thread_still_leaves_every_group():
    """``_StreamGuard.abandon`` shuts a session down off the loop; the leaves
    are handed to the loop that joined."""
    session, _key = await _sse("RoomOnePage", "/room/")
    channel = _member(session).channel_name
    layer = get_channel_layer()
    assert _all_members(layer, channel)
    await asyncio.to_thread(session.shutdown)
    await wait_until(lambda: not _all_members(layer, channel), what="the groups to be left")


async def test_a_close_that_races_a_join_leaves_what_the_join_added():
    """The stream closes while a turn is joining a scope group: the join that
    lands after the release is left too, so no membership outlives the view."""
    session, key = await _sse("GroupPage", "/group/")
    member = _member(session)
    layer = get_channel_layer()
    real_group_add = layer.group_add

    async def group_add_then_close(group, channel):
        if channel == member.channel_name and group.startswith("djust_scope_"):
            member.release()
            # A networked layer (Redis) yields inside group_add: the release's
            # leave runs first and cannot see a group not yet recorded.
            for _ in range(5):
                await asyncio.sleep(0)
        await real_group_add(group, channel)

    layer.group_add = group_add_then_close
    try:
        await _sse_event(session, key, "join_room", {"room": "lobby"})
    finally:
        del layer.group_add
    await wait_until(
        lambda: not _all_members(layer, member.channel_name), what="every group to be left"
    )


# ---------------------------------------------------------------------------
# Busy sessions: the WebSocket's rules
# ---------------------------------------------------------------------------


async def test_a_db_notify_that_finds_the_sse_view_busy_is_dropped(monkeypatch):
    """Best-effort, as on the WebSocket (#813): once its bounded wait for the
    lock is over (0 here), it is dropped, not queued behind the turn."""
    from djust import websocket

    monkeypatch.setattr(websocket, "_SERVER_TURN_LOCK_WAIT_S", 0)
    session, _key = await _sse("NotifyPage", "/notify/")
    view = session.view_instance
    await session._render_lock.acquire()
    try:
        # Bounded: a NOTIFY that waited for the lock would never return here.
        ran = await asyncio.wait_for(
            session.runtime.db_notify(view, {"type": "db_notify", "payload": 1}), 10
        )
        assert ran is False
    finally:
        session._render_lock.release()
    await _notify("orders_3885", {"id": 2})
    await _sse_frames(session, _shows(1))
    assert [m["payload"] for m in view.seen] == [{"id": 2}]


async def test_a_push_for_another_tenant_is_not_queued_behind_a_busy_sse_view(monkeypatch):
    """Checked on arrival, as the WebSocket does: it never takes a queue slot."""
    from djust import push as push_module

    session, _key = await _sse("GroupPage", "/group/")
    runtime = session.runtime
    monkeypatch.setattr(push_module, "view_tenant_scope", lambda view: "tenant:a")
    group = push_module.view_group_name(MOD + "GroupPage")
    layer = get_channel_layer()
    await session._render_lock.acquire()
    try:
        for n, stamp in ((4, "tenant:b"), (5, "tenant:a")):
            await layer.group_send(
                group,
                {
                    "type": "server_push",
                    "handler": "handle_set",
                    "payload": {"n": n},
                    "group": group,
                    "tenant_scope": stamp,
                },
            )
        await wait_until(
            lambda: (
                runtime._deferred_pushes
                and any(e.get("payload") == {"n": 5} for _v, e in runtime._deferred_pushes)
            ),
            what="the tenant:a push to be queued",
        )
        assert [e["payload"] for _v, e in runtime._deferred_pushes] == [{"n": 5}]
    finally:
        session._render_lock.release()
    await _sse_frames(session, _shows(5))


async def test_a_queued_push_is_dropped_if_the_view_moved_tenant_before_it_ran(monkeypatch):
    """Checked again when the queue drains, as the WebSocket's drain does."""
    from djust import push as push_module

    session, _key = await _sse("GroupPage", "/group/")
    view, runtime = session.view_instance, session.runtime
    tenant = {"now": "tenant:a"}
    monkeypatch.setattr(push_module, "view_tenant_scope", lambda v: tenant["now"])
    group = push_module.view_group_name(MOD + "GroupPage")
    await session._render_lock.acquire()
    try:
        await get_channel_layer().group_send(
            group,
            {
                "type": "server_push",
                "handler": "handle_set",
                "payload": {"n": 4},
                "group": group,
                "tenant_scope": "tenant:a",
            },
        )
        await wait_until(lambda: runtime._deferred_pushes, what="the push to be queued")
        tenant["now"] = "tenant:b"  # the turn holding the lock moved the view
    finally:
        session._render_lock.release()
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 6}, tenant=None)
    await _sse_frames(session, _shows(6))
    assert view.log == [6]


# ---------------------------------------------------------------------------
# Views mounted beside the page view; authorization
# ---------------------------------------------------------------------------


async def test_a_view_mounted_beside_the_page_view_has_its_own_channel():
    session, key = await _sse("GroupPage", "/group/")
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{session.session_id}/message/",
        {"type": "mount", "view": MOD + "NotifyPage", "target_id": "lazy-1", "url": "/notify/"},
        key,
    )
    assert (
        await DjustSSEMessageView().post(post, session_id=session.session_id)
    ).status_code == 200
    slot = session._slots["lazy-1"]
    slot_channel = slot.runtime.transport.channel_member.channel_name
    assert slot_channel and slot_channel != _member(session).channel_name
    await _notify("orders_3885", {"id": 1})
    frames = await _sse_frames(
        session, lambda fs: any(f.get("target_id") == "lazy-1" and _shows(1)([f]) for f in fs)
    )
    assert slot.view.seen[0]["payload"] == {"id": 1}
    assert session.view_instance.log == []
    assert all(f.get("target_id") == "lazy-1" for f in frames if f.get("source") == "broadcast")
    layer = get_channel_layer()
    async with slot.dispatch_lock, slot.session._render_lock:
        await session._release_slot("lazy-1", reason="view_unmounted")
    await wait_until(lambda: not _all_members(layer, slot_channel), what="the slot's groups to go")
    assert _all_members(layer, _member(session).channel_name), "the page view keeps its groups"


async def test_a_revoked_principal_receives_no_group_push_over_sse():
    """A group push is a server turn: ``reauth_on_event`` refuses it on SSE as
    it does on the WebSocket, and the refused view leaves its groups."""
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
    from django.contrib.auth.models import User

    from djust.config import config

    def login():
        user = User.objects.create_user("g3885%s" % uuid.uuid4().hex[:8], password="x")
        store = SessionStore()
        store[SESSION_KEY] = str(user.pk)
        store[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        store[HASH_SESSION_KEY] = user.get_session_auth_hash()
        store.create()
        return user, store.session_key

    previous = (config.get("reauth_on_event"), config.get("reauth_server_turn_interval"))
    config.set("reauth_on_event", True)
    config.set("reauth_server_turn_interval", 0)
    try:
        user, key = await sync_to_async(login)()
        sid = str(uuid.uuid4())
        request = await sync_to_async(_request)(
            "GET", f"/djust/sse/{sid}/", {"view": MOD + "LoginPage", "_djust_url": "/login/"}, key
        )
        assert (await DjustSSEStreamView().get(request, session_id=sid)).status_code == 200
        session = _sse_sessions[sid]
        view = session.view_instance
        channel = _member(session).channel_name
        await apush_to_view(MOD + "LoginPage", handler="handle_set", payload={"n": 1})
        await _sse_frames(session, _shows(1))

        def revoke():
            SessionStore(key).delete()
            User.objects.filter(pk=user.pk).update(is_active=False)

        await sync_to_async(revoke)()
        await apush_to_view(MOD + "LoginPage", handler="handle_set", payload={"n": 2})
        await _sse_frames(session, lambda fs: any(f.get("code") == "permission_denied" for f in fs))
        assert view.log == [1], "a revoked principal's group push applies nothing"
    finally:
        config.set("reauth_on_event", previous[0])
        config.set("reauth_server_turn_interval", previous[1])
    layer = get_channel_layer()
    await wait_until(lambda: not _all_members(layer, channel), what="the refused view's groups")


async def test_a_view_beside_the_page_skips_its_own_echo_too():
    """The #1677 tag of a POSTed turn of a view mounted beside the page view is
    that view's channel: its push to its own class is not applied to itself."""
    page, key = await _sse("NotifyPage", "/notify/")
    other, _ = await _sse("GroupPage", "/group/")
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{page.session_id}/message/",
        {"type": "mount", "view": MOD + "GroupPage", "target_id": "lazy-1", "url": "/group/"},
        key,
    )
    assert (await DjustSSEMessageView().post(post, session_id=page.session_id)).status_code == 200
    slot = page._slots["lazy-1"]
    post = await sync_to_async(_request)(
        "POST",
        f"/djust/sse/{page.session_id}/message/",
        {
            "type": "event",
            "event": "push_to_my_class",
            "params": {"n": 10},
            "target_id": "lazy-1",
        },
        key,
    )
    assert (await DjustSSEMessageView().post(post, session_id=page.session_id)).status_code == 200
    await _sse_frames(other, _shows(11))
    await apush_to_view(MOD + "GroupPage", handler="handle_set", payload={"n": 20})
    await _sse_frames(
        page, lambda fs: any(f.get("target_id") == "lazy-1" and _shows(20)([f]) for f in fs)
    )
    assert slot.view.log == [20]


# ---------------------------------------------------------------------------
# Review round (#3885): cancelled mounts, races, mount-time pushes, contention
# ---------------------------------------------------------------------------

MEMBERS = []


@pytest.fixture
def members(monkeypatch):
    """Every SSE channel member created during the test, in order."""
    from djust import _sse_channels

    real_init = _sse_channels.SSEChannelMember.__init__

    def init(self, transport):
        real_init(self, transport)
        MEMBERS.append(self)

    MEMBERS.clear()
    monkeypatch.setattr(_sse_channels.SSEChannelMember, "__init__", init)
    yield MEMBERS


async def _start_get(view, url):
    """The stream GET as a task (what Django runs and cancels on a disconnect)."""
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + view, "_djust_url": url}, key
    )
    GATE.clear()
    return asyncio.ensure_future(DjustSSEStreamView().get(request, session_id=sid)), sid


def _group_of(view):
    from djust.push import view_group_name

    return view_group_name(MOD + view)


async def _cancel(task):
    """Cancel the mount (Django on a client disconnect), then let the blocked
    ``mount()`` return: the worker thread is not interruptible, so the task
    ends once it does."""
    task.cancel()
    GATE.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_mount_cancelled_by_a_client_disconnect_releases_the_session(members):
    layer = get_channel_layer()
    task, sid = await _start_get("BlockingPage", "/blocking/")
    await wait_until(lambda: layer.groups.get(_group_of("BlockingPage")), what="the group join")
    member = members[-1]
    session = member._transport._session
    receiver = member._receive_task
    await _cancel(task)
    await wait_until(lambda: not _all_members(layer, member.channel_name), what="groups left")
    await wait_until(receiver.done, what="the read loop to stop")
    assert session.active is False, "the never-registered session is shut down"
    assert sid not in _sse_sessions


async def test_a_cancelled_mount_stops_the_tick_task(members):
    task, _sid = await _start_get("BlockingTickPage", "/blockingtick/")
    layer = get_channel_layer()
    await wait_until(lambda: layer.groups.get(_group_of("BlockingTickPage")), what="admission")
    runtime = members[-1]._transport.runtime
    tick = runtime._tick_task
    assert tick is not None, "on_view_mounted started the tick"
    await _cancel(task)
    await wait_until(tick.done, what="the tick task to end")
    assert BLOCKED[-1].n == 0, "the view never ticked"


async def test_the_runtime_undoes_a_cancelled_mount_on_its_own(members):
    """``_unmount_on_failure`` alone (no SSE view around it): the cancelled
    mount's view leaves the runtime and its server work stops."""
    from djust.sse import SSESession

    key = await sync_to_async(_fresh_key)()
    session = SSESession(str(uuid.uuid4()))
    session._request = await sync_to_async(_request)("GET", "/blocking/", {}, key)
    GATE.clear()
    task = asyncio.ensure_future(
        session.runtime.dispatch_mount(
            {"type": "mount", "view": MOD + "BlockingPage", "url": "/blocking/", "params": {}}
        )
    )
    layer = get_channel_layer()
    await wait_until(lambda: layer.groups.get(_group_of("BlockingPage")), what="admission")
    member = members[-1]
    await _cancel(task)
    assert session.runtime.view_instance is None
    await wait_until(lambda: not _all_members(layer, member.channel_name), what="groups left")


async def _race_join(members, prefix, view, url):
    layer = get_channel_layer()
    real_group_add = layer.group_add

    async def group_add_then_close(group, channel):
        member = members[-1] if members else None
        if member is not None and channel == member.channel_name and group.startswith(prefix):
            member.release()
            # A networked layer yields inside group_add: the release's leave
            # runs before this join has landed.
            for _ in range(5):
                await asyncio.sleep(0)
        await real_group_add(group, channel)

    layer.group_add = group_add_then_close
    try:
        await _sse(view, url)
    finally:
        del layer.group_add
    member = members[-1]
    await wait_until(
        lambda: not _all_members(layer, member.channel_name), what="every group to be left"
    )


async def test_a_release_inside_the_view_group_join_leaves_that_group(members):
    await _race_join(members, "djust_view_", "GroupPage", "/group/")


async def test_a_release_inside_a_notify_group_join_leaves_that_group(members):
    await _race_join(members, "djust_db_notify_", "NotifyPage", "/notify/")


async def test_a_release_while_the_channel_is_created_starts_no_reader(members):
    layer = get_channel_layer()
    real_new_channel = layer.new_channel

    async def new_channel_then_close(*args, **kwargs):
        name = await real_new_channel(*args, **kwargs)
        members[-1].release()
        for _ in range(5):
            await asyncio.sleep(0)
        return name

    layer.new_channel = new_channel_then_close
    try:
        await _sse("GroupPage", "/group/")
    finally:
        del layer.new_channel
    member = members[-1]
    assert member._receive_task is None, "no read loop for a released member"
    assert not _all_members(layer, member.channel_name)


async def test_a_push_published_while_mount_runs_is_delivered_after_the_mount_over_sse():
    layer = get_channel_layer()
    task, sid = await _start_get("BlockingPage", "/blocking/")
    await wait_until(lambda: layer.groups.get(_group_of("BlockingPage")), what="the group join")
    await apush_to_view(MOD + "BlockingPage", handler="handle_set", payload={"n": 5})
    view = BLOCKED[-1]
    await wait_until(
        lambda: view._sse_session.runtime._deferred_pushes, what="the push to be queued"
    )
    assert view.log == [], "not applied before the mount frame"
    GATE.set()
    assert (await task).status_code == 200
    session = _sse_sessions[sid]
    frames = await _sse_frames(session, _shows(5))
    assert [f["type"] for f in frames][0] == "mount", "the mount frame comes first"
    await apush_to_view(MOD + "BlockingPage", handler="handle_set", payload={"n": 6})
    await _sse_frames(session, _shows(6))
    assert view.log == [5, 6]


async def test_a_push_published_while_mount_runs_is_delivered_over_a_websocket():
    """The WebSocket twin of the test above: what SSE is measured against."""
    layer = get_channel_layer()
    GATE.clear()
    socket = WebsocketCommunicator(RecordingConsumer.as_asgi(), "/ws/")
    socket.scope.update(
        session=await sync_to_async(_saved_session)(), user=AnonymousUser(), tenant=None
    )
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": MOD + "BlockingPage", "url": "/blocking/"})
    await wait_until(lambda: layer.groups.get(_group_of("BlockingPage")), what="the group join")
    await apush_to_view(MOD + "BlockingPage", handler="handle_set", payload={"n": 5})
    GATE.set()
    await _ws_frames(socket, _shows(5))
    await apush_to_view(MOD + "BlockingPage", handler="handle_set", payload={"n": 6})
    await _ws_frames(socket, _shows(6))
    assert BLOCKED[-1].log == [5, 6]
    await socket.disconnect()


async def test_a_push_for_a_mount_that_is_then_refused_is_discarded():
    layer = get_channel_layer()
    task, sid = await _start_get("BlockingDeniedPage", "/blockingdenied/")
    group = _group_of("BlockingDeniedPage")
    await wait_until(lambda: layer.groups.get(group), what="admission")
    await apush_to_view(MOD + "BlockingDeniedPage", handler="handle_set", payload={"n": 5})
    view = BLOCKED[-1]
    runtime = view._sse_session.runtime
    await wait_until(lambda: runtime._deferred_pushes, what="the push to be queued")
    GATE.set()
    response = await task
    assert response.status_code == 200
    assert sid not in _sse_sessions, "the refused session is not registered"
    assert view.log == [], "never applied"
    assert not runtime._deferred_pushes, "discarded with the refused view"
    queued = []
    session = view._sse_session
    while not session.queue.empty():
        queued.append(session.queue.get_nowait())
    assert not any(f and f.get("source") == "broadcast" for f in queued), queued
    assert any(f and f.get("code") == "permission_denied" for f in queued), queued
    assert not layer.groups.get(group)


async def test_a_db_notify_waits_for_a_held_lock_and_then_runs(monkeypatch):
    """As the WebSocket's NOTIFY turn: it waits (bounded; long here, so the
    test does not depend on the bound) for a held lock, and pushes queued
    meanwhile do not make it drop."""
    from djust import websocket

    monkeypatch.setattr(websocket, "_SERVER_TURN_LOCK_WAIT_S", 60)
    session, _key = await _sse("NotifyPage", "/notify/")
    view = session.view_instance
    lock = session._render_lock
    await lock.acquire()
    try:
        assert await view.server_push("handle_bump") is True  # queued behind the lock
        await _notify("orders_3885", {"id": 1})
        await wait_until(
            lambda: len([w for w in (lock._waiters or ()) if not w.cancelled()]) >= 2,
            what="the drain and the NOTIFY to wait for the lock",
        )
        assert view.seen == []
    finally:
        lock.release()
    await _sse_frames(session, lambda fs: view.seen and view.n >= 101)
    assert [m["payload"] for m in view.seen] == [{"id": 1}]
