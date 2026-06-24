"""Direct-runtime tests for ``transport.event_context()`` (#1899, ADR-022 Iter 2 Phase 2.3a).

Phase 2.3a's foundational fold: the runtime borrows the CONSUMER's render-lock +
origin-channel + observability scope for the duration of an event handler+render
turn, instead of owning its own (dead) ``_render_lock``.

Why the runtime can't own the lock: render serialization is consumer-owned
(``LiveViewConsumer._render_lock`` websocket.py:619 + ``_processing_user_event``
:622) and SHARED with the WS-only ``_run_tick`` / ``server_push`` / ``db_notify``
render loops. A runtime-local lock would be a DIFFERENT object and could not
serialize against ticks → the #560 version-interleave bug. So the WS transport
BORROWS the consumer's EXISTING lock via the ``event_context`` async-CM; SSE (no
concurrent tick/push loop) is a no-op.

These tests build a ``WSConsumerTransport`` over a FAKE consumer (no real
Channels stack) and drive ``event_context`` directly, asserting:

* the WS context acquires the consumer's EXISTING lock (same object — not a new
  one), holds it INSIDE, releases it AFTER;
* ``_processing_user_event`` is True inside / False after;
* the #1677 origin-channel contextvar is set to the consumer's ``channel_name``
  inside and reset (to its prior value) after;
* a ``PerformanceTracker`` is current inside and cleared after;
* the SSE context is a no-op (touches no lock, leaves no tracker);
* gate-off (#1468): if the WS context does NOT acquire the lock, the
  "held inside" assertion goes RED — proving it is non-tautological;
* the ``ViewRuntime`` no longer owns a ``_render_lock`` (dead code deleted).
"""

from __future__ import annotations

import asyncio

import pytest

from djust import push as _djust_push
from djust.performance import PerformanceTracker
from djust.runtime import (
    SSESessionTransport,
    Transport,
    ViewRuntime,
    WSConsumerTransport,
)


class _FakeConsumer:
    """Minimal stand-in for ``LiveViewConsumer`` exposing only the attrs
    ``WSConsumerTransport.event_context`` reads (the consumer's EXISTING
    render-lock, the processing flag, ``channel_name``, ``session_id``)."""

    def __init__(self) -> None:
        # The EXISTING per-connection lock, mirroring websocket.py:619.
        self._render_lock = asyncio.Lock()
        self._processing_user_event = False
        self.channel_name = "specific.channel.abc123"
        self.session_id = "sess-1899"


class _FakeView:
    """A bare view object — ``event_context`` only passes it through."""


# --------------------------------------------------------------------------- #
# WS event_context: borrows the consumer's EXISTING lock + sets/resets state
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ws_event_context_borrows_existing_consumer_lock_object():
    """The WS context holds the consumer's OWN lock object — not a fresh one."""
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView()

    borrowed_lock_was_the_consumers = None

    assert not consumer._render_lock.locked()
    async with transport.event_context(view):
        # The lock held inside the context MUST be the consumer's existing object.
        borrowed_lock_was_the_consumers = consumer._render_lock.locked()
    # After exit the borrowed lock is released.
    assert consumer._render_lock.locked() is False

    assert borrowed_lock_was_the_consumers is True, (
        "event_context must ACQUIRE the consumer's EXISTING _render_lock inside the "
        "context (borrow, not create a new one) — the #560 tick-serialization guard."
    )


@pytest.mark.asyncio
async def test_ws_event_context_sets_processing_flag_inside_clears_after():
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView()

    assert consumer._processing_user_event is False
    async with transport.event_context(view):
        assert consumer._processing_user_event is True, (
            "_processing_user_event must be True inside the event context "
            "(ticks yield priority to user events, websocket.py:3394)."
        )
    assert consumer._processing_user_event is False, (
        "_processing_user_event must be reset to False after the context "
        "(finally clause, websocket.py:4312)."
    )


@pytest.mark.asyncio
async def test_ws_event_context_sets_and_resets_origin_channel_token():
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView()

    # Establish a sentinel "before" value so we can prove the token is RESET
    # (not merely cleared to the module default).
    before_token = _djust_push.origin_channel.set("PRE-EXISTING")
    try:
        assert _djust_push.origin_channel.get() == "PRE-EXISTING"
        async with transport.event_context(view):
            assert _djust_push.origin_channel.get() == consumer.channel_name, (
                "origin-channel contextvar must be the consumer's channel_name "
                "inside the context (#1677, websocket.py:3398-3400)."
            )
        assert _djust_push.origin_channel.get() == "PRE-EXISTING", (
            "origin-channel token must be RESET to its prior value after the "
            "context (finally clause, websocket.py:4311)."
        )
    finally:
        _djust_push.origin_channel.reset(before_token)


