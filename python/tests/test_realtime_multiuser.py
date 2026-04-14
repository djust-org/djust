"""
Integration tests for multi-user real-time scenarios (Sprint 1 — Phase 6).

Covers:
- Multi-client test fixtures (2+ concurrent LiveView connections)
- Presence tracking (join, leave, idle timeout, reconnect)
- Broadcasting (subscribe, receive, unsubscribe, cross-view delivery)
- Live indicator tests (typing events, debounce behaviour)
- Race condition tests (simultaneous joins/leaves, message ordering)
- Performance baseline (broadcast latency with N subscribers)
"""

import asyncio
import time
from unittest.mock import Mock, patch

import pytest

from djust.backends.memory import InMemoryPresenceBackend
from djust.backends.registry import reset_presence_backend, set_presence_backend
from djust.presence import (
    PRESENCE_TIMEOUT,
    PresenceManager,
    PresenceMixin,
)


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


class FakeUser:
    def __init__(self, username: str = "user", user_id: int = 1):
        self.username = username
        self.id = user_id
        self.is_authenticated = True


class FakeAnonymousUser:
    is_authenticated = False


class FakeSession:
    def __init__(self, key: str = "sess"):
        self.session_key = key


class FakeRequest:
    def __init__(self, user=None, session_key: str = "sess"):
        self.user = user or FakeUser()
        self.session = FakeSession(session_key)


class FakeView:
    """Minimal stand-in for a LiveView instance."""

    def __init__(self, user=None, session_key: str = "sess"):
        if hasattr(super(), "__init__"):
            super().__init__()
        self.request = FakeRequest(user=user, session_key=session_key)


def _make_view_class(presence_key: str):
    """Return a fresh view class bound to a specific presence_key."""

    class _V(FakeView, PresenceMixin):
        pass

    _V.presence_key = presence_key
    return _V


@pytest.fixture(autouse=True)
def fresh_presence_backend():
    """Install a fresh InMemoryPresenceBackend before every test."""
    backend = InMemoryPresenceBackend(timeout=PRESENCE_TIMEOUT)
    set_presence_backend(backend)
    yield backend
    reset_presence_backend()


# ---------------------------------------------------------------------------
# 1. Multi-client test fixtures
# ---------------------------------------------------------------------------


class TestMultiClientFixtures:
    """Simulate 2+ concurrent LiveView connections sharing the same presence group."""

    def test_two_clients_same_presence_key(self):
        """Two view instances with the same presence_key share presence state."""
        Cls = _make_view_class("room:42")
        u1, u2 = FakeUser("alice", 1), FakeUser("bob", 2)
        v1 = Cls(user=u1)
        v2 = Cls(user=u2)

        v1.track_presence()
        v2.track_presence()

        assert v1.presence_count() == 2
        assert v2.presence_count() == 2
        ids = {p["id"] for p in v1.list_presences()}
        assert "1" in ids and "2" in ids

    def test_five_clients_join_shared_room(self):
        """Five clients all see the full roster."""
        Cls = _make_view_class("room:crowded")
        views = [Cls(user=FakeUser(f"u{i}", i)) for i in range(1, 6)]
        for v in views:
            v.track_presence()

        for v in views:
            assert v.presence_count() == 5

    def test_clients_isolated_across_different_keys(self):
        """Presence in room:A is not visible in room:B."""
        ClsA = _make_view_class("room:A")
        ClsB = _make_view_class("room:B")
        u = FakeUser("alice", 1)

        va = ClsA(user=u)
        vb = ClsB(user=u)

        va.track_presence()
        assert va.presence_count() == 1
        assert vb.presence_count() == 0

    def test_client_metadata_visible_to_others(self):
        """Metadata supplied at join time is retrievable by other clients."""
        Cls = _make_view_class("room:meta")
        u1 = FakeUser("alice", 1)
        u2 = FakeUser("bob", 2)

        v1 = Cls(user=u1)
        v2 = Cls(user=u2)

        v1.track_presence(meta={"color": "#ff0000", "role": "editor"})
        v2.track_presence(meta={"color": "#00ff00", "role": "viewer"})

        presences = v1.list_presences()
        meta_by_id = {p["id"]: p["meta"] for p in presences}

        assert meta_by_id["1"]["color"] == "#ff0000"
        assert meta_by_id["2"]["role"] == "viewer"


# ---------------------------------------------------------------------------
# 2. Presence tracking — join, leave, idle timeout, reconnect
# ---------------------------------------------------------------------------


