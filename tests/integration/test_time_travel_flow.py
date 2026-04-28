"""Integration smoke tests for Time-Travel Debugging (v0.6.1).

Exercise the end-to-end path through the LiveViewConsumer: dispatch a
``time_travel_jump`` frame, verify the server restores state + emits a
``time_travel_state`` ack frame. The event-recording path is exercised
indirectly via ``record_event_start`` / ``record_event_end`` in the
unit suite; here we verify the receiver side wired into
``LiveViewConsumer.receive``/``handle_time_travel_jump``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from django.test import override_settings

from djust import LiveView
from djust.time_travel import EventSnapshot, TimeTravelBuffer
from djust.websocket import LiveViewConsumer


class _TTView(LiveView):
    """Minimal LiveView that opts into time travel."""

    template = "<div>{{ count }}</div>"
    time_travel_enabled = True

    def mount(self, request=None, **kwargs):
        self.count = 0


@pytest.mark.asyncio
async def test_time_travel_jump_restores_state_and_acks():
    """Jumping to a past snapshot restores view state + emits ack."""
    consumer = LiveViewConsumer()
    view = _TTView()
    view.count = 42  # current live state
    consumer.view_instance = view

    # Pre-populate the buffer with one snapshot captured at count=7.
    buf: TimeTravelBuffer = view._time_travel_buffer
    assert buf is not None
    buf.append(
        EventSnapshot(
            event_name="increment",
            params={},
            ref=None,
            ts=0.0,
            state_before={"count": 7},
            state_after={"count": 8},
        )
    )

    sent: list = []
    consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
    consumer.send_error = AsyncMock(
        side_effect=lambda msg, **kw: sent.append({"type": "error", "error": msg})
    )
    consumer._send_update = AsyncMock()

    with override_settings(DEBUG=True):
        await consumer.handle_time_travel_jump(
            {"type": "time_travel_jump", "index": 0, "which": "before"}
        )

    # State restored to the snapshot's state_before.
    assert consumer.view_instance.count == 7

    # _send_update called with the re-rendered patches.
    consumer._send_update.assert_called_once()

    # time_travel_state ack emitted with cursor=0.
    tt_frames = [m for m in sent if m.get("type") == "time_travel_state"]
    assert len(tt_frames) == 1
    assert tt_frames[0]["cursor"] == 0
    assert tt_frames[0]["which"] == "before"
    assert tt_frames[0]["history_len"] == 1


@pytest.mark.asyncio
async def test_time_travel_jump_rejected_in_production():
    """DEBUG=False blocks the jump and emits an error."""
    consumer = LiveViewConsumer()
    view = _TTView()
    consumer.view_instance = view

    sent_errors: list = []
    consumer.send_error = AsyncMock(side_effect=lambda msg, **kw: sent_errors.append(msg))
    consumer.send_json = AsyncMock()
    consumer._send_update = AsyncMock()

    with override_settings(DEBUG=False):
        await consumer.handle_time_travel_jump({"index": 0, "which": "before"})

    assert any("DEBUG=True" in e for e in sent_errors)
    consumer._send_update.assert_not_called()


@pytest.mark.asyncio
async def test_time_travel_jump_rejects_bad_index():
    """Out-of-range index yields an error without restoration."""
    consumer = LiveViewConsumer()
    view = _TTView()
    view.count = 99
    consumer.view_instance = view

    sent_errors: list = []
    consumer.send_error = AsyncMock(side_effect=lambda msg, **kw: sent_errors.append(msg))
    consumer.send_json = AsyncMock()
    consumer._send_update = AsyncMock()

    with override_settings(DEBUG=True):
        await consumer.handle_time_travel_jump({"index": 99, "which": "before"})

    assert any("no snapshot at index" in e for e in sent_errors)
    # State unchanged.
    assert consumer.view_instance.count == 99


# ---------------------------------------------------------------------------
# Time-Travel event push fan-out (v0.6.1 follow-up — Fix #3a)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_push_tt_event_sends_frame_in_debug():
    """_maybe_push_tt_event emits a time_travel_event frame under DEBUG."""
    from djust.time_travel import EventSnapshot

    consumer = LiveViewConsumer()
    view = _TTView()
    consumer.view_instance = view

    snap = EventSnapshot(
        event_name="increment",
        params={},
        ref=1,
        ts=1.0,
        state_before={"count": 0},
        state_after={"count": 1},
    )
    # Put the snapshot into the buffer so history_len > 0.
    view._time_travel_buffer.append(snap)

    sent: list = []
    consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))

    with override_settings(DEBUG=True):
        await consumer._maybe_push_tt_event(view, snap)

    frames = [m for m in sent if m.get("type") == "time_travel_event"]
    assert len(frames) == 1
    assert frames[0]["entry"]["event_name"] == "increment"
    assert frames[0]["history_len"] == 1


@pytest.mark.asyncio
async def test_maybe_push_tt_event_noop_in_production():
    """DEBUG=False suppresses the push (never leaks state to client)."""
    from djust.time_travel import EventSnapshot

    consumer = LiveViewConsumer()
    view = _TTView()
    consumer.view_instance = view
    snap = EventSnapshot(
        event_name="a", params={}, ref=None, ts=0.0, state_before={}, state_after={}
    )
    view._time_travel_buffer.append(snap)

    consumer.send_json = AsyncMock()
    with override_settings(DEBUG=False):
        await consumer._maybe_push_tt_event(view, snap)
    consumer.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_push_tt_event_noop_when_snapshot_none():
    """None snapshot (time-travel disabled) is a silent no-op."""
    consumer = LiveViewConsumer()
    consumer.send_json = AsyncMock()
    with override_settings(DEBUG=True):
        await consumer._maybe_push_tt_event(None, None)
    consumer.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_push_tt_event_truncates_oversized_state():
    """Fix C: state_before/state_after larger than the size cap are
    replaced with a truncation placeholder to prevent WS frame bloat.
    The full state remains server-side for time_travel_jump.
    """
    from djust.time_travel import EventSnapshot

    consumer = LiveViewConsumer()
    view = _TTView()
    consumer.view_instance = view

    # Construct a snapshot whose serialized size comfortably exceeds the
    # 16 KiB cap. Each entry is ~20 bytes JSON; 2000 entries ≈ 40 KiB.
    big = {"items": ["row-%04d" % i for i in range(2000)]}
    snap = EventSnapshot(
        event_name="bulk",
        params={},
        ref=1,
        ts=1.0,
        state_before=big,
        state_after=big,
    )
    view._time_travel_buffer.append(snap)

    sent: list = []
    consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))

    with override_settings(DEBUG=True):
        await consumer._maybe_push_tt_event(view, snap)

    frames = [m for m in sent if m.get("type") == "time_travel_event"]
    assert len(frames) == 1
    entry = frames[0]["entry"]
    assert entry.get("_truncated") is True
    assert entry["state_before"] == {
        "_truncated": True,
        "_size": entry["state_before"]["_size"],
    }
    assert entry["state_before"]["_size"] > 16 * 1024
    assert entry["state_after"]["_truncated"] is True
    # Event-identity fields survive.
    assert entry["event_name"] == "bulk"
    assert entry["ref"] == 1


@pytest.mark.asyncio
async def test_maybe_push_tt_event_does_not_truncate_small_state():
    """Small snapshots pass through unmodified — no _truncated marker."""
    from djust.time_travel import EventSnapshot

    consumer = LiveViewConsumer()
    view = _TTView()
    consumer.view_instance = view

    snap = EventSnapshot(
        event_name="tiny",
        params={},
        ref=1,
        ts=1.0,
        state_before={"count": 0},
        state_after={"count": 1},
    )
    view._time_travel_buffer.append(snap)

    sent: list = []
    consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))

    with override_settings(DEBUG=True):
        await consumer._maybe_push_tt_event(view, snap)

    frames = [m for m in sent if m.get("type") == "time_travel_event"]
    assert len(frames) == 1
    entry = frames[0]["entry"]
    assert "_truncated" not in entry
    assert entry["state_before"] == {"count": 0}
    assert entry["state_after"] == {"count": 1}


# ---------------------------------------------------------------------------
# Permission-denied recording (v0.6.1 follow-up — Fix #6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_denied_view_handler_records_with_error():
    """When security validation rejects a handler, the snapshot still lands
    in the buffer with an error set — matches Fix #6 from Self-Review.
    """
    from unittest.mock import patch

    consumer = LiveViewConsumer()
    view = _TTView()
    view.count = 5
    consumer.view_instance = view

    # Mocks for consumer plumbing the handler path touches.
    consumer.send_error = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer._rate_limiter = None

    # Stub _validate_event_security to return None (permission denied).
    async def _fake_validate(self_arg, name, v, rl):
        await self_arg.send_error("forbidden")
        return None

    import asyncio as _asyncio

    consumer._render_lock = _asyncio.Lock()

    with (
        override_settings(DEBUG=True),
        patch("djust.websocket._validate_event_security", side_effect=_fake_validate),
    ):
        await consumer.handle_event({"event": "increment", "params": {}, "ref": 1})

    # One entry should be in the buffer with the permission_denied error.
    entries = view._time_travel_buffer.history()
    assert len(entries) == 1
    assert entries[0]["error"] == "permission_denied"
    assert entries[0]["event_name"] == "increment"
    # State was not mutated — state_before and state_after match.
    assert entries[0]["state_before"]["count"] == 5
    assert entries[0]["state_after"]["count"] == 5


# ---------------------------------------------------------------------------
# #1151, v0.9.4 — wire-protocol exposure for per-component time-travel +
# forward-replay. Five integration cases against the augmented
# time_travel_state ack frame and the two new handlers.
# ---------------------------------------------------------------------------


class _TwoComponentView(LiveView):
    """LiveView with a `_components` registry for component-jump tests."""

    template = "<div>{{ total }}</div>"
    time_travel_enabled = True

    def mount(self, request=None, **kwargs):
        self.total = 0
        self._components = {
            "comp-a": _CompStub("comp-a"),
            "comp-b": _CompStub("comp-b"),
        }


class _CompStub:
    """Minimal component stub — only what restore_component_snapshot needs."""

    def __init__(self, component_id: str):
        self._component_id = component_id
        self.value = 0


def _make_consumer(view):
    consumer = LiveViewConsumer()
    consumer.view_instance = view
    sent: list = []
    consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
    consumer.send_error = AsyncMock(
        side_effect=lambda msg, **kw: sent.append({"type": "error", "error": msg})
    )
    consumer._send_update = AsyncMock()
    return consumer, sent


@pytest.mark.asyncio
async def test_time_travel_state_carries_branch_id_and_max_events_at_tip():
    """Augmented ack frame: defaults branch_id=main, replay disabled at tip, max_events from buffer."""
    view = _TTView()
    view.count = 5
    consumer, sent = _make_consumer(view)
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={},
            ref=None,
            ts=0.0,
            state_before={"count": 4},
            state_after={"count": 5},
        )
    )

    with override_settings(DEBUG=True):
        # Jump to the tip (cursor=0, which="after" — i.e., last index, post-event).
        await consumer.handle_time_travel_jump(
            {"type": "time_travel_jump", "index": 0, "which": "after"}
        )

    tt_frames = [m for m in sent if m.get("type") == "time_travel_state"]
    assert len(tt_frames) == 1
    frame = tt_frames[0]
    assert frame["cursor"] == 0
    assert frame["which"] == "after"
    assert frame["history_len"] == 1
    assert frame["branch_id"] == "main"
    assert frame["forward_replay_enabled"] is False  # at tip — nothing to replay forward
    assert frame["max_events"] == view._time_travel_buffer.max_events


@pytest.mark.asyncio
async def test_time_travel_state_forward_replay_enabled_when_cursor_not_at_tip():
    """Cursor before the tip ⇒ forward_replay_enabled=True."""
    view = _TTView()
    consumer, sent = _make_consumer(view)
    buf = view._time_travel_buffer
    for i in range(3):
        buf.append(
            EventSnapshot(
                event_name="increment",
                params={"n": i},
                ref=None,
                ts=float(i),
                state_before={"count": i},
                state_after={"count": i + 1},
            )
        )

    with override_settings(DEBUG=True):
        await consumer.handle_time_travel_jump(
            {"type": "time_travel_jump", "index": 0, "which": "before"}
        )

    frame = next(m for m in sent if m.get("type") == "time_travel_state")
    assert frame["cursor"] == 0
    assert frame["forward_replay_enabled"] is True
    assert frame["history_len"] == 3


@pytest.mark.asyncio
async def test_time_travel_event_frame_includes_components_top_level():
    """Per-event push surfaces __components__ at frame top level (#1151)."""
    view = _TwoComponentView()
    view.mount()
    consumer, sent = _make_consumer(view)
    snapshot = EventSnapshot(
        event_name="increment",
        params={},
        ref=None,
        ts=0.0,
        state_before={"total": 0, "__components__": {"comp-a": {"value": 1}}},
        state_after={
            "total": 1,
            "__components__": {"comp-a": {"value": 2}, "comp-b": {"value": 7}},
        },
    )
    view._time_travel_buffer.append(snapshot)

    with override_settings(DEBUG=True):
        await consumer._maybe_push_tt_event(view, snapshot)

    event_frames = [m for m in sent if m.get("type") == "time_travel_event"]
    assert len(event_frames) == 1
    frame = event_frames[0]
    assert frame["branch_id"] == "main"
    # Top-level mirror equals state_after.__components__.
    assert frame["components"] == {"comp-a": {"value": 2}, "comp-b": {"value": 7}}
    # And the entry's nested copy is intact (no destructive surfacing).
    assert frame["entry"]["state_after"]["__components__"]["comp-a"]["value"] == 2


@pytest.mark.asyncio
async def test_handle_time_travel_component_jump_isolates_to_one_component():
    """Component-only jump scrubs comp-a but leaves comp-b alone."""
    view = _TwoComponentView()
    view.mount()
    consumer, sent = _make_consumer(view)
    # Snapshot captured at comp-a=5, comp-b=7.
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={},
            ref=None,
            ts=0.0,
            state_before={
                "total": 0,
                "__components__": {"comp-a": {"value": 5}, "comp-b": {"value": 7}},
            },
            state_after={
                "total": 12,
                "__components__": {"comp-a": {"value": 6}, "comp-b": {"value": 8}},
            },
        )
    )
    # Mutate live state — both components way ahead, parent at 100.
    view.total = 100
    view._components["comp-a"].value = 99
    view._components["comp-b"].value = 88

    with override_settings(DEBUG=True):
        await consumer.handle_time_travel_component_jump(
            {
                "type": "time_travel_component_jump",
                "index": 0,
                "component_id": "comp-a",
                "which": "before",
            }
        )

    # comp-a restored to 5 from snapshot.state_before; comp-b untouched at 88.
    assert view._components["comp-a"].value == 5
    assert view._components["comp-b"].value == 88
    # Parent state untouched — component jump doesn't write to the view.
    assert view.total == 100
    # Ack frame emitted.
    frame = next(m for m in sent if m.get("type") == "time_travel_state")
    assert frame["cursor"] == 0
    assert frame["which"] == "before"


