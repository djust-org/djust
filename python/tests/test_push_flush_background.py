"""Tests for push event flushing in @background tasks (#693).

Verifies that ``_flush_pending_push_events()`` / ``flush_push_events()``
correctly flushes queued push events mid-task when a callback is
registered, and is a safe no-op otherwise.
"""

from __future__ import annotations

import asyncio

import pytest

from djust.mixins.push_events import PushEventMixin


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _View(PushEventMixin):
    """Minimal host for PushEventMixin tests."""

    pass


# ---------------------------------------------------------------------------
# Tests: flush mechanism
# ---------------------------------------------------------------------------


class TestFlushPushEvents:
    """Tests for the flush_push_events / _flush_pending_push_events API."""

    @pytest.mark.asyncio
    async def test_flush_sends_queued_events_via_callback(self):
        """When a flush callback is set, flush_push_events drains the queue."""
        view = _View()
        flushed = []

        async def fake_flush():
            flushed.extend(view._drain_push_events())

        view._push_events_flush_callback = fake_flush

        view.push_event("step", {"n": 1})
        view.push_event("step", {"n": 2})
        assert len(view._pending_push_events) == 2

        await view.flush_push_events()
        # The callback was invoked and drained the events
        assert len(flushed) == 2
        assert flushed[0] == ("step", {"n": 1})
        assert flushed[1] == ("step", {"n": 2})
        # Queue is now empty (drained by the callback)
        assert len(view._pending_push_events) == 0

    @pytest.mark.asyncio
    async def test_flush_noop_when_no_callback(self):
        """Without a callback (test/HTTP mode), flush is a safe no-op."""
        view = _View()
        view.push_event("hello", {"x": 1})
        assert view._push_events_flush_callback is None

        # Should not raise
        await view.flush_push_events()

        # Events are still queued (not lost)
        assert len(view._pending_push_events) == 1

    @pytest.mark.asyncio
    async def test_flush_noop_when_queue_empty(self):
        """Flush with no pending events does not invoke the callback."""
        view = _View()
        called = []

        async def fake_flush():
            called.append(True)

        view._push_events_flush_callback = fake_flush
        await view.flush_push_events()
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_private_alias_calls_public(self):
        """_flush_pending_push_events delegates to flush_push_events."""
        view = _View()
        flushed = []

        async def fake_flush():
            flushed.extend(view._drain_push_events())

        view._push_events_flush_callback = fake_flush
        view.push_event("test", {})
        await view._flush_pending_push_events()
        assert len(flushed) == 1

    @pytest.mark.asyncio
    async def test_multiple_flushes_accumulate(self):
        """Multiple push + flush cycles each deliver their batch."""
        view = _View()
        all_flushed = []

        async def fake_flush():
            all_flushed.append(list(view._drain_push_events()))

        view._push_events_flush_callback = fake_flush

        # Batch 1
        view.push_event("a", {"n": 1})
        await view.flush_push_events()

        # Batch 2
        view.push_event("b", {"n": 2})
        view.push_event("c", {"n": 3})
        await view.flush_push_events()

        assert len(all_flushed) == 2
        assert len(all_flushed[0]) == 1
        assert all_flushed[0][0][0] == "a"
        assert len(all_flushed[1]) == 2


class TestPushCommandsFlush:
    """Verify push_commands events are flushable mid-task."""

    @pytest.mark.asyncio
    async def test_push_commands_flushed_mid_task(self):
        """push_commands queues a djust:exec event that flush delivers."""
        from djust.js import JS

        view = _View()
        flushed = []

        async def fake_flush():
            flushed.extend(view._drain_push_events())

        view._push_events_flush_callback = fake_flush

        chain = JS.add_class("highlight", to="#btn")
        view.push_commands(chain)
        assert len(view._pending_push_events) == 1

        await view.flush_push_events()
        assert len(flushed) == 1
        assert flushed[0][0] == "djust:exec"
        assert "ops" in flushed[0][1]


class TestTutorialMixinFlush:
    """Verify TutorialMixin._run_step flushes after push_commands."""

    @pytest.mark.asyncio
    async def test_run_step_flushes_setup_commands(self):
        """_run_step calls _flush_pending_push_events after the setup chain."""
        from djust.mixins.waiters import WaiterMixin
        from djust.tutorials import TutorialMixin, TutorialStep

        class View(WaiterMixin, PushEventMixin, TutorialMixin):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)

            def start_async(self, callback, *args, name=None, **kwargs):
                pass

        view = View()
        flush_calls = []

        async def track_flush():
            events = view._drain_push_events()
            flush_calls.append(list(events))

        view._push_events_flush_callback = track_flush

        step = TutorialStep(
            target="#test",
            message="Hello",
            timeout=None,
            wait_for=None,
        )

        view.tutorial_running = True
        view.tutorial_current_step = 0
        view._tutorial_skip_signal = asyncio.Event()
        view._tutorial_cancel_signal = asyncio.Event()

        await view._run_step(step)

        # _run_step flushes twice: once after setup, once after cleanup
        assert len(flush_calls) >= 1
        # First flush should contain the setup chain (highlight + narrate + focus)
        assert len(flush_calls[0]) > 0
        assert flush_calls[0][0][0] == "djust:exec"