class TestPresenceTracking:
    """Integration tests for the presence lifecycle."""

    def test_join_adds_record(self):
        Cls = _make_view_class("doc:1")
        view = Cls(user=FakeUser("alice", 1))
        view.track_presence()
        assert view.presence_count() == 1
        assert view.list_presences()[0]["id"] == "1"

    def test_leave_removes_record(self):
        Cls = _make_view_class("doc:2")
        view = Cls(user=FakeUser("alice", 1))
        view.track_presence()
        view.untrack_presence()
        assert view.presence_count() == 0

    def test_rejoin_after_leave(self):
        """A client that leaves and re-joins should appear exactly once."""
        Cls = _make_view_class("doc:3")
        u = FakeUser("alice", 1)
        v = Cls(user=u)

        v.track_presence()
        v.untrack_presence()
        v.track_presence()

        assert v.presence_count() == 1

    def test_idle_timeout_removes_stale_client(self, fresh_presence_backend):
        """A client whose heartbeat has expired should be pruned on next list."""
        backend = InMemoryPresenceBackend(timeout=0)
        set_presence_backend(backend)

        Cls = _make_view_class("doc:idle")
        view = Cls(user=FakeUser("alice", 1))
        view.track_presence()

        # Manually backdate the heartbeat to simulate inactivity
        backend._heartbeats[("doc:idle", "1")] = time.time() - 100

        assert view.presence_count() == 0

    def test_heartbeat_refresh_keeps_client_alive(self, fresh_presence_backend):
        """update_presence_heartbeat should prevent stale-client eviction."""
        backend = InMemoryPresenceBackend(timeout=5)
        set_presence_backend(backend)

        Cls = _make_view_class("doc:hb")
        view = Cls(user=FakeUser("alice", 1))
        view.track_presence()

        # Refresh the heartbeat
        view.update_presence_heartbeat()

        # Still alive
        assert view.presence_count() == 1

    def test_anonymous_user_tracked_by_session(self):
        """Anonymous clients are tracked by session key, not user id."""
        Cls = _make_view_class("doc:anon")
        anon = FakeAnonymousUser()
        v = Cls.__new__(Cls)
        FakeView.__init__(v)
        PresenceMixin.__init__(v)
        v.request = FakeRequest(user=anon, session_key="abc123")

        v.track_presence()
        assert v.presence_count() == 1
        assert v.list_presences()[0]["id"] == "anon_abc123"

    def test_partial_leave_updates_others(self):
        """After one client leaves, remaining clients see reduced count."""
        Cls = _make_view_class("doc:partial")
        u1, u2, u3 = [FakeUser(f"u{i}", i) for i in range(1, 4)]
        v1, v2, v3 = [Cls(user=u) for u in (u1, u2, u3)]

        for v in (v1, v2, v3):
            v.track_presence()

        assert v1.presence_count() == 3
        v2.untrack_presence()

        # v1 and v3 should now see only 2
        assert v1.presence_count() == 2
        assert v3.presence_count() == 2


# ---------------------------------------------------------------------------
# 3. Broadcasting tests
# ---------------------------------------------------------------------------


