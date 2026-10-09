"""Presence-bound clocks through actual LiveView WebSocket consumers."""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, PresenceMixin
from djust.clocks import RoomClock, SharedPoll, Publish, _registry
from djust.decorators import event_handler
from djust.tenants.mixin import TenantMixin
from djust.tenants.resolvers import TenantInfo
from djust.tenants.middleware import get_current_tenant
from djust.testing import ManualClock, DeterministicClockExecutor
from djust.websocket import LiveViewConsumer
from ._ws_frames import wait_until

CONSUMERS = []
TICKS = []
TIME = ManualClock()


def step(tick):
    TICKS.append((tick, getattr(get_current_tenant(), "id", None)))
    return True


CLOCK = RoomClock(
    name="ws-room",
    interval=0.1,
    step=step,
    idle_stop=0.2,
    time_source=TIME,
    executor=DeterministicClockExecutor(),
    publish=Publish(__name__ + ".RoomView", "handle_refresh"),
    trailing=0,
)


class RecordingConsumer(LiveViewConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        CONSUMERS.append(self)


class RoomView(TenantMixin, PresenceMixin, LiveView):
    template = "<div dj-root>{{ room }}:{{ refreshes }}</div>"
    presence_key = "room:{room}"
    room_clock = CLOCK
    presence_broadcast_scoped = True

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "same")
        self.refreshes = 0
        self.track_presence({})

    def handle_refresh(self, key="", **kwargs):
        self.refreshes += 1

    @event_handler()
    def restart_clock(self, **kwargs):
        # Event spine binds origin_channel. The engine must drop that token.
        self.room_clock.ensure(self, "room:" + self.room, presence_key=self.get_presence_key())


FETCHES = []


def fetch(tick):
    FETCHES.append(tick.scope)
    return {"tenant": get_current_tenant().id, "room": tick.key}


POLL = SharedPoll(
    name="ws-poll",
    interval=0.1,
    fetch=fetch,
    idle_stop=0.2,
    time_source=TIME,
    executor=DeterministicClockExecutor(),
    publish=Publish(__name__ + ".PollView", "handle_refresh"),
    trailing=0,
)


class PollView(RoomView):
    room_clock = POLL

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.handle_refresh()

    def handle_refresh(self, key="", **kwargs):
        latest = POLL.latest(POLL.scope(self, "room:" + self.room))
        self.refreshes = latest[0] if latest else 0
        if latest:
            assert latest[1]["tenant"] == self._tenant.id


class PlainView(LiveView):
    template = "<div dj-root>plain</div>"

    def mount(self, request, **kwargs):
        self.value = 0


def session():
    s = SessionStore()
    s.save()
    return s


async def mount(tenant="a", room="same", view_class=RoomView):
    socket = WebsocketCommunicator(RecordingConsumer.as_asgi(), "/ws/")
    socket.scope["headers"] = [(b"host", (tenant + ".example.com").encode())]
    socket.scope.update(
        session=await sync_to_async(session)(),
        user=AnonymousUser(),
        tenant=TenantInfo(tenant) if tenant else None,
    )
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": "/r/?room=" + room}
    )
    frame = await socket.receive_json_from(timeout=3)
    assert frame["type"] == "mount", frame
    return socket, CONSUMERS[-1]


@pytest.fixture(autouse=True)
def reset():
    assert not _registry
    CONSUMERS.clear()
    TICKS.clear()
    TIME.value = 0
    CLOCK._history.clear()
    POLL._history.clear()
    POLL._results.clear()
    FETCHES.clear()
    yield
    assert not _registry


async def close_all(sockets):
    for socket in sockets:
        await socket.disconnect()
    for clock in (CLOCK, POLL):
        for scope in clock.stats():
            clock.stop(scope)
    await TIME.settle()


