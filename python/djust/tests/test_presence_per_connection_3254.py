"""Presence is one record per connection, aggregated per user (#3254).

The owner's decision on #3254: a user stays present until their LAST connection
leaves, and closing or navigating one tab must not remove another tab's
presence. Before, a backend held one record per ``(room, user)``: a second tab
of a user collapsed onto the first tab's record, so closing either tab removed
the user, and a same-room navigation (whose old view is torn down before the
replacement mounts) made the user leave and rejoin.

Covered here:

* the backend contract on every built-in backend (memory, Redis, and the two
  tenant-aware ones), Redis through fakeredis as ``test_redis_presence_list_cost_3203``
  does;
* a third-party backend written against the old three-method contract;
* ``PresenceMixin`` through the real WebSocket consumer with two connections
  for one user: the join hook fires once, the leave hook once, closing one tab
  leaves the user present, a dead tab expires on its own, and a same-room
  navigation never shows the user as gone.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust.backends.base import PresenceBackend
from djust.backends.memory import InMemoryPresenceBackend
from djust.backends.registry import reset_presence_backend, set_presence_backend
from djust.presence import PresenceManager, PresenceMixin

fakeredis = pytest.importorskip("fakeredis")

from djust.backends.redis import RedisPresenceBackend  # noqa: E402
from djust.tenants.backends import (  # noqa: E402
    TenantAwareMemoryBackend,
    TenantAwareRedisBackend,
)

ROOM = "room:1"


def _fake_redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


def _make(kind: str) -> PresenceBackend:
    if kind == "memory":
        return InMemoryPresenceBackend(timeout=60)
    if kind == "tenant_memory":
        TenantAwareMemoryBackend.clear_all()
        return TenantAwareMemoryBackend(tenant_id="acme", timeout=60)
    with patch("redis.from_url", return_value=_fake_redis()):
        if kind == "redis":
            return RedisPresenceBackend(redis_url="redis://fake:6379/0", timeout=60)
        return TenantAwareRedisBackend(tenant_id="acme", redis_url="redis://fake:6379/0")


@pytest.fixture(params=["memory", "tenant_memory", "redis", "tenant_redis"])
def backend(request) -> PresenceBackend:
    yield _make(request.param)
    TenantAwareMemoryBackend.clear_all()


@contextmanager
def clock(start: float = 1_000_000.0):
    """One controllable ``time.time`` for every backend module."""
    now = [start]
    with patch("time.time", side_effect=lambda: now[0]):
        yield now


def ids(backend, key=ROOM):
    return [p["id"] for p in backend.list(key)]


# --------------------------------------------------------------------------- #
# The backend contract
# --------------------------------------------------------------------------- #


def test_a_users_second_connection_is_not_a_second_presence(backend):
    _, first_a = backend.join_connection(ROOM, "alice", "tab-a", {"c": "red"})
    _, first_b = backend.join_connection(ROOM, "alice", "tab-b", {"c": "blue"})

    assert (first_a, first_b) == (True, False)
    assert ids(backend) == ["alice"]
    assert backend.count(ROOM) == 1


def test_closing_one_connection_keeps_the_user_until_the_last_leaves(backend):
    backend.join_connection(ROOM, "alice", "tab-a", {})
    backend.join_connection(ROOM, "alice", "tab-b", {})

    assert backend.leave_connection(ROOM, "alice", "tab-a") is None  # still in tab B
    assert ids(backend) == ["alice"]

    gone = backend.leave_connection(ROOM, "alice", "tab-b")
    assert gone is not None and gone["id"] == "alice"
    assert backend.list(ROOM) == []
    assert backend.count(ROOM) == 0


def test_leaving_an_unknown_or_already_left_connection_is_a_noop(backend):
    backend.join_connection(ROOM, "alice", "tab-a", {})
    assert backend.leave_connection(ROOM, "alice", "never-joined") is None
    assert ids(backend) == ["alice"]
    assert backend.leave_connection(ROOM, "alice", "tab-a") is not None
    assert backend.leave_connection(ROOM, "alice", "tab-a") is None


def test_users_are_independent(backend):
    backend.join_connection(ROOM, "alice", "a1", {})
    backend.join_connection(ROOM, "bob", "b1", {})
    backend.join_connection(ROOM, "alice", "a2", {})

    assert sorted(ids(backend)) == ["alice", "bob"]
    assert backend.leave_connection(ROOM, "bob", "b1") is not None
    assert backend.leave_connection(ROOM, "alice", "a1") is None
    assert ids(backend) == ["alice"]


def test_a_rejoin_of_the_same_connection_is_idempotent(backend):
    with clock() as now:
        backend.join_connection(ROOM, "alice", "tab-a", {"v": 1})
        now[0] += 5
        _, first = backend.join_connection(ROOM, "alice", "tab-a", {"v": 2})
        listed = backend.list(ROOM)

        assert first is False
        assert [p["id"] for p in listed] == ["alice"]
        assert listed[0]["meta"] == {"v": 2}
        assert listed[0]["joined_at"] == 1_000_000.0  # the arrival, not the refresh
        assert backend.leave_connection(ROOM, "alice", "tab-a") is not None
        assert backend.list(ROOM) == []


def test_the_record_keeps_its_shape_and_aggregates(backend):
    """``joined_at`` is the earliest connection, ``meta`` the newest one's."""
    with clock() as now:
        backend.join_connection(ROOM, "alice", "tab-a", {"c": "red"})
        now[0] += 10
        backend.join_connection(ROOM, "alice", "tab-b", {"c": "blue"})
        (record,) = backend.list(ROOM)

        assert record["id"] == "alice"
        assert record["joined_at"] == 1_000_000.0
        assert record["meta"] == {"c": "blue"}
        assert set(record) - {"tenant_id"} == {"id", "joined_at", "meta"}


