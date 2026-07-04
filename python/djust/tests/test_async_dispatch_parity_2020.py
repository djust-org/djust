"""#2020 — transport-parity for the async-work dispatch path (runtime vs consumer).

#2016 fixed a #1646 parallel-path drift: the WS consumer (`_run_async_work`) had
an `iscoroutinefunction(callback)` branch that awaited an `async def` background
callback directly, but the converged runtime path (`_execute_async_task`) routed
EVERY callback through `sync_to_async`, so an async `@background` handler raised
``TypeError: sync_to_async can only be applied to sync functions`` and silently
failed (#2001). The fix copied the branch into the runtime path — leaving TWO
identical copies, i.e. the exact drift shape that would re-break on the next edit.

This is the structural cure (#1646) + its parity guard (#1125): both paths now
delegate to ONE shared `run_async_callback`, and these tests (a) exercise that
helper across all three callback shapes and (b) pin that both transports call it
and neither carries its own dispatch branch — so they can never drift again.
"""

from __future__ import annotations

import inspect

import pytest

from djust.mixins.async_work import run_async_callback


class TestRunAsyncCallbackShapes:
    """The shared helper handles every callback shape both transports feed it."""

    @pytest.mark.asyncio
    async def test_async_def_callback_is_awaited_directly(self):
        # Gate-off sentinel (#1468): with the fix this returns 10; route an async
        # callback through sync_to_async instead (the #2001 bug) and it raises
        # TypeError instead of returning — so this test fails-closed on regress.
        async def acb(x, *, mult):
            return x * mult

        assert await run_async_callback(acb, (5,), {"mult": 2}) == 10

    @pytest.mark.asyncio
    async def test_sync_callback_is_thread_dispatched(self):
        def scb(x, *, add):
            return x + add

        assert await run_async_callback(scb, (5,), {"add": 3}) == 8

    @pytest.mark.asyncio
    async def test_sync_callback_returning_a_coroutine_is_unwrapped(self):
        # A sync function that returns an un-awaited coroutine (pre-v0.4.2
        # @background contract) must have that coroutine awaited too.
        async def inner():
            return "deep"

        def scb():
            return inner()

        assert await run_async_callback(scb, (), {}) == "deep"

    @pytest.mark.asyncio
    async def test_defaults_for_empty_args_kwargs(self):
        async def acb():
            return "ok"

        assert await run_async_callback(acb) == "ok"


class TestTransportDispatchParity:
    """Structural pin (#1646/#1125): both transports route through the ONE helper
    and neither keeps its own ``iscoroutinefunction(callback)`` branch. A future
    edit that re-introduces a divergent copy trips these immediately."""

    def _sources(self):
        from djust.runtime import ViewRuntime
        from djust.websocket import LiveViewConsumer

        return (
            inspect.getsource(LiveViewConsumer._run_async_work),
            inspect.getsource(ViewRuntime._execute_async_task),
        )

    def test_both_paths_delegate_to_the_shared_helper(self):
        ws_src, rt_src = self._sources()
        assert "run_async_callback" in ws_src, "WS path must call the shared helper"
        assert "run_async_callback" in rt_src, "runtime path must call the shared helper"

    def test_neither_path_carries_its_own_async_dispatch_branch(self):
        ws_src, rt_src = self._sources()
        # The drift-prone branch now lives ONLY inside run_async_callback.
        assert "iscoroutinefunction(callback)" not in ws_src, (
            "WS path re-grew its own async-dispatch branch — drift risk (#2020)"
        )
        assert "iscoroutinefunction(callback)" not in rt_src, (
            "runtime path re-grew its own async-dispatch branch — drift risk (#2020)"
        )

    def test_helper_is_the_single_source_of_truth(self):
        # Exactly one definition of the dispatch helper across the package.
        from pathlib import Path

        src_root = Path(inspect.getfile(run_async_callback)).resolve().parent.parent
        needle = "async def " + "run_async_callback"  # split so this file can't self-match
        defs = [
            p
            for p in src_root.rglob("*.py")
            if "tests" not in p.parts and needle in p.read_text(encoding="utf-8")
        ]
        assert len(defs) == 1, f"expected 1 definition, found {len(defs)}: {defs}"