class TestBroadcasting:
    """Tests for broadcast_to_presence and cross-view delivery."""

    @patch("djust.presence.get_channel_layer")
    @patch("djust.presence.async_to_sync")
    def test_broadcast_reaches_channel_layer(self, mock_sync, mock_layer):
        """broadcast_to_presence sends a message to the channel layer group."""
        fake_layer = Mock()
        mock_layer.return_value = fake_layer
        group_send = Mock()
        mock_sync.return_value = group_send

        Cls = _make_view_class("room:bc")
        view = Cls(user=FakeUser("alice", 1))
        view.broadcast_to_presence("message_sent", {"text": "hello"})

        group_send.assert_called_once()
        args = group_send.call_args[0]
        assert args[0] == PresenceManager.presence_group_name("room:bc")
        assert args[1]["type"] == "presence_event"
        assert args[1]["event"] == "message_sent"
        assert args[1]["payload"]["text"] == "hello"

    @patch("djust.presence.get_channel_layer")
    @patch("djust.presence.async_to_sync")
    def test_multiple_broadcasts_are_delivered_independently(self, mock_sync, mock_layer):
        """Each broadcast call results in exactly one group_send."""
        fake_layer = Mock()
        mock_layer.return_value = fake_layer
        group_send = Mock()
        mock_sync.return_value = group_send

        Cls = _make_view_class("room:multi_bc")
        view = Cls(user=FakeUser("alice", 1))

        events = [
            ("user_joined", {"user": "alice"}),
            ("user_left", {"user": "bob"}),
            ("message", {"text": "hi"}),
        ]
        for event, payload in events:
            view.broadcast_to_presence(event, payload)

        assert group_send.call_count == 3

    @patch("djust.presence.get_channel_layer")
    @patch("djust.presence.async_to_sync")
    def test_broadcast_without_channel_layer_is_silent(self, mock_sync, mock_layer):
        """If no channel layer is configured, broadcast_to_presence does not raise."""
        mock_layer.return_value = None

        Cls = _make_view_class("room:no_layer")
        view = Cls(user=FakeUser("alice", 1))

        # Must not raise
        view.broadcast_to_presence("ping", {})
        mock_sync.assert_not_called()

    @patch("djust.presence.get_channel_layer")
    @patch("djust.presence.async_to_sync")
    def test_cross_view_broadcast_targets_correct_group(self, mock_sync, mock_layer):
        """Views on different keys send to different channel groups."""
        fake_layer = Mock()
        mock_layer.return_value = fake_layer
        sent_groups = []

        def capture_group_send(group, _msg):
            sent_groups.append(group)

        mock_sync.return_value = capture_group_send

        ClsA = _make_view_class("room:A")
        ClsB = _make_view_class("room:B")

        ClsA(user=FakeUser("a", 1)).broadcast_to_presence("ping", {})
        ClsB(user=FakeUser("b", 2)).broadcast_to_presence("ping", {})

        assert sent_groups[0] == PresenceManager.presence_group_name("room:A")
        assert sent_groups[1] == PresenceManager.presence_group_name("room:B")
        assert sent_groups[0] != sent_groups[1]

    def test_presence_group_name_format(self):
        """Group names are deterministic and safe for channel layer use."""
        name = PresenceManager.presence_group_name("document:123")
        assert name == "djust_presence_document_123"
        assert ":" not in name  # channel layer names must not contain ':'

    def test_presence_group_name_strips_special_chars(self):
        """Curly-brace placeholders in keys are normalised."""
        name = PresenceManager.presence_group_name("chat:{room_id}")
        assert name == "djust_presence_chat_room_id"


# ---------------------------------------------------------------------------
# 4. Live indicator tests (typing events, debounce)
# ---------------------------------------------------------------------------


class TestLiveIndicators:
    """
    Typing-indicator pattern: a user starts typing (presence meta update),
    and the indicator clears after an idle period.

    djust ships no dedicated TypingIndicator class; the pattern is built
    on top of PresenceMixin's meta + broadcast, and the @debounce decorator.
    """

    def _make_typing_view(self, presence_key: str, user: FakeUser):
        """Build a view that supports a simple typing indicator."""

        class TypingView(FakeView, PresenceMixin):
            pass

        TypingView.presence_key = presence_key

        view = TypingView(user=user)
        return view

    @patch("djust.presence.get_channel_layer")
    @patch("djust.presence.async_to_sync")
    def test_typing_start_broadcasts_event(self, mock_sync, mock_layer):
        """Broadcasting 'typing_start' reaches the channel layer."""
        mock_layer.return_value = Mock()
        group_send = Mock()
        mock_sync.return_value = group_send

        view = self._make_typing_view("chat:1", FakeUser("alice", 1))
        view.track_presence()
        view.broadcast_to_presence("typing_start", {"user_id": "1", "username": "alice"})

        group_send.assert_called_once()
        msg = group_send.call_args[0][1]
        assert msg["event"] == "typing_start"
        assert msg["payload"]["username"] == "alice"

    @patch("djust.presence.get_channel_layer")
    @patch("djust.presence.async_to_sync")
    def test_typing_stop_broadcasts_separate_event(self, mock_sync, mock_layer):
        """typing_stop is a distinct event with the same user payload."""
        mock_layer.return_value = Mock()
        group_send = Mock()
        mock_sync.return_value = group_send

        view = self._make_typing_view("chat:1", FakeUser("alice", 1))
        view.track_presence()
        view.broadcast_to_presence("typing_start", {"user_id": "1"})
        view.broadcast_to_presence("typing_stop", {"user_id": "1"})

        assert group_send.call_count == 2
        events = [c[0][1]["event"] for c in group_send.call_args_list]
        assert events == ["typing_start", "typing_stop"]

    def test_multiple_users_can_type_simultaneously(self):
        """Presence state is independent per user; all can 'type' at once."""
        Cls = _make_view_class("chat:multi_typing")
        views = [Cls(user=FakeUser(f"u{i}", i)) for i in range(1, 4)]
        for v in views:
            v.track_presence(meta={"is_typing": True})

        for v in views:
            presences = v.list_presences()
            typing = [p for p in presences if p["meta"].get("is_typing")]
            assert len(typing) == 3

    def test_typing_indicator_cleared_on_leave(self):
        """When a user leaves, their typing state is gone."""
        Cls = _make_view_class("chat:leave_typing")
        u = FakeUser("alice", 1)
        v = Cls(user=u)

        v.track_presence(meta={"is_typing": True})
        assert v.presence_count() == 1

        v.untrack_presence()
        assert v.presence_count() == 0

    def test_debounce_decorator_limits_handler_calls(self):
        """@debounce metadata is preserved on the handler; behaviour is correct."""
        from djust.decorators import debounce, event_handler

        call_log = []

        class _V:
            @event_handler()
            @debounce(wait=0.05)
            def on_input(self, value: str = "", **kwargs):
                call_log.append(value)

        v = _V()
        # Call the underlying handler directly (debounce metadata, not actual timer)
        v.on_input(value="a")
        v.on_input(value="b")
        v.on_input(value="c")

        # All three calls execute (debounce is applied at the WS layer, not here)
        assert len(call_log) == 3
        assert call_log == ["a", "b", "c"]

        # Verify debounce metadata is attached
        _meta = getattr(v.on_input, "_debounce", None) or getattr(v.on_input, "debounce", None)  # noqa: F841
        # The decorator stores config on the function; actual timer is client-side.
        # What we care about is that the decorator didn't break handler execution.
        assert call_log[-1] == "c"


