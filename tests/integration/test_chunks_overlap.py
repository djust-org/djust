"""Integration tests for v0.9.0 PR-C: asyncio.as_completed parallel
lazy render (ADR-015).

PR-B shipped the sequential thunk loop in ``arender_chunks`` Phase 5.
PR-C swaps to ``asyncio.as_completed`` so lazy children render in
parallel. Total wall-clock time = max(thunk_durations) instead of
sum(thunk_durations); chunks emerge in completion order rather than
registration order.

These tests are wall-clock-sensitive but use generous tolerances
(50ms) to stay non-flaky on shared CI workers.
"""

from __future__ import annotations

import asyncio
import gc
import time
import warnings

import pytest

from djust.http_streaming import ChunkEmitter


# ---------------------------------------------------------------------------
# Thunk factory — produces a thunk that sleeps for the requested duration
# then emits a tag chunk identifying itself.
# ---------------------------------------------------------------------------


def _make_sleeping_thunk(view_id: str, sleep_s: float):
    async def _thunk():
        await asyncio.sleep(sleep_s)
        return (
            f'<template id="djl-fill-{view_id}" data-target="{view_id}" '
            f'data-status="ok"><span>{view_id}</span></template>'
            f'<script>window.djust.lazyFill("{view_id}")</script>'
        ).encode("utf-8")

    return _thunk


def _make_timed_thunk(view_id: str, sleep_s: float, records: list):
    """Like :func:`_make_sleeping_thunk`, but records this thunk's
    ``(view_id, start, end)`` ``perf_counter`` interval into ``records``.

    Lets a test prove the thunks' execution intervals OVERLAP (concurrency)
    via event ordering — ``max(start) < min(end)`` — rather than asserting on
    absolute or ratio'd wall-clock durations, which are inherently flaky under
    CPU load (#1795).
    """

    async def _thunk():
        start = time.perf_counter()
        await asyncio.sleep(sleep_s)
        end = time.perf_counter()
        records.append((view_id, start, end))
        return (
            f'<template id="djl-fill-{view_id}" data-target="{view_id}" '
            f'data-status="ok"><span>{view_id}</span></template>'
            f'<script>window.djust.lazyFill("{view_id}")</script>'
        ).encode("utf-8")

    return _thunk


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _ParentStub:
    """Bare-minimum stand-in for a LiveView — supplies arender_chunks via
    inheritance from TemplateMixin."""

    template = "<!DOCTYPE html><html><body><div dj-root></div></body></html>"


@pytest.fixture
def make_parent():
    """Construct a real LiveView so we get arender_chunks via the mixin
    chain."""
    from djust import LiveView

    class _ParallelTestParent(LiveView):
        template = "<!DOCTYPE html><html><body><div dj-root></div></body></html>"

        def mount(self, request, **kwargs):
            pass

    return _ParallelTestParent


