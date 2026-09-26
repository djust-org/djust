"""``assign_async``'s runners let cancellation through.

The async runner caught ``BaseException``, so cancelling the task that runs it
(``cancel_async``, view teardown) was swallowed: the task finished normally and
the attribute became an errored ``AsyncResult`` holding the ``CancelledError``.
It now catches ``Exception`` only; loader failures are still surfaced.
"""

import asyncio

import pytest

from djust.async_result import AsyncResult
from djust.mixins.async_work import AsyncWorkMixin


class FakeView(AsyncWorkMixin):
    pass


def _runner(view, name):
    callback, args, kwargs = view._async_tasks[f"assign_async:{name}"]
    return callback(*args, **kwargs)


@pytest.mark.asyncio
async def test_cancelling_an_async_loader_propagates_and_leaves_it_pending():
    started = asyncio.Event()

    async def loader():
        started.set()
        await asyncio.sleep(3600)

    view = FakeView()
    view.assign_async("metrics", loader)
    task = asyncio.ensure_future(_runner(view, "metrics"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert view.metrics == AsyncResult.pending()


@pytest.mark.asyncio
async def test_an_async_loader_failure_is_still_surfaced():
    async def loader():
        raise ValueError("boom")

    view = FakeView()
    view.assign_async("metrics", loader)
    await _runner(view, "metrics")

    assert view.metrics.failed
    assert isinstance(view.metrics.error, ValueError)


def test_a_sync_loader_failure_is_still_surfaced():
    def loader():
        raise ValueError("boom")

    view = FakeView()
    view.assign_async("metrics", loader)
    _runner(view, "metrics")

    assert view.metrics.failed
