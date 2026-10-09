"""Presence-bound clocks through actual LiveView WebSocket consumers."""

import asyncio
import threading
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
        self.clock_running_on_join = CLOCK.running(self._room_clock_scope)
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
            await wait_until(lambda: peer_consumer.view_instance.joins == 1, what="first join")
            assert peer_consumer.view_instance.clock_running_on_join
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
            with patch.object(AsyncJoinView, "handle_presence_join") as joined:
                with pytest.raises(ValueError, match="clock key"):
                    instance.track_presence({})
                joined.assert_called_once()
            assert instance._presence_tracked
            instance.untrack_presence()
            assert not instance._presence_tracked
        finally:
            await close_all(sockets)


class FollowupView(RoomView):
    exposure_policy = "explicit"

    def mount(self, request, **kwargs):
        self.room = "followup"
        self.refreshes = 0
        self.callbacks = []

    @event_handler()
    async def join(self, **kwargs):
        self.track_presence({})

    def handle_presence_join(self, presence):
        self.callbacks.append("join")

    def handle_presence_leave(self, presence):
        self.callbacks.append("leave")


class FollowupSyncView(FollowupView):
    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.track_presence({})


async def join_followup(sockets, mode):
    socket, consumer = await mount(view_class=FollowupSyncView if mode == "sync" else FollowupView)
    sockets.append(socket)
    if mode == "async":
        await socket.send_json_to({"type": "event", "event": "join", "params": {}})
        assert (await socket.receive_json_from(timeout=3))["type"] == "patch"
    return consumer.view_instance


async def retire_followup(clock):
    for scope in clock.stats():
        clock.stop(scope)
        run = clock._lookup(scope)
        if run is not None:
            await asyncio.wait_for(asyncio.wrap_future(run.done), 3)
    await TIME.settle()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("leave", ["none", "joiner", "all"])
async def test_presence_timeout_retries_after_retirement_without_another_join(mode, leave, caplog):
    entered, release = threading.Event(), threading.Event()
    ticks = []

    def hung_step(tick):
        ticks.append(tick)
        if len(ticks) == 1:
            entered.set()
            assert release.wait(5)
        return True

    clock = RoomClock(
        name="presence-retry",
        interval=0.1,
        step=hung_step,
        time_source=TIME,
        idle_stop=100,
        trailing=0,
        publish=Publish(__name__ + ".FollowupView", "handle_refresh"),
    )
    sockets = []
    caplog.set_level("DEBUG", logger="djust.clocks")
    with (
        override_settings(**SETTINGS),
        patch.object(FollowupView, "room_clock", clock),
        patch("djust.clocks._STOP_WAIT_SECONDS", 0.05),
    ):
        try:
            peer = await join_followup(sockets, "async")
            await wait_until(lambda: peer.callbacks == ["join"], what="peer join")
            scope = peer._room_clock_scope
            await TIME.advance(0.1)
            assert await asyncio.to_thread(entered.wait, 3)
            old_id = clock.stats()[scope]["run_id"]
            assert clock.stop(scope)
            joining = await join_followup(sockets, mode)
            await wait_until(lambda: joining.callbacks == ["join"], what="bounded join callback")
            assert clock.stats()[scope]["run_id"] == old_id
            assert len(ticks) == 1
            if leave != "none":
                joining.untrack_presence()
                assert joining.callbacks == ["join", "leave"]
            if leave == "all":
                peer.untrack_presence()
                release.set()
                await retire_followup(clock)
                assert not clock.running(scope)
                assert not clock._presence_retries
                assert not clock._retry_tasks
                return
            release.set()
            await wait_until(
                lambda: clock.running(scope) and clock.stats()[scope]["run_id"] != old_id,
                what="automatic replacement after retirement",
            )
            await TIME.advance(0.1)
            # The released old step publishes its own doorbell first, so wait for
            # the replacement run's step rather than for any refresh.
            await wait_until(lambda: len(ticks) == 2, what="replacement step")
            await wait_until(lambda: peer.refreshes > 1, what="replacement doorbell")
            assert len(ticks) == 2
            assert sum("Presence clock retry scheduled" in r.message for r in caplog.records) == 1
        finally:
            release.set()
            await close_all(sockets)
            await retire_followup(clock)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_presence_raising_factory_still_calls_join_then_leave(mode, caplog):
    def bad_factory(key):
        raise ValueError("private factory payload")

    clock = RoomClock(
        name="presence-raising",
        interval=0.1,
        step=step,
        time_source=TIME,
        publish=Publish(__name__ + ".FollowupView", "handle_refresh", bad_factory),
    )
    sockets = []
    with override_settings(**SETTINGS), patch.object(FollowupView, "room_clock", clock):
        try:
            joining = await join_followup(sockets, mode)
            await wait_until(
                lambda: joining.callbacks == ["join"], what="join despite ensure error"
            )
            assert joining._presence_tracked
            assert joining.online_count == 1
            joining.untrack_presence()
            assert joining.callbacks == ["join", "leave"]
            assert (
                sum(r.name == "djust.presence" and r.levelname == "ERROR" for r in caplog.records)
                == 1
            )
            assert "private factory payload" not in caplog.text
        finally:
            await close_all(sockets)
            await retire_followup(clock)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_untrack_during_ensure_flushes_join_before_leave():
    entered, release = threading.Event(), threading.Event()

    def slow_factory(key):
        entered.set()
        assert release.wait(5)
        return {"key": key}

    clock = RoomClock(
        name="presence-untrack",
        interval=0.1,
        step=step,
        time_source=TIME,
        publish=Publish(__name__ + ".FollowupView", "handle_refresh", slow_factory),
    )
    sockets = []
    with override_settings(**SETTINGS), patch.object(FollowupView, "room_clock", clock):
        try:
            joining = await join_followup(sockets, "async")
            assert await asyncio.to_thread(entered.wait, 3)
            assert joining.callbacks == []
            joining.untrack_presence()
            assert joining.callbacks == ["join", "leave"]
            release.set()
            await retire_followup(clock)
            assert joining.callbacks == ["join", "leave"]
        finally:
            release.set()
            await close_all(sockets)
            await retire_followup(clock)