@pytest.mark.asyncio
async def test_ws_event_context_starts_tracker_inside_clears_after():
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView()

    PerformanceTracker.set_current(None)
    assert PerformanceTracker.get_current() is None
    async with transport.event_context(view):
        assert PerformanceTracker.get_current() is not None, (
            "a PerformanceTracker must be current inside the event context "
            "(websocket.py:3150-3154)."
        )
    assert PerformanceTracker.get_current() is None, (
        "the tracker must be cleared after the context so it does not leak "
        "across event turns on the same worker thread."
    )


@pytest.mark.asyncio
async def test_ws_event_context_releases_lock_on_exception():
    """An exception inside the body still releases the borrowed lock + resets state."""
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView()

    with pytest.raises(ValueError):
        async with transport.event_context(view):
            assert consumer._render_lock.locked() is True
            raise ValueError("boom")

    assert consumer._render_lock.locked() is False, (
        "the borrowed lock MUST be released even when the body raises (finally)."
    )
    assert consumer._processing_user_event is False
    assert PerformanceTracker.get_current() is None


# --------------------------------------------------------------------------- #
# Gate-off (#1468): a non-acquiring WS context makes the "held inside" RED
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gate_off_non_acquiring_context_fails_held_inside_assertion():
    """Prove the 'lock held inside' assertion is non-tautological.

    Simulate the change being GATED OFF — a context manager that does NOT acquire
    the consumer's lock — and assert the held-inside check goes RED against it.
    If this assertion passed against a no-op context, the real test would be a
    tautology (#1468)."""
    import contextlib

    consumer = _FakeConsumer()

    @contextlib.asynccontextmanager
    async def _non_acquiring_context():
        # GATE-OFF: does NOT acquire consumer._render_lock.
        yield

    held_inside = None
    async with _non_acquiring_context():
        held_inside = consumer._render_lock.locked()

    assert held_inside is False, (
        "sanity: the gate-off (non-acquiring) context leaves the lock UNHELD"
    )
    # The real test's load-bearing assertion (`held_inside is True`) would FAIL
    # here — demonstrating it actually depends on the acquire.
    with pytest.raises(AssertionError):
        assert held_inside is True, "this is the assertion the real test makes"


# --------------------------------------------------------------------------- #
# SSE event_context: no-op (touches no lock, leaves no tracker)
# --------------------------------------------------------------------------- #


class _FakeSession:
    def __init__(self) -> None:
        self.session_id = "sse-1899"


@pytest.mark.asyncio
async def test_sse_event_context_is_a_noop():
    session = _FakeSession()
    transport = SSESessionTransport(session)
    view = _FakeView()

    PerformanceTracker.set_current(None)
    before_origin = _djust_push.origin_channel.get()

    # The SSE no-op context must yield cleanly and touch no shared state.
    async with transport.event_context(view):
        # No lock to inspect (SSE owns none); the SSE context must not have set a
        # tracker or mutated the origin channel.
        assert PerformanceTracker.get_current() is None, (
            "SSE event_context must NOT set a PerformanceTracker (no-op)."
        )
        assert _djust_push.origin_channel.get() == before_origin, (
            "SSE event_context must NOT touch the origin-channel contextvar."
        )

    assert PerformanceTracker.get_current() is None
    assert _djust_push.origin_channel.get() == before_origin


# --------------------------------------------------------------------------- #
# Dead-lock deletion: ViewRuntime no longer owns a _render_lock
# --------------------------------------------------------------------------- #


def test_viewruntime_does_not_own_a_render_lock():
    """The dead runtime-local ``_render_lock`` (runtime.py) is deleted.

    The runtime must NOT create its own lock — render serialization is borrowed
    from the consumer via ``event_context`` (a runtime-local lock could not
    serialize against the WS tick loop, the #560 bug)."""

    class _DummyTransport:
        pass

    runtime = ViewRuntime(_DummyTransport())
    assert not hasattr(runtime, "_render_lock"), (
        "ViewRuntime must NOT own a _render_lock — the dead one is deleted "
        "(it was never acquired; the consumer's lock is borrowed via "
        "transport.event_context, #1899)."
    )


def test_event_context_is_on_transport_protocol_and_both_adapters():
    """``event_context`` is part of the Transport protocol surface + both adapters."""
    assert hasattr(Transport, "event_context")
    assert hasattr(WSConsumerTransport, "event_context")
    assert hasattr(SSESessionTransport, "event_context")
    # runtime_checkable Protocol membership still holds for both adapters.
    assert isinstance(WSConsumerTransport(_FakeConsumer()), Transport)
    assert isinstance(SSESessionTransport(_FakeSession()), Transport)
