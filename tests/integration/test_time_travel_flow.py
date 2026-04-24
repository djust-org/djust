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