def test_each_connection_has_its_own_timeout(backend):
    """A dead tab expires on its own; the user's other tab keeps them present."""
    with clock() as now:
        backend.join_connection(ROOM, "alice", "tab-a", {})
        backend.join_connection(ROOM, "alice", "tab-b", {})
        now[0] += 40
        backend.heartbeat_connection(ROOM, "alice", "tab-a")  # tab B is the dead one
        now[0] += 30  # tab B: 70 s without a heartbeat, tab A: 30 s

        assert ids(backend) == ["alice"]
        # Tab A closing is now the LAST connection: tab B had already expired.
        assert backend.leave_connection(ROOM, "alice", "tab-a") is not None
        assert backend.list(ROOM) == []


def test_a_user_whose_every_connection_timed_out_is_gone(backend):
    with clock() as now:
        backend.join_connection(ROOM, "alice", "tab-a", {})
        backend.join_connection(ROOM, "alice", "tab-b", {})
        now[0] += 61
        assert backend.list(ROOM) == []
        assert backend.count(ROOM) == 0


def test_a_user_returning_after_every_connection_timed_out_is_a_first(backend):
    with clock() as now:
        backend.join_connection(ROOM, "alice", "tab-a", {})
        now[0] += 61
        _, first = backend.join_connection(ROOM, "alice", "tab-b", {})
        assert first is True


def test_a_heartbeat_after_expiry_does_not_bring_the_connection_back(backend):
    with clock() as now:
        backend.join_connection(ROOM, "alice", "tab-a", {})
        now[0] += 61
        backend.cleanup_stale(ROOM)
        backend.heartbeat_connection(ROOM, "alice", "tab-a")
        assert backend.list(ROOM) == []
        assert backend.count(ROOM) == 0


def test_the_legacy_calls_still_work_and_remove_the_whole_user(backend):
    """No connection id: the user's one legacy connection, as before #3254."""
    record = backend.join(ROOM, "alice", {"c": "red"})
    assert record["id"] == "alice"
    backend.heartbeat(ROOM, "alice")
    assert ids(backend) == ["alice"]

    gone = backend.leave(ROOM, "alice")
    assert gone is not None and gone["id"] == "alice"
    assert backend.list(ROOM) == []
    assert backend.leave(ROOM, "alice") is None


def test_leave_without_a_connection_removes_every_connection_of_the_user(backend):
    backend.join_connection(ROOM, "alice", "tab-a", {})
    backend.join_connection(ROOM, "alice", "tab-b", {})
    backend.join_connection(ROOM, "bob", "tab-c", {})

    gone = backend.leave(ROOM, "alice")

    assert gone is not None and gone["id"] == "alice"
    assert ids(backend) == ["bob"]


def test_a_heartbeat_without_a_connection_refreshes_all_of_the_users(backend):
    with clock() as now:
        backend.join_connection(ROOM, "alice", "tab-a", {})
        backend.join_connection(ROOM, "alice", "tab-b", {})
        now[0] += 50
        backend.heartbeat(ROOM, "alice")
        now[0] += 50
        assert ids(backend) == ["alice"]
        assert backend.leave_connection(ROOM, "alice", "tab-a") is None  # tab B is alive too


def test_the_health_check_still_reports(backend):
    backend.join_connection(ROOM, "alice", "tab-a", {})
    backend.join_connection(ROOM, "alice", "tab-b", {})
    assert backend.health_check()["status"] == "healthy"


def test_rooms_are_independent(backend):
    backend.join_connection("room:a", "alice", "tab-a", {})
    backend.join_connection("room:b", "alice", "tab-b", {})
    assert backend.leave_connection("room:a", "alice", "tab-a") is not None
    assert ids(backend, "room:b") == ["alice"]