@pytest.mark.asyncio
async def test_handle_forward_replay_returns_new_branch_id():
    """Forward-replay from a non-tip cursor allocates a fresh branch_id."""
    from djust.decorators import event_handler

    class _ReplayView(LiveView):
        template = "<div>{{ count }}</div>"
        time_travel_enabled = True

        def mount(self, request=None, **kwargs):
            self.count = 0

        @event_handler()
        def increment(self, n: int = 1, **kwargs):
            self.count += n

    view = _ReplayView()
    view.mount()
    consumer, sent = _make_consumer(view)
    buf = view._time_travel_buffer
    # Two recorded events: count 0→1, then 1→2.
    buf.append(
        EventSnapshot(
            event_name="increment",
            params={"n": 1},
            ref=None,
            ts=0.0,
            state_before={"count": 0},
            state_after={"count": 1},
        )
    )
    buf.append(
        EventSnapshot(
            event_name="increment",
            params={"n": 1},
            ref=None,
            ts=1.0,
            state_before={"count": 1},
            state_after={"count": 2},
        )
    )
    view.count = 2

    with override_settings(DEBUG=True):
        await consumer.handle_forward_replay(
            {"type": "forward_replay", "from_index": 0, "override_params": {"n": 42}}
        )

    # Branch id allocated; original was "main", new should be "branch-0".
    assert view._time_travel_branch_id == "branch-0"
    # Ack frame carries the new branch_id.
    frame = next(m for m in sent if m.get("type") == "time_travel_state")
    assert frame["branch_id"] == "branch-0"
    # History grew by 1 (the replay was recorded).
    assert frame["history_len"] == 3
    # The new entry has the override params.
    new_entry = buf.history()[-1]
    assert new_entry["params"] == {"n": 42}
    # And the live view reflects the replayed state.
    assert view.count == 0 + 42  # restored to state_before (count=0), then handler added 42