class TestParallelRender:
    @pytest.mark.asyncio
    async def test_fills_arrive_in_completion_order_not_registration_order(self, rf, make_parent):
        """Three thunks with sleeps 100ms, 50ms, 25ms registered in that
        order. With sequential render, fills arrive 100ms / 150ms /
        175ms. With parallel render via as_completed, fills arrive in
        completion order: 25ms (slot-c), 50ms (slot-b), 100ms (slot-a).

        We assert the order of fill ids in the chunk stream matches
        completion order, NOT registration order.
        """
        parent = make_parent()
        parent.request = rf.get("/")
        emitter = ChunkEmitter(parent.request)

        emitter.register_thunk("slot-a", _make_sleeping_thunk("slot-a", 0.10))
        emitter.register_thunk("slot-b", _make_sleeping_thunk("slot-b", 0.05))
        emitter.register_thunk("slot-c", _make_sleeping_thunk("slot-c", 0.025))

        async def _drain():
            return [c async for c in emitter]

        consumer = asyncio.create_task(_drain())
        # ``arender_chunks`` runs Phase 1-4 (instant for this minimal
        # template) then Phase 5 (parallel thunks).
        await parent.arender_chunks(parent.template, emitter)
        await emitter.close()
        chunks = await consumer

        body = b"".join(chunks)
        # Find the position of each fill template — order of arrival.
        positions = sorted(
            [
                (body.find(f'id="djl-fill-{vid}"'.encode("utf-8")), vid)
                for vid in ("slot-a", "slot-b", "slot-c")
            ]
        )
        ordered_ids = [vid for _pos, vid in positions]

        # Completion order: c (25ms) → b (50ms) → a (100ms).
        assert ordered_ids == ["slot-c", "slot-b", "slot-a"], (
            f"expected completion order [c,b,a], got {ordered_ids}"
        )

    @pytest.mark.asyncio
    async def test_total_wall_clock_is_max_not_sum(self, rf, make_parent):
        """Parallel render wall-clock ≈ max(thunk durations), NOT the sum.

        Proven DETERMINISTICALLY via interval OVERLAP, not a wall-clock
        threshold. Each of three 50ms thunks records its ``[start, end]``
        interval; a concurrent render starts every thunk before any finishes
        its sleep, so ``max(start) < min(end)`` — the intervals share a common
        instant. A sequential loop can NEVER satisfy this (thunk k+1 starts
        only after thunk k ends), so the assertion still rejects a serial
        implementation (see ``test_overlap_proof_rejects_a_serial_loop``).

        This replaces two earlier timing-threshold formulations that both
        false-failed under CPU load (#1795): the original absolute ceiling
        (``elapsed < 0.10``) and its relative successor
        (``parallel < serial/2``, PR #1797). The latter still drifted past
        0.5 under full ``make test -n auto`` saturation — when the 3 thunks
        can't get dedicated cores, the speedup ratio degrades (observed
        parallel=88.1ms vs threshold=85.8ms at the 1.0.5rc5 cut). Event
        ordering is immune to that jitter: scheduling N coroutines takes
        microseconds regardless of load, always well under the 50ms sleep.
        """
        sleep_s = 0.05
        slot_ids = ("slot-x", "slot-y", "slot-z")
        n = len(slot_ids)
        records: list = []

        # --- Parallel render via arender_chunks / as_completed. ---
        parent = make_parent()
        parent.request = rf.get("/")
        emitter = ChunkEmitter(parent.request)

        for vid in slot_ids:
            emitter.register_thunk(vid, _make_timed_thunk(vid, sleep_s, records))

        async def _drain():
            async for _c in emitter:
                pass

        consumer = asyncio.create_task(_drain())
        await parent.arender_chunks(parent.template, emitter)
        await emitter.close()
        await consumer

        # Deterministic overlap invariant: a concurrent render starts EVERY
        # thunk before ANY finishes its sleep, so the latest start precedes
        # the earliest end — all N intervals share a common instant. A
        # sequential loop can never satisfy this (thunk k+1 starts only after
        # thunk k ends). Event ordering is immune to CPU-saturation jitter:
        # launching N coroutines takes microseconds, far under the 50ms sleep,
        # regardless of load — unlike the old ``parallel < serial/2`` ratio,
        # which still false-failed under full ``-n auto`` saturation (#1795).
        assert len(records) == n, f"expected all {n} thunks to run, got {len(records)}: {records}"
        t0 = min(r[1] for r in records)
        latest_start = max(r[1] for r in records)
        earliest_end = min(r[2] for r in records)
        assert latest_start < earliest_end, (
            "expected the lazy-child thunks to OVERLAP (concurrent render: all "
            "start before any finishes) — total wall-clock is max(), not sum(). "
            f"latest start={(latest_start - t0) * 1000:.1f}ms >= earliest "
            f"end={(earliest_end - t0) * 1000:.1f}ms; "
            f"intervals(ms)={[(r[0], round((r[1] - t0) * 1000, 1), round((r[2] - t0) * 1000, 1)) for r in records]}"
        )

    @pytest.mark.asyncio
    async def test_overlap_proof_rejects_a_serial_loop(self):
        """Gate-off for :meth:`test_total_wall_clock_is_max_not_sum`: the same
        timed thunks awaited ONE AT A TIME (a sequential loop) must NOT
        overlap — ``max(start) >= min(end)`` — proving the overlap assertion
        actually distinguishes parallel from serial (it is not a tautology
        that any execution would satisfy). If this ever fails, the overlap
        proof above is meaningless.
        """
        sleep_s = 0.02
        records: list = []
        thunks = [_make_timed_thunk(vid, sleep_s, records) for vid in ("a", "b", "c")]
        for thunk in thunks:  # serial: await one at a time
            await thunk()

        latest_start = max(r[1] for r in records)
        earliest_end = min(r[2] for r in records)
        assert latest_start >= earliest_end, (
            "a serial loop must NOT overlap (thunk k+1 starts only after thunk k "
            "ends) — if this fails, the overlap proof in "
            "test_total_wall_clock_is_max_not_sum is tautological. "
            f"intervals={records}"
        )

    @pytest.mark.asyncio
    async def test_parallel_thunk_failure_does_not_stall_others(self, rf, make_parent):
        """If one thunk raises, the others still emit their fills.
        Failure is logged + skipped per ADR §"Error propagation".

        Note: ``_failing`` here is a bare ``async def`` that raises —
        NOT how production thunks behave. The PR-B tag closure catches
        its own exceptions and emits a ``data-status="error"``
        envelope. This test exercises the OUTER (defensive) phase-5
        error handler that catches anything the closure missed; the
        log message uses the per-thunk wrapper's ``view_id`` capture
        so multi-failure attribution is correct.
        """
        parent = make_parent()
        parent.request = rf.get("/")
        emitter = ChunkEmitter(parent.request)

        async def _failing():
            raise ValueError("intentional failure mid-render")

        emitter.register_thunk("slot-good-1", _make_sleeping_thunk("slot-good-1", 0.01))
        emitter.register_thunk("slot-bad", _failing)
        emitter.register_thunk("slot-good-2", _make_sleeping_thunk("slot-good-2", 0.02))

        async def _drain():
            return [c async for c in emitter]

        consumer = asyncio.create_task(_drain())
        await parent.arender_chunks(parent.template, emitter)
        await emitter.close()
        chunks = await consumer

        body = b"".join(chunks)
        # Both healthy thunks emit.
        assert b'id="djl-fill-slot-good-1"' in body
        assert b'id="djl-fill-slot-good-2"' in body
        # Failed thunk does NOT emit (its closure didn't catch + wrap;
        # outer phase-5 handler logs and skips). Production thunks do
        # catch + emit error envelopes.
        assert b'id="djl-fill-slot-bad"' not in body

    @pytest.mark.asyncio
    async def test_cancel_mid_stream_aborts_pending_thunks(self, rf, make_parent):
        """T-PRC-4 from ADR §"Test contract": when the emitter is
        cancelled while thunks are still running, ``arender_chunks``
        cancels every pending task via ``task.cancel()`` and returns
        cleanly. Tests the cancellation propagation path that the basic
        ``test_aget_cancels_emitter_on_disconnect`` does not exercise
        (which has no thunks registered).
        """
        parent = make_parent()
        parent.request = rf.get("/")
        emitter = ChunkEmitter(parent.request)

        # Slow thunks so the cancel fires while they're pending.
        for vid in ("slot-slow-1", "slot-slow-2", "slot-slow-3"):
            emitter.register_thunk(vid, _make_sleeping_thunk(vid, 1.0))

        async def _drain():
            return [c async for c in emitter]

        consumer = asyncio.create_task(_drain())
        # Run arender_chunks in the background; cancel the emitter
        # mid-stream so the phase-5 loop hits the cancellation branch.
        render_task = asyncio.create_task(parent.arender_chunks(parent.template, emitter))

        # Yield once so render_task starts; then cancel.
        await asyncio.sleep(0.05)
        await emitter.cancel("test-disconnect")

        # arender_chunks returns cleanly within a short window —
        # without ``_cancel_pending`` it would block on the slow
        # thunks for 1 second.
        await asyncio.wait_for(render_task, timeout=0.5)
        await emitter.close()
        chunks = await consumer

        # No fills should have completed before cancellation.
        body = b"".join(chunks)
        assert b'id="djl-fill-slot-slow-1"' not in body
        assert b'id="djl-fill-slot-slow-2"' not in body
        assert b'id="djl-fill-slot-slow-3"' not in body

    @pytest.mark.asyncio
    async def test_cancel_does_not_leak_wait_for_one_warning(self, rf, make_parent):
        """Regression for #1153 — when ``arender_chunks`` is cancelled
        mid-stream, the ``asyncio.as_completed`` iterator's internal
        ``_wait_for_one`` coroutines must be drained, not GC'd.

        Without the explicit ``await _drain_iterator(...)`` after
        ``_cancel_pending()``, Python emits ``RuntimeWarning: coroutine
        '_wait_for_one' was never awaited`` because ``task.cancel()``
        only signals — it doesn't unblock the queue ``done.get()`` is
        sitting on, and the next coroutine the for-protocol pulls is
        dropped on early ``return``.

        Asserting absence of warning here is the canonical guard that
        future refactors don't regress the lifecycle.
        """
        parent = make_parent()
        parent.request = rf.get("/")
        emitter = ChunkEmitter(parent.request)

        for vid in ("slot-slow-1", "slot-slow-2", "slot-slow-3"):
            emitter.register_thunk(vid, _make_sleeping_thunk(vid, 1.0))

        async def _drain():
            return [c async for c in emitter]

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")

            consumer = asyncio.create_task(_drain())
            render_task = asyncio.create_task(parent.arender_chunks(parent.template, emitter))
            await asyncio.sleep(0.05)
            await emitter.cancel("test-disconnect")
            await asyncio.wait_for(render_task, timeout=0.5)
            await emitter.close()
            await consumer

            # The `_wait_for_one was never awaited` warning fires from
            # CPython's coroutine GC, not from explicit code. Without an
            # explicit gc.collect() the test passes today by accident of
            # CPython's reference-counting timing — under PyPy or a
            # different GC mode the orphan coroutine may not be finalized
            # before the assertion runs. Force collection here so the
            # absence-assertion below is deterministic. Closes #1188 🟡 #2.
            gc.collect()

        wait_for_one_warnings = [w for w in captured if "_wait_for_one" in str(w.message)]
        assert not wait_for_one_warnings, (
            "expected no '_wait_for_one' RuntimeWarnings; got: "
            f"{[str(w.message) for w in wait_for_one_warnings]}"
        )