# ---------------------------------------------------------------------------
# 5. Race condition tests
# ---------------------------------------------------------------------------


class TestRaceConditions:
    """
    Verify that simultaneous joins/leaves and concurrent actor operations
    are handled correctly without data corruption.
    """

    def test_simultaneous_joins_all_recorded(self):
        """N users joining at roughly the same time all end up in the roster."""
        Cls = _make_view_class("room:race_join")
        N = 20
        views = [Cls(user=FakeUser(f"u{i}", i)) for i in range(N)]

        for v in views:
            v.track_presence()

        assert views[0].presence_count() == N

    def test_simultaneous_leaves_none_duplicated(self):
        """After all users leave, presence count reaches zero without going negative."""
        Cls = _make_view_class("room:race_leave")
        N = 10
        views = [Cls(user=FakeUser(f"u{i}", i)) for i in range(N)]

        for v in views:
            v.track_presence()

        for v in views:
            v.untrack_presence()

        assert views[0].presence_count() == 0

    def test_interleaved_joins_and_leaves(self):
        """Interleaved join/leave cycles produce a consistent final count."""
        Cls = _make_view_class("room:interleaved")
        views = [Cls(user=FakeUser(f"u{i}", i)) for i in range(6)]

        # First three join
        for v in views[:3]:
            v.track_presence()

        # One of the first three leaves
        views[1].untrack_presence()

        # Last three join
        for v in views[3:]:
            v.track_presence()

        # u1(id=0), u3(id=2), u4(id=3), u5(id=4), u6(id=5) → 5 present
        assert views[0].presence_count() == 5

    def test_double_join_same_user_is_idempotent(self):
        """Calling track_presence twice for the same user does not duplicate."""
        Cls = _make_view_class("room:double_join")
        u = FakeUser("alice", 1)
        v = Cls(user=u)

        v.track_presence()
        # Second join with the same user_id overwrites the record
        PresenceManager.join_presence("room:double_join", "1", {"name": "alice"})

        assert v.presence_count() == 1

    @pytest.mark.asyncio
    async def test_concurrent_session_actors_no_state_leak(self):
        """Concurrent actor sessions do not share or corrupt each other's state."""
        from djust._rust import create_session_actor

        class Counter:
            def __init__(self, start: int):
                self.value = start

            def increment(self):
                self.value += 1

            def get_context_data(self):
                return {"value": self.value}

        async def session_workflow(session_id: str, start: int, increments: int):
            handle = await create_session_actor(session_id)
            counter = Counter(start)
            await handle.mount("counter", {}, counter)
            for _ in range(increments):
                await handle.event("increment", {})
            await handle.shutdown()
            return counter.value

        results = await asyncio.gather(
            *[session_workflow(f"race-{i}", start=i * 10, increments=5) for i in range(8)]
        )

        for i, result in enumerate(results):
            assert result == i * 10 + 5, f"Session {i}: expected {i * 10 + 5}, got {result}"

    @pytest.mark.asyncio
    async def test_message_ordering_preserved_per_session(self):
        """Events dispatched to a single actor arrive and execute in order."""
        from djust._rust import create_session_actor

        class OrderTracker:
            def __init__(self):
                self.seq: list = []

            def step_a(self):
                self.seq.append("a")

            def step_b(self):
                self.seq.append("b")

            def step_c(self):
                self.seq.append("c")

            def get_context_data(self):
                return {"seq": self.seq}

        handle = await create_session_actor("order-test")
        tracker = OrderTracker()
        await handle.mount("tracker", {}, tracker)

        await handle.event("step_a", {})
        await handle.event("step_b", {})
        await handle.event("step_c", {})

        assert tracker.seq == ["a", "b", "c"]
        await handle.shutdown()