SETTINGS = dict(
    DEBUG=False,
    DJUST_CONFIG={"TENANT_RESOLVER": "subdomain", "TENANT_MAIN_DOMAIN": "example.com"},
    DJUST_TENANTS=None,
    LIVEVIEW_ALLOWED_MODULES=[__name__],
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("members", [2, 4, 10])
async def test_sessions_share_one_clock_and_stop_after_last_leave(members):
    sockets = []
    with override_settings(**SETTINGS):
        try:
            consumers = []
            for _ in range(members):
                socket, consumer = await mount()
                sockets.append(socket)
                consumers.append(consumer)
            await TIME.settle()
            assert len(_registry) == 1
            scope = consumers[0].view_instance.push_scope
            assert scope == "tenant:a:ws-room:room%3Asame"
            for beat in range(1, 6):
                await TIME.advance(0.1)
                await wait_until(
                    lambda: all(c.view_instance.refreshes == beat for c in consumers),
                    what="all room doorbells",
                )
            assert len(TICKS) == 5
            assert all(tenant == "a" for _, tenant in TICKS)
            for socket in sockets[:-1]:
                await socket.disconnect()
            sockets = sockets[-1:]
            await TIME.advance(0.1)
            assert CLOCK.running(scope)
            await sockets.pop().disconnect()
            await TIME.advance(0.2)
            await TIME.advance(0.2)
            assert not CLOCK.running(scope)
            assert CLOCK.stats()[scope]["stop_reason"] == "idle"
        finally:
            await close_all(sockets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_same_room_two_tenants_and_other_room_never_cross_deliver():
    sockets = []
    with override_settings(**SETTINGS):
        try:
            consumers = []
            for tenant, room in [("a", "same"), ("a", "same"), ("b", "same"), ("b", "other")]:
                socket, consumer = await mount(tenant, room)
                sockets.append(socket)
                consumers.append(consumer)
            await TIME.settle()
            assert len(_registry) == 3
            a_scope = consumers[0].view_instance.push_scope
            CLOCK.pause(consumers[2].view_instance.push_scope)
            CLOCK.pause(consumers[3].view_instance.push_scope)
            await TIME.settle()
            await TIME.advance(0.1)
            await wait_until(
                lambda: (
                    consumers[0].view_instance.refreshes == 1
                    and consumers[1].view_instance.refreshes == 1
                ),
                what="tenant a refresh",
            )
            assert len(TICKS) == 1
            assert TICKS[0][0].scope == a_scope
            assert consumers[2].view_instance.refreshes == 0
            assert consumers[3].view_instance.refreshes == 0
            CLOCK.pause(a_scope)
            CLOCK.resume(consumers[2].view_instance.push_scope)
            await TIME.settle()
            await TIME.advance(0.1)
            await wait_until(
                lambda: consumers[2].view_instance.refreshes == 1, what="tenant b refresh"
            )
            assert [c.view_instance.refreshes for c in consumers] == [1, 1, 1, 0]
            assert [tenant for _, tenant in TICKS] == ["a", "b"]
        finally:
            await close_all(sockets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_busy_session_fifty_beats_one_real_queued_doorbell():
    sockets = []
    with override_settings(**SETTINGS):
        try:
            socket, consumer = await mount()
            sockets.append(socket)
            await TIME.settle()
            await consumer._render_lock.acquire()
            consumer._processing_user_event = True
            for beat in range(50):
                await TIME.advance(0.1)
                await wait_until(lambda: bool(consumer._deferred_pushes), what="queued doorbell")
            assert len(TICKS) == 50
            assert len(consumer._deferred_pushes) == 1
            assert consumer.view_instance.refreshes == 0
            consumer._processing_user_event = False
            consumer._render_lock.release()
            await consumer._push_drain_task
            await wait_until(
                lambda: consumer.view_instance.refreshes == 1, what="one drained refresh"
            )
        finally:
            consumer._processing_user_event = False
            if consumer._render_lock.locked():
                consumer._render_lock.release()
            await close_all(sockets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_event_started_clock_reaches_starter():
    sockets = []
    with override_settings(**SETTINGS):
        try:
            socket, consumer = await mount()
            sockets.append(socket)
            await TIME.settle()
            scope = consumer.view_instance.push_scope
            CLOCK.stop(scope)
            await TIME.settle()
            await socket.send_json_to({"type": "event", "event": "restart_clock", "params": {}})
            await wait_until(lambda: CLOCK.running(scope), what="event restarted clock")
            await TIME.settle()
            await TIME.advance(0.1)
            await wait_until(
                lambda: consumer.view_instance.refreshes == 1, what="starter receives its doorbell"
            )
        finally:
            await close_all(sockets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_restore_starts_without_mount_or_first_user_join():
    sockets = []
    with override_settings(**SETTINGS):
        try:
            socket, consumer = await mount()
            sockets.append(socket)
            await TIME.settle()
            original = consumer.view_instance
            scope = original.push_scope
            CLOCK.stop(scope)
            await TIME.settle()
            restored = RoomView()
            restored._tenant = original._tenant
            restored.room = original.room
            restored._websocket_session_id = "restored"
            restored._presence_tracked = True
            restored._presence_user_id = original._presence_user_id
            restored._presence_meta = {}
            with (
                patch.object(restored, "mount", side_effect=AssertionError("must not mount")),
                patch.object(restored, "handle_presence_join") as join,
            ):
                await sync_to_async(restored._restore_presence)()
                assert CLOCK.running(scope)
                join.assert_not_called()
            await sync_to_async(original.untrack_presence)()
            await TIME.advance(0.2)
            assert CLOCK.running(scope)
            await sync_to_async(restored.untrack_presence)()
        finally:
            await close_all(sockets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_view_without_clocks_creates_no_tasks_pool_or_presence_reads():
    sockets = []
    with (
        override_settings(**SETTINGS),
        patch("djust.clocks._worker_pool", side_effect=AssertionError("no pool")),
        patch("djust.clocks.RoomClock.aensure", side_effect=AssertionError("no ensure")),
        patch(
            "djust.presence.PresenceManager.presence_count", side_effect=AssertionError("no count")
        ),
    ):
        try:
            before = set(_registry)
            socket, consumer = await mount(view_class=PlainView)
            sockets.append(socket)
            assert set(_registry) == before
            assert consumer._tick_task is None
        finally:
            await close_all(sockets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_shared_poll_viewers_fetch_once_and_late_join_reads_latest_snapshot():
    sockets = []
    with override_settings(**SETTINGS):
        try:
            consumers = []
            for _ in range(4):
                socket, consumer = await mount(view_class=PollView)
                sockets.append(socket)
                consumers.append(consumer)
            await TIME.settle()
            await TIME.advance(0.1)
            await wait_until(
                lambda: all(c.view_instance.refreshes == 1 for c in consumers),
                what="shared poll version",
            )
            assert len(FETCHES) == 1
            late, late_consumer = await mount(view_class=PollView)
            sockets.append(late)
            assert late_consumer.view_instance.refreshes == 1
            other, other_consumer = await mount("b", view_class=PollView)
            sockets.append(other)
            assert other_consumer.view_instance.refreshes == 0
            await TIME.settle()
            await TIME.advance(0.1)
            await wait_until(
                lambda: (
                    late_consumer.view_instance.refreshes == 2
                    and other_consumer.view_instance.refreshes == 1
                ),
                what="tenant poll versions",
            )
            assert len(FETCHES) == 3  # two a fetches and one b fetch, not six
        finally:
            await close_all(sockets)


class AsyncJoinView(RoomView):
    def mount(self, request, **kwargs):
        self.room = "async"
        self.refreshes = 0
        self.joins = 0

    @event_handler()
    async def join(self, **kwargs):
        self.track_presence({})

    def handle_presence_join(self, presence):
        self.joins += 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_handler_tracks_and_broadcasts_presence_with_clock():
    sockets = []
    with override_settings(**SETTINGS):
        try:
            peer, peer_consumer = await mount(view_class=AsyncJoinView)
            sockets.append(peer)
            await peer.send_json_to({"type": "event", "event": "join", "params": {}})
            assert (await peer.receive_json_from(timeout=3))["type"] == "patch"
            joining, consumer = await mount(view_class=AsyncJoinView)
            sockets.append(joining)
            await joining.send_json_to({"type": "event", "event": "join", "params": {}})
            frame = await joining.receive_json_from(timeout=3)
            assert frame["type"] == "patch", frame
            await wait_until(
                lambda: getattr(peer_consumer.view_instance, "online_count", 0) == 2,
                what="join broadcast reaches peer",
            )
            instance = consumer.view_instance
            assert instance.joins == 1
            assert instance.online_count == 2
            await TIME.settle()
            assert CLOCK.running(instance._room_clock_scope)
        finally:
            await close_all(sockets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_untrack_after_clock_scope_validation_failure():
    sockets = []
    with override_settings(**SETTINGS):
        try:
            socket, consumer = await mount(view_class=AsyncJoinView)
            sockets.append(socket)
            instance = consumer.view_instance
            instance.room = "x" * 513
            with pytest.raises(ValueError, match="clock key"):
                instance.track_presence({})
            assert instance._presence_tracked
            instance.untrack_presence()
            assert not instance._presence_tracked
        finally:
            await close_all(sockets)
