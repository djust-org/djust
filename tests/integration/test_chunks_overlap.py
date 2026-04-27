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
import time
from typing import List

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
        """Three thunks with 50ms sleep each. Sequential render takes
        ≥ 150ms; parallel takes ≈ 50ms. Generous 100ms tolerance — the
        invariant we're checking is "well below the sequential
        baseline", not exact wall-clock."""
        parent = make_parent()
        parent.request = rf.get("/")
        emitter = ChunkEmitter(parent.request)

        for vid in ("slot-x", "slot-y", "slot-z"):
            emitter.register_thunk(vid, _make_sleeping_thunk(vid, 0.05))

        async def _drain():
            chunks: List[bytes] = []
            async for c in emitter:
                chunks.append(c)
            return chunks

        consumer = asyncio.create_task(_drain())
        t0 = time.perf_counter()
        await parent.arender_chunks(parent.template, emitter)
        await emitter.close()
        await consumer
        elapsed = time.perf_counter() - t0

        # 3 × 50ms sequential = 150ms baseline. Parallel: ~50ms +
        # overhead. 100ms ceiling proves parallel even on slow
        # workers.
        assert elapsed < 0.10, f"expected parallel render under 100ms, took {elapsed * 1000:.1f}ms"

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