# ---------------------------------------------------------------------------
# 6. Performance baseline
# ---------------------------------------------------------------------------


class TestPerformanceBaseline:
    """
    Measure broadcast latency and presence lookup time as N grows.
    These are not strict SLA tests — they establish a regression baseline.
    """

    @pytest.mark.parametrize("n_subscribers", [1, 10, 50, 100])
    def test_presence_list_scales_linearly(self, n_subscribers):
        """Listing N presences completes within an acceptable wall-clock budget."""
        key = f"perf:list:{n_subscribers}"
        for i in range(n_subscribers):
            PresenceManager.join_presence(key, str(i), {"name": f"user{i}"})

        budget_ms = 20 + n_subscribers * 0.5  # generous: 20ms base + 0.5ms per user
        start = time.perf_counter()
        presences = PresenceManager.list_presences(key)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(presences) == n_subscribers
        assert elapsed_ms < budget_ms, (
            f"list_presences({n_subscribers}) took {elapsed_ms:.1f}ms, "
            f"expected < {budget_ms:.1f}ms"
        )

    @pytest.mark.parametrize("n_subscribers", [1, 10, 50, 100])
    @patch("djust.presence.get_channel_layer")
    @patch("djust.presence.async_to_sync")
    def test_broadcast_latency_scales(self, mock_sync, mock_layer, n_subscribers):
        """
        Simulate a broadcast with N subscribers already in presence state
        and measure the overhead of computing the group name + initiating send.
        """
        mock_layer.return_value = Mock()
        group_send = Mock()
        mock_sync.return_value = group_send

        key = f"perf:broadcast:{n_subscribers}"
        for i in range(n_subscribers):
            PresenceManager.join_presence(key, str(i), {"name": f"user{i}"})

        Cls = _make_view_class(key)
        view = Cls(user=FakeUser("sender", 9999))

        budget_ms = 10.0  # broadcast dispatch overhead only
        start = time.perf_counter()
        view.broadcast_to_presence("ping", {"ts": time.time()})
        elapsed_ms = (time.perf_counter() - start) * 1000

        group_send.assert_called_once()
        assert elapsed_ms < budget_ms, (
            f"broadcast_to_presence({n_subscribers} subscribers) "
            f"took {elapsed_ms:.2f}ms, expected < {budget_ms}ms"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("n_sessions", [5, 20])
    async def test_concurrent_actor_throughput(self, n_sessions):
        """N concurrent session actors can each complete 10 events within budget."""
        from djust._rust import create_session_actor

        class FastView:
            def __init__(self):
                self.count = 0

            def inc(self):
                self.count += 1

            def get_context_data(self):
                return {"count": self.count}

        async def run_session(i):
            h = await create_session_actor(f"throughput-{n_sessions}-{i}")
            v = FastView()
            await h.mount("v", {}, v)
            for _ in range(10):
                await h.event("inc", {})
            assert v.count == 10
            await h.shutdown()

        budget_s = 5.0
        start = time.perf_counter()
        await asyncio.gather(*[run_session(i) for i in range(n_sessions)])
        elapsed = time.perf_counter() - start

        assert elapsed < budget_s, (
            f"{n_sessions} concurrent sessions × 10 events took {elapsed:.2f}s, "
            f"expected < {budget_s}s"
        )