def test_a_record_written_before_connections_existed_is_one_legacy_connection():
    """A rolling upgrade: Redis holds records an old node wrote (member = the bare
    user id, no ``connection_id`` field)."""
    import json

    with patch("redis.from_url", return_value=_fake_redis()):
        backend = RedisPresenceBackend(redis_url="redis://fake:6379/0", timeout=60)
    now = time.time()
    backend._client.zadd(backend._zset_key(ROOM), {"alice": now})
    backend._client.hset(
        backend._meta_key(ROOM),
        "alice",
        json.dumps({"id": "alice", "joined_at": now, "meta": {"c": "red"}}),
    )

    assert ids(backend) == ["alice"]
    _, first = backend.join_connection(ROOM, "alice", "tab-a", {})
    assert first is False  # already present through the old node's record
    assert backend.leave_connection(ROOM, "alice", "tab-a") is None
    assert ids(backend) == ["alice"]


def test_concurrent_first_joins_of_one_user_report_exactly_one_first(backend):
    """The ``first`` decision is atomic: with 16 threads joining one user's first
    connections at once, exactly one sees an empty group. A check-then-set fails this."""
    import threading

    barrier = threading.Barrier(16)
    firsts = []

    def join(i):
        barrier.wait()
        firsts.append(backend.join_connection(ROOM, "alice", f"tab-{i}", {})[1])

    threads = [threading.Thread(target=join, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(firsts) == [False] * 15 + [True]
    assert ids(backend) == ["alice"]


def test_concurrent_leaves_report_exactly_one_last(backend):
    import threading

    for i in range(16):
        backend.join_connection(ROOM, "alice", f"tab-{i}", {})
    barrier = threading.Barrier(16)
    lasts = []

    def leave(i):
        barrier.wait()
        lasts.append(backend.leave_connection(ROOM, "alice", f"tab-{i}") is not None)

    threads = [threading.Thread(target=leave, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(lasts) == [False] * 15 + [True]
    assert backend.list(ROOM) == []


def test_two_redis_nodes_share_one_store():
    client = _fake_redis()
    with patch("redis.from_url", return_value=client):
        node1 = RedisPresenceBackend(redis_url="redis://fake:6379/0", timeout=60)
        node2 = RedisPresenceBackend(redis_url="redis://fake:6379/0", timeout=60)
    assert node1.join_connection(ROOM, "alice", "n1", {})[1] is True
    assert node2.join_connection(ROOM, "alice", "n2", {})[1] is False
    assert node2.leave_connection(ROOM, "alice", "n2") is None
    assert node1.leave_connection(ROOM, "alice", "n1") is not None


def test_redis_count_reads_member_names_only(backend):
    if not isinstance(backend, (RedisPresenceBackend, TenantAwareRedisBackend)):
        pytest.skip("Redis only")
    from collections import Counter

    from fakeredis._basefakesocket import BaseFakeSocket

    backend.join_connection(ROOM, "alice", "a1", {})
    backend.join_connection(ROOM, "alice", "a2", {})
    backend.join_connection(ROOM, "bob", "b1", {})
    seen: Counter = Counter()
    original = BaseFakeSocket._process_command

    def record(self, fields):
        if fields:
            name = fields[0]
            seen[(name.decode() if isinstance(name, bytes) else str(name)).upper()] += 1
        return original(self, fields)

    with patch.object(BaseFakeSocket, "_process_command", record):
        assert backend.count(ROOM) == 2
    assert dict(seen) == {"ZRANGEBYSCORE": 1}


def test_a_tenant_memory_heartbeat_does_not_create_an_entry_for_a_missing_connection():
    TenantAwareMemoryBackend.clear_all()
    backend = TenantAwareMemoryBackend(tenant_id="acme", timeout=60)
    backend.heartbeat_connection(ROOM, "alice", "never-joined")
    backend.heartbeat(ROOM, "alice")
    backend.join_connection(ROOM, "alice", "tab-a", {})
    backend.leave_connection(ROOM, "alice", "tab-a")
    backend.heartbeat_connection(ROOM, "alice", "tab-a")
    assert TenantAwareMemoryBackend._heartbeats["acme"] == {}
    TenantAwareMemoryBackend.clear_all()


# --------------------------------------------------------------------------- #
# PresenceManager and a third-party backend on the old contract
# --------------------------------------------------------------------------- #


class OldContractBackend(PresenceBackend):
    """Written against the three-method contract: one record per user."""

    def __init__(self):
        self.records = {}
        self.calls = []

    def join(self, presence_key, user_id, meta):
        self.calls.append(("join", presence_key, user_id))
        record = {"id": user_id, "joined_at": time.time(), "meta": meta}
        self.records[(presence_key, user_id)] = record
        return record

    def leave(self, presence_key, user_id):
        self.calls.append(("leave", presence_key, user_id))
        return self.records.pop((presence_key, user_id), None)

    def list(self, presence_key):
        return [r for (k, _), r in self.records.items() if k == presence_key]

    def count(self, presence_key):
        return len(self.list(presence_key))

    def heartbeat(self, presence_key, user_id):
        self.calls.append(("heartbeat", presence_key, user_id))

    def cleanup_stale(self, presence_key):
        return 0

    def health_check(self):
        return {"status": "healthy"}


@pytest.fixture
def installed():
    """Install a backend for the test; the registry is reset after."""

    def install(backend):
        set_presence_backend(backend)
        return backend

    yield install
    reset_presence_backend()


def test_a_backend_on_the_old_contract_keeps_working_unchanged(installed):
    old = installed(OldContractBackend())

    record, first = PresenceManager.join_connection(ROOM, "alice", "tab-a", {"c": 1})
    assert record["id"] == "alice" and first is True
    PresenceManager.update_heartbeat(ROOM, "alice", "tab-a")
    assert PresenceManager.leave_connection(ROOM, "alice", "tab-a")["id"] == "alice"

    assert old.calls == [
        ("join", ROOM, "alice"),
        ("heartbeat", ROOM, "alice"),
        ("leave", ROOM, "alice"),
    ]
    assert PresenceManager.list_presences(ROOM) == []


def test_the_manager_calls_with_no_connection_id_reach_the_old_methods(installed):
    old = installed(OldContractBackend())
    PresenceManager.join_presence(ROOM, "alice", {})
    PresenceManager.update_heartbeat(ROOM, "alice")
    assert PresenceManager.leave_presence(ROOM, "alice")["id"] == "alice"
    assert [c[0] for c in old.calls] == ["join", "heartbeat", "leave"]


def test_the_mixin_on_an_old_contract_backend_behaves_as_it_always_did(installed):
    """One record per user: a second tab collapses onto the first, a close removes
    the user, and the hooks fire on every track and untrack."""
    installed(OldContractBackend())
    seen = []

    class Room(PresenceMixin, LiveView):
        presence_key = ROOM

        def get_presence_user_id(self):
            return "alice"

        def handle_presence_join(self, presence):
            seen.append("join")

        def handle_presence_leave(self, presence):
            seen.append("leave")

    a, b = Room(), Room()
    for view in (a, b):
        view._websocket_session_id = "ws"
        view.track_presence()
    a.untrack_presence()
    assert PresenceManager.list_presences(ROOM) == []  # the old collapse, unchanged
    b.untrack_presence()  # nothing left to remove: no second leave

    assert seen == ["join", "join", "leave"]


class AuditedMemory(InMemoryPresenceBackend):
    """A subclass of a built-in backend that overrides the old-signature methods."""

    def __init__(self):
        super().__init__(timeout=60)
        self.log = []

    def join(self, key, user, meta):
        self.log.append(("join", user))
        return super().join(key, user, meta)

    def leave(self, key, user):
        self.log.append(("leave", user))
        return super().leave(key, user)

    def heartbeat(self, key, user):
        self.log.append(("heartbeat", user))
        return super().heartbeat(key, user)


def test_a_builtin_subclass_with_old_signature_overrides_is_driven_the_old_way(installed):
    """No TypeError, and the override sees every call: it is a one-record-per-user backend."""
    from djust.backends.base import uses_per_connection

    audited = installed(AuditedMemory())
    assert uses_per_connection(audited) is False
    assert PresenceManager.per_connection() is False

    record, first = PresenceManager.join_connection(ROOM, "alice", "tab-a", {"c": 1})
    assert record["id"] == "alice" and first is True
    PresenceManager.update_heartbeat(ROOM, "alice", "tab-a")
    assert PresenceManager.leave_connection(ROOM, "alice", "tab-a")["id"] == "alice"

    assert audited.log == [("join", "alice"), ("heartbeat", "alice"), ("leave", "alice")]
    assert PresenceManager.list_presences(ROOM) == []


class AuditedNewSignature(InMemoryPresenceBackend):
    def __init__(self):
        super().__init__(timeout=60)
        self.log = []

    def join(self, key, user, meta, connection_id=None):
        self.log.append(("join", user, connection_id))
        return super().join(key, user, meta, connection_id)

    def leave(self, key, user, connection_id=None):
        self.log.append(("leave", user, connection_id))
        return super().leave(key, user, connection_id)


def test_a_builtin_subclass_with_new_signature_overrides_stays_per_connection(installed):
    audited = installed(AuditedNewSignature())
    assert PresenceManager.per_connection() is True

    _, first_a = PresenceManager.join_connection(ROOM, "alice", "tab-a", {})
    _, first_b = PresenceManager.join_connection(ROOM, "alice", "tab-b", {})
    assert (first_a, first_b) == (True, False)
    assert PresenceManager.leave_connection(ROOM, "alice", "tab-a") is None
    assert PresenceManager.leave_connection(ROOM, "alice", "tab-b") is not None
    assert audited.log == [
        ("join", "alice", "tab-a"),
        ("join", "alice", "tab-b"),
        ("leave", "alice", "tab-a"),
        ("leave", "alice", "tab-b"),
    ]


def test_the_builtins_and_a_plain_old_contract_backend_are_classified(installed):
    from djust.backends.base import uses_per_connection

    assert uses_per_connection(InMemoryPresenceBackend()) is True
    assert uses_per_connection(TenantAwareMemoryBackend(tenant_id="acme")) is True
    assert uses_per_connection(OldContractBackend()) is False


# --------------------------------------------------------------------------- #
# PresenceMixin
# --------------------------------------------------------------------------- #


class Room(PresenceMixin, LiveView):
    exposure_policy = "legacy"
    presence_key = "mixin3254"
    JOINED: list = []
    LEFT: list = []

    def get_presence_user_id(self):
        return "alice"

    def handle_presence_join(self, presence):
        type(self).JOINED.append(presence["id"])

    def handle_presence_leave(self, presence):
        type(self).LEFT.append(presence["id"])


@pytest.fixture
def mixin_backend(installed):
    Room.JOINED.clear()
    Room.LEFT.clear()
    return installed(InMemoryPresenceBackend(timeout=60))


def _tab(cls=Room, ws="ws"):
    view = cls()
    view._websocket_session_id = ws
    return view


def test_two_tabs_one_join_one_leave(mixin_backend):
    a, b = _tab(ws="ws-a"), _tab(ws="ws-b")
    a.track_presence({"n": "A"})
    b.track_presence({"n": "B"})

    assert Room.JOINED == ["alice"]  # tab B joined silently: alice was already here
    assert a._presence_connection_id != b._presence_connection_id
    assert a.online_count == 1 and b.online_count == 1
    assert a.presence_count() == 1

    a.untrack_presence()
    assert Room.LEFT == []
    assert b.list_presences()[0]["id"] == "alice"
    assert a.online_count == 1  # tab A's last count still includes the user

    b.untrack_presence()
    assert Room.LEFT == ["alice"]
    assert b.list_presences() == []


def test_track_is_idempotent_per_view(mixin_backend):
    view = _tab()
    view.track_presence()
    connection = view._presence_connection_id
    view.track_presence()
    assert view._presence_connection_id == connection
    assert Room.JOINED == ["alice"]
    assert len(mixin_backend._groups[Room.presence_key]) == 1


def test_untrack_removes_only_this_views_connection(mixin_backend):
    a, b = _tab(ws="ws-a"), _tab(ws="ws-b")
    a.track_presence()
    b.track_presence()
    members = set(mixin_backend._groups[Room.presence_key])
    a.untrack_presence()
    assert len(set(mixin_backend._groups[Room.presence_key]) & members) == 1
    assert b._presence_connection_id in next(iter(mixin_backend._groups[Room.presence_key]))


def test_a_restored_view_joins_under_a_fresh_connection(mixin_backend):
    """The connection id in saved state belongs to the connection it was saved
    from; another tab of the user may still hold it."""
    live = _tab(ws="ws-live")
    live.track_presence()

    restored = _tab(ws="ws-restored")
    restored._presence_tracked = True
    restored._presence_user_id = "alice"
    restored._presence_meta = {}
    restored._presence_connection_id = live._presence_connection_id  # from saved state
    restored._restore_presence()

    assert restored._presence_connection_id != live._presence_connection_id
    assert len(mixin_backend._groups[Room.presence_key]) == 2
    restored.untrack_presence()
    assert [p["id"] for p in live.list_presences()] == ["alice"]  # live tab untouched
    assert Room.LEFT == []


def test_a_view_with_no_connection_does_not_remove_anyone(mixin_backend):
    """State saved by a build without connection ids restores ``_presence_tracked``
    but joined nothing: untracking it must not remove the user's real tab."""
    live = _tab(ws="ws-live")
    live.track_presence()
    ghost = _tab(ws="ws-ghost")
    ghost._presence_tracked = True
    ghost._presence_user_id = "alice"

    ghost.untrack_presence()

    assert [p["id"] for p in live.list_presences()] == ["alice"]
    assert Room.LEFT == []


def test_the_heartbeat_refreshes_this_views_connection(mixin_backend):
    a, b = _tab(ws="ws-a"), _tab(ws="ws-b")
    a.track_presence()
    b.track_presence()
    with clock(time.time()) as now:
        for member in mixin_backend._groups[Room.presence_key]:
            mixin_backend._heartbeats[(Room.presence_key, member)] = now[0] - 59
        a.update_presence_heartbeat()
        beats = {
            m: now[0] - ts
            for (k, m), ts in mixin_backend._heartbeats.items()
            if k == Room.presence_key
        }
    fresh = [m for m, age in beats.items() if age < 5]
    assert len(fresh) == 1 and a._presence_connection_id in fresh[0]


def test_a_cursor_leaves_with_the_users_last_connection(mixin_backend):
    from djust.presence import CursorTracker, LiveCursorMixin

    class CursorRoom(LiveCursorMixin, Room):
        pass

    a, b = _tab(CursorRoom, "ws-a"), _tab(CursorRoom, "ws-b")
    a.track_presence()
    b.track_presence()
    CursorTracker.update_cursor(CursorRoom.presence_key, "alice", 1, 2)

    a.untrack_presence()
    assert "alice" in CursorTracker.get_cursors(CursorRoom.presence_key)
    b.untrack_presence()
    assert "alice" not in CursorTracker.get_cursors(CursorRoom.presence_key)


# --------------------------------------------------------------------------- #
# Through the real WebSocket consumer: two connections for one user
# --------------------------------------------------------------------------- #

MOD = __name__
WS_ROOM = "wsroom3254"
OTHER_ROOM = "otherroom3254"
EVENTS: list = []  # ("join" | "leave", room, user)


class SpyBackend(InMemoryPresenceBackend):
    """Records every operation, with who is present right after it."""

    def __init__(self):
        super().__init__(timeout=60)
        self.ops: list = []

    def _record(self, op, key, user, result):
        self.ops.append((op, key, user, result, [p["id"] for p in self.list(key)]))

    def join_connection(self, presence_key, user_id, connection_id, meta):
        record, first = super().join_connection(presence_key, user_id, connection_id, meta)
        self._record("join", presence_key, user_id, first)
        return record, first

    def leave_connection(self, presence_key, user_id, connection_id):
        record = super().leave_connection(presence_key, user_id, connection_id)
        self._record("leave", presence_key, user_id, record is not None)
        return record


class WsRoom(PresenceMixin, LiveView):
    exposure_policy = "legacy"
    presence_key = WS_ROOM
    template = '<div dj-root dj-view="' + MOD + '.WsRoom"><b>{{ online_count }}</b></div>'

    def mount(self, request, **kwargs):
        self.track_presence(meta={"tab": "x"})

    def handle_presence_join(self, presence):
        EVENTS.append(("join", self.presence_key, presence["id"]))

    def handle_presence_leave(self, presence):
        EVENTS.append(("leave", self.presence_key, presence["id"]))


class WsRoomTwo(WsRoom):
    """The same room, another page."""

    template = '<div dj-root dj-view="' + MOD + '.WsRoomTwo"><b>{{ online_count }}</b></div>'


class OtherRoom(WsRoom):
    presence_key = OTHER_ROOM
    template = '<div dj-root dj-view="' + MOD + '.OtherRoom"><b>{{ online_count }}</b></div>'


class Plain(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Plain">plain</div>'


class Broken(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Broken">x</div>'

    def mount(self, request, **kwargs):
        raise RuntimeError("mount failed")


urlpatterns = [
    path("room/", WsRoom.as_view()),
    path("room2/", WsRoomTwo.as_view()),
    path("other/", OtherRoom.as_view()),
    path("plain/", Plain.as_view()),
    path("broken/", Broken.as_view()),
]

CONSUMERS: list = []


@pytest.fixture
def ws_env():
    EVENTS.clear()
    CONSUMERS.clear()
    spy = SpyBackend()
    set_presence_backend(spy)
    with override_settings(ROOT_URLCONF=MOD, LIVEVIEW_ALLOWED_MODULES=["djust", MOD], DEBUG=False):
        yield spy
    reset_presence_backend()


def _fresh_key():
    session = SessionStore()
    session.create()
    return session.session_key


async def _ws_until(communicator, *types, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    frames = []
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "no frame of type %r; got %r" % (types, frames)
        frame = await communicator.receive_json_from(timeout=remaining)
        frames.append(frame)
        if frame.get("type") in types:
            return frames


async def _connect(session_key):
    """A socket for the browser session ``session_key`` (the same key is the same user)."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CONSUMERS.append(self)

    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(session_key)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect ack
    communicator.consumer = CONSUMERS[-1]
    return communicator


async def _mount(communicator, cls, url):
    await communicator.send_json_to({"type": "mount", "view": MOD + "." + cls.__name__, "url": url})
    frames = await _ws_until(communicator, "mount", "error")
    assert frames[-1]["type"] == "mount", frames


async def _redirect(communicator, cls, url):
    await communicator.send_json_to(
        {"type": "live_redirect_mount", "view": MOD + "." + cls.__name__, "url": url}
    )
    frames = await _ws_until(communicator, "mount", "error")
    assert frames[-1]["type"] == "mount", frames


async def _close(communicator):
    try:
        await communicator.disconnect()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
        pass


async def _present(room):
    return [p["id"] for p in await sync_to_async(PresenceManager.list_presences)(room)]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_two_tabs_of_one_user_closing_one_keeps_the_user_present(ws_env):
    key = await sync_to_async(_fresh_key)()
    tab_a = await _connect(key)
    tab_b = await _connect(key)
    try:
        await _mount(tab_a, WsRoom, "/room/")
        await _mount(tab_b, WsRoom, "/room/")
        (user,) = await _present(WS_ROOM)
        assert EVENTS == [("join", WS_ROOM, user)]  # tab B joined silently

        await _close(tab_a)

        assert await _present(WS_ROOM) == [user]
        assert EVENTS == [("join", WS_ROOM, user)]  # no leave: tab B is still open
    finally:
        await _close(tab_b)

    assert await _present(WS_ROOM) == []
    assert EVENTS == [("join", WS_ROOM, user), ("leave", WS_ROOM, user)]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_second_tab_closing_first_also_keeps_the_user(ws_env):
    key = await sync_to_async(_fresh_key)()
    tab_a = await _connect(key)
    tab_b = await _connect(key)
    try:
        await _mount(tab_a, WsRoom, "/room/")
        await _mount(tab_b, WsRoom, "/room/")
        await _close(tab_b)
        assert len(await _present(WS_ROOM)) == 1
        assert [e[0] for e in EVENTS] == ["join"]
    finally:
        await _close(tab_a)
    assert await _present(WS_ROOM) == []
    assert [e[0] for e in EVENTS] == ["join", "leave"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_two_users_are_counted_separately(ws_env):
    alice = await _connect(await sync_to_async(_fresh_key)())
    bob = await _connect(await sync_to_async(_fresh_key)())
    try:
        await _mount(alice, WsRoom, "/room/")
        await _mount(bob, WsRoom, "/room/")
        assert len(await _present(WS_ROOM)) == 2
        await _close(alice)
        assert len(await _present(WS_ROOM)) == 1
        assert [e[0] for e in EVENTS] == ["join", "join", "leave"]
    finally:
        await _close(bob)
    assert await _present(WS_ROOM) == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_same_room_navigation_never_shows_the_user_as_gone(ws_env):
    """#3254 item 2: the old view is torn down before the replacement mounts, so a
    one-tab user used to leave and rejoin; peers saw the count drop and recover."""
    tab = await _connect(await sync_to_async(_fresh_key)())
    try:
        await _mount(tab, WsRoom, "/room/")
        (user,) = await _present(WS_ROOM)
        ws_env.ops.clear()
        EVENTS.clear()

        await _redirect(tab, WsRoomTwo, "/room2/")

        assert await _present(WS_ROOM) == [user]
        # The replacement joined as a second connection BEFORE the old one left,
        # so the user was in the room after every single backend operation.
        assert [(op, result) for op, _, _, result, _ in ws_env.ops] == [
            ("join", False),
            ("leave", False),
        ]
        assert all(present == [user] for *_, present in ws_env.ops)
        assert EVENTS == []  # no leave, no rejoin
    finally:
        await _close(tab)
    assert await _present(WS_ROOM) == []
    assert EVENTS == [("leave", WS_ROOM, user)]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_second_mount_frame_into_the_same_room_is_not_churn_either(ws_env):
    """A lazily hydrated view on a socket that already has one replaces it too."""
    tab = await _connect(await sync_to_async(_fresh_key)())
    try:
        await _mount(tab, WsRoom, "/room/")
        (user,) = await _present(WS_ROOM)
        ws_env.ops.clear()
        EVENTS.clear()

        await _mount(tab, WsRoomTwo, "/room2/")

        assert await _present(WS_ROOM) == [user]
        assert all(present == [user] for *_, present in ws_env.ops)
        assert EVENTS == []
    finally:
        await _close(tab)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_navigating_one_tab_does_not_remove_the_other_tab(ws_env):
    key = await sync_to_async(_fresh_key)()
    tab_a = await _connect(key)
    tab_b = await _connect(key)
    try:
        await _mount(tab_a, WsRoom, "/room/")
        await _mount(tab_b, WsRoom, "/room/")
        (user,) = await _present(WS_ROOM)
        EVENTS.clear()

        await _redirect(tab_a, Plain, "/plain/")  # tab A leaves the room

        assert await _present(WS_ROOM) == [user]  # tab B is still there
        assert EVENTS == []
    finally:
        await _close(tab_a)
        await _close(tab_b)
    assert await _present(WS_ROOM) == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_navigating_to_another_room_leaves_the_first_room_once(ws_env):
    tab = await _connect(await sync_to_async(_fresh_key)())
    try:
        await _mount(tab, WsRoom, "/room/")
        (user,) = await _present(WS_ROOM)
        EVENTS.clear()

        await _redirect(tab, OtherRoom, "/other/")

        assert await _present(WS_ROOM) == []
        assert await _present(OTHER_ROOM) == [user]
        assert EVENTS == [("join", OTHER_ROOM, user), ("leave", WS_ROOM, user)]
    finally:
        await _close(tab)
    assert await _present(OTHER_ROOM) == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_replacement_mount_that_fails_still_releases_the_old_presence(ws_env):
    """The old view's untrack waits for the replacement, but never forever."""
    tab = await _connect(await sync_to_async(_fresh_key)())
    try:
        await _mount(tab, WsRoom, "/room/")
        (user,) = await _present(WS_ROOM)
        EVENTS.clear()

        await tab.send_json_to({"type": "mount", "view": MOD + ".Broken", "url": "/broken/"})
        await _ws_until(tab, "error", "mount")

        await asyncio.sleep(0.05)
        assert await _present(WS_ROOM) == []
        assert EVENTS == [("leave", WS_ROOM, user)]
    finally:
        await _close(tab)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_disconnect_with_a_navigation_still_pending_untracks_everything(ws_env):
    tab = await _connect(await sync_to_async(_fresh_key)())
    await _mount(tab, WsRoom, "/room/")
    consumer = tab.consumer
    old = consumer.view_instance
    # A replacement frame was released but the socket died before it finished.
    consumer._defer_presence_untrack([old])
    await _close(tab)
    assert await _present(WS_ROOM) == []
    assert old._presence_tracked is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_dead_tab_expires_without_removing_the_users_other_tab(ws_env):
    key = await sync_to_async(_fresh_key)()
    tab_a = await _connect(key)
    tab_b = await _connect(key)
    try:
        await _mount(tab_a, WsRoom, "/room/")
        await _mount(tab_b, WsRoom, "/room/")
        view_a, view_b = tab_a.consumer.view_instance, tab_b.consumer.view_instance
        user = view_a._presence_user_id
        member_a = f"{user}\x1f{view_a._presence_connection_id}"
        member_b = f"{user}\x1f{view_b._presence_connection_id}"

        # Tab B stopped heartbeating a minute and a half ago; tab A pings.
        ws_env._heartbeats[(WS_ROOM, member_b)] = time.time() - 90
        ws_env._heartbeats[(WS_ROOM, member_a)] = time.time() - 59
        await tab_a.send_json_to({"type": "ping"})
        await _ws_until(tab_a, "pong")

        assert await _present(WS_ROOM) == [user]
        assert set(ws_env._groups[WS_ROOM]) == {member_a}  # only B's connection went
        assert time.time() - ws_env._heartbeats[(WS_ROOM, member_a)] < 5
    finally:
        await _close(tab_a)
        await _close(tab_b)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_ping_keeps_every_view_of_a_mount_batch_alive(ws_env):
    tab = await _connect(await sync_to_async(_fresh_key)())
    try:
        await tab.send_json_to(
            {
                "type": "mount_batch",
                "views": [
                    {"view": MOD + ".WsRoom", "url": "/room/", "target_id": "a"},
                    {"view": MOD + ".OtherRoom", "url": "/other/", "target_id": "b"},
                ],
            }
        )
        await _ws_until(tab, "mount_batch")
        for room in (WS_ROOM, OTHER_ROOM):
            for member in ws_env._groups[room]:
                ws_env._heartbeats[(room, member)] = time.time() - 59

        await tab.send_json_to({"type": "ping"})
        await _ws_until(tab, "pong")

        for room in (WS_ROOM, OTHER_ROOM):
            ages = [time.time() - ts for (k, _), ts in ws_env._heartbeats.items() if k == room]
            assert ages and all(age < 5 for age in ages), (room, ages)
    finally:
        await _close(tab)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_old_contract_backend_same_room_navigation_ends_with_the_user_present(ws_env):
    """One record per user: the replacement's join and the old view's leave address
    the same record, so the order must stay leave, mount, join as before #3254."""
    old = OldContractBackend()
    set_presence_backend(old)
    tab = await _connect(await sync_to_async(_fresh_key)())
    try:
        await _mount(tab, WsRoom, "/room/")
        (user,) = await _present(WS_ROOM)
        old.calls.clear()

        await _redirect(tab, WsRoomTwo, "/room2/")

        assert await _present(WS_ROOM) == [user]  # on the page, and present
        assert [c[0] for c in old.calls] == ["leave", "join"]
        assert tab.consumer.view_instance._presence_tracked is True
    finally:
        await _close(tab)
    assert await _present(WS_ROOM) == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_cancelled_flush_requeues_the_views_it_did_not_reach(ws_env):
    tab = await _connect(await sync_to_async(_fresh_key)())
    try:
        await _mount(tab, WsRoom, "/room/")
        consumer = tab.consumer
        first, second = object(), object()
        consumer._deferred_presence_untrack = [first, second]

        async def cancelled(views):
            raise asyncio.CancelledError

        consumer._untrack_presence_of = cancelled
        with pytest.raises(asyncio.CancelledError):
            await consumer._flush_deferred_presence_untrack()

        assert consumer._deferred_presence_untrack == [first, second]
    finally:
        del consumer._untrack_presence_of
        consumer._deferred_presence_untrack = []
        await _close(tab)