@pytest.mark.asyncio
async def test_handle_forward_replay_branches_on_tip_with_override_params():
    """Replay from the LAST entry with override_params still allocates a
    new branch — overrides diverge from the recorded `state_after` even
    when from_index == tip. Regression for the Stage 11 off-by-one fix.
    """
    from djust.decorators import event_handler

    class _ReplayView(LiveView):
        template = "<div>{{ count }}</div>"
        time_travel_enabled = True

        def mount(self, request=None, **kwargs):
            self.count = 0

        @event_handler()
        def increment(self, n: int = 1, **kwargs):
            self.count += n

    view = _ReplayView()
    view.mount()
    consumer, sent = _make_consumer(view)
    buf = view._time_travel_buffer
    # Single entry — replay target is the very last (tip).
    buf.append(
        EventSnapshot(
            event_name="increment",
            params={"n": 1},
            ref=None,
            ts=0.0,
            state_before={"count": 0},
            state_after={"count": 1},
        )
    )
    view.count = 1

    with override_settings(DEBUG=True):
        await consumer.handle_forward_replay(
            {
                "type": "forward_replay",
                "from_index": 0,  # tip
                "override_params": {"n": 99},  # diverges from recorded n=1
            }
        )

    # Override-params at tip ⇒ branch (not "main").
    assert view._time_travel_branch_id == "branch-0"
    frame = next(m for m in sent if m.get("type") == "time_travel_state")
    assert frame["branch_id"] == "branch-0"


