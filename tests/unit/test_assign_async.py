"""Tests for :class:`djust.async_result.AsyncResult` and
:meth:`djust.mixins.async_work.AsyncWorkMixin.assign_async`.

Covers v0.5.0 async rendering additions.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from djust.async_result import AsyncResult
from djust.mixins.async_work import AsyncWorkMixin


class _View(AsyncWorkMixin):
    """Minimal concrete view for exercising the mixin in isolation."""


# ---------------------------------------------------------------------------
# AsyncResult
# ---------------------------------------------------------------------------


def test_pending_initial_state():
    pending = AsyncResult.pending()
    assert pending.loading is True
    assert pending.ok is False
    assert pending.failed is False
    assert pending.result is None
    assert pending.error is None


def test_succeeded_carries_result():
    done = AsyncResult.succeeded({"total": 42})
    assert done.loading is False
    assert done.ok is True
    assert done.failed is False
    assert done.result == {"total": 42}
    assert done.error is None


def test_errored_carries_exception():
    exc = ValueError("boom")
    failed = AsyncResult.errored(exc)
    assert failed.loading is False
    assert failed.ok is False
    assert failed.failed is True
    assert failed.result is None
    assert failed.error is exc
    assert failed.error.args == ("boom",)


def test_bool_truthy_only_when_ok():
    assert not bool(AsyncResult.pending())
    assert not bool(AsyncResult.errored(RuntimeError("x")))
    assert bool(AsyncResult.succeeded("hello"))


def test_async_result_is_frozen():
    done = AsyncResult.succeeded("x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        done.result = "mutated"  # type: ignore[misc]


def test_async_result_invariant_enforced_on_direct_construction():
    """Direct construction with invalid state combinations raises. The
    docstring promises exactly-one-flag; __post_init__ makes it true."""
    # Two flags True
    with pytest.raises(ValueError, match="exactly one"):
        AsyncResult(loading=True, ok=True)
    # Zero flags True
    with pytest.raises(ValueError, match="exactly one"):
        AsyncResult(loading=False, ok=False, failed=False)
    # ok with an error is contradictory
    with pytest.raises(ValueError, match="cannot carry an error"):
        AsyncResult(loading=False, ok=True, failed=False, error=RuntimeError("x"))
    # failed without an error is under-specified
    with pytest.raises(ValueError, match="requires an error"):
        AsyncResult(loading=False, ok=False, failed=True, error=None)


# ---------------------------------------------------------------------------
# assign_async — scheduling
# ---------------------------------------------------------------------------


def test_assign_async_sets_pending_immediately():
    view = _View()
    view.assign_async("metrics", lambda: {"n": 1})

    # Loading state must be visible before the scheduled callback runs.
    assert isinstance(view.metrics, AsyncResult)
    assert view.metrics.loading is True
    # Task scheduled under the conventional name.
    assert "assign_async:metrics" in view._async_tasks


def _drain_sync(view, name: str):
    """Run the sync-runner callback scheduled by assign_async inline."""
    callback, args, kwargs = view._async_tasks.pop(f"assign_async:{name}")
    callback(*args, **kwargs)


def test_assign_async_success_path():
    view = _View()
    view.assign_async("notifications", lambda: ["ping", "pong"])
    _drain_sync(view, "notifications")

    assert isinstance(view.notifications, AsyncResult)
    assert view.notifications.ok is True
    assert view.notifications.result == ["ping", "pong"]
    assert view.notifications.loading is False
    assert view.notifications.failed is False


def test_assign_async_failure_path():
    view = _View()

    def _broken():
        raise RuntimeError("database exploded")

    view.assign_async("metrics", _broken)
    _drain_sync(view, "metrics")

    assert isinstance(view.metrics, AsyncResult)
    assert view.metrics.failed is True
    assert view.metrics.loading is False
    assert isinstance(view.metrics.error, RuntimeError)
    assert str(view.metrics.error) == "database exploded"


def test_assign_async_multiple_concurrent_loads():
    view = _View()
    view.assign_async("a", lambda: 1)
    view.assign_async("b", lambda: 2)
    view.assign_async("c", lambda: 3)

    # Three independent scheduled tasks, all in loading state.
    assert set(view._async_tasks.keys()) == {
        "assign_async:a",
        "assign_async:b",
        "assign_async:c",
    }
    assert view.a.loading and view.b.loading and view.c.loading

    for name in ("a", "b", "c"):
        _drain_sync(view, name)
    assert view.a.result == 1 and view.b.result == 2 and view.c.result == 3


def test_cancel_async_works_with_assign_async_task_name():
    view = _View()
    view.assign_async("slow", lambda: "done")
    view.cancel_async("assign_async:slow")

    # Scheduled task removed; cancellation marker set.
    assert "assign_async:slow" not in view._async_tasks
    assert "assign_async:slow" in view._async_cancelled


def test_assign_async_accepts_async_loader():
    view = _View()

    async def _loader():
        await asyncio.sleep(0)
        return {"shape": "coroutine"}

    view.assign_async("async_metric", _loader)

    assert view.async_metric.loading is True
    callback, args, kwargs = view._async_tasks.pop("assign_async:async_metric")
    # The wrapper itself must be a coroutine function so the websocket consumer
    # awaits it natively (rather than running it in a worker thread).
    assert asyncio.iscoroutinefunction(callback)

    # Run the async wrapper to completion to verify result propagation.
    asyncio.new_event_loop().run_until_complete(callback(*args, **kwargs))
    assert view.async_metric.ok is True
    assert view.async_metric.result == {"shape": "coroutine"}


def test_assign_async_async_loader_failure():
    view = _View()

    async def _loader():
        raise ValueError("async boom")

    view.assign_async("bad", _loader)
    callback, args, kwargs = view._async_tasks.pop("assign_async:bad")
    asyncio.new_event_loop().run_until_complete(callback(*args, **kwargs))

    assert view.bad.failed is True
    assert isinstance(view.bad.error, ValueError)
    assert str(view.bad.error) == "async boom"


def test_assign_async_forwards_args_and_kwargs():
    view = _View()
    captured: dict = {}

    def _loader(a, b, *, mode):
        captured["args"] = (a, b)
        captured["mode"] = mode
        return f"{a}:{b}:{mode}"

    view.assign_async("x", _loader, 1, 2, mode="fast")
    _drain_sync(view, "x")

    assert captured == {"args": (1, 2), "mode": "fast"}
    assert view.x.ok is True and view.x.result == "1:2:fast"