@pytest.mark.asyncio
async def test_handle_forward_replay_does_not_leak_branch_id_on_failure():
    """When replay_event returns None (handler missing / un-decorated),
    branch_id and the counter MUST stay at their pre-call values.
    Regression for the Stage 11 finding: branch_id was being mutated
    BEFORE replay_event was awaited, so a failed replay leaked a stale
    branch_id with no recorded events.
    """

    class _ViewWithoutHandler(LiveView):
        template = "<div>{{ count }}</div>"
        time_travel_enabled = True

        def mount(self, request=None, **kwargs):
            self.count = 0

        # No `increment` handler defined — replay_event will return None.

    view = _ViewWithoutHandler()
    view.mount()
    consumer, sent = _make_consumer(view)
    buf = view._time_travel_buffer
    buf.append(
        EventSnapshot(
            event_name="increment",  # not on the view
            params={"n": 1},
            ref=None,
            ts=0.0,
            state_before={"count": 0},
            state_after={"count": 1},
        )
    )
    pre_branch_id = view._time_travel_branch_id
    pre_counter = view._time_travel_branch_counter

    with override_settings(DEBUG=True):
        await consumer.handle_forward_replay(
            {
                "type": "forward_replay",
                "from_index": 0,
                "override_params": {"n": 99},
            }
        )

    # Branch_id and counter unchanged — no leak.
    assert view._time_travel_branch_id == pre_branch_id
    assert view._time_travel_branch_counter == pre_counter
    # An error frame was emitted, no time_travel_state ack.
    assert any(m.get("type") == "error" for m in sent)
    assert not any(m.get("type") == "time_travel_state" for m in sent)


@pytest.mark.asyncio
async def test_handle_time_travel_component_jump_supports_which_after():
    """Component jump with which="after" restores from state_after."""
    view = _TwoComponentView()
    view.mount()
    consumer, sent = _make_consumer(view)
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="increment",
            params={},
            ref=None,
            ts=0.0,
            state_before={
                "total": 0,
                "__components__": {"comp-a": {"value": 5}, "comp-b": {"value": 7}},
            },
            state_after={
                "total": 12,
                "__components__": {"comp-a": {"value": 6}, "comp-b": {"value": 8}},
            },
        )
    )
    view._components["comp-a"].value = 99

    with override_settings(DEBUG=True):
        await consumer.handle_time_travel_component_jump(
            {
                "type": "time_travel_component_jump",
                "index": 0,
                "component_id": "comp-a",
                "which": "after",
            }
        )

    # Restored from state_after (value=6), not state_before (value=5).
    assert view._components["comp-a"].value == 6
    frame = next(m for m in sent if m.get("type") == "time_travel_state")
    assert frame["which"] == "after"
