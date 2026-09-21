"""Completion control messages are independent of callback results and scheduling."""

import asyncio
from types import SimpleNamespace

import pytest

from djust._async_batch import AsyncBatch

pytestmark = pytest.mark.asyncio


async def test_prestart_cancellation_still_completes_batch_once():
    owner = SimpleNamespace(_async_tasks={"one": (None, (), {}), "two": (None, (), {})})
    sent = []
    done = asyncio.Event()

    async def send(frame):
        sent.append(frame)
        done.set()

    async def runner(*args):
        pytest.fail("Cancelled work must not start")

    batch = AsyncBatch(owner)
    assert owner._async_tasks == {}
    batch.dispatch(SimpleNamespace(send=send), runner)
    handles = tuple(owner._async_task_handles)
    for handle in handles:
        handle.cancel()
    await asyncio.gather(*handles, return_exceptions=True)
    await asyncio.wait_for(done.wait(), 1)
    assert sent == [{"type": "async_complete", "async_batch": batch.token}]
    batch.dispatch(SimpleNamespace(send=send), runner)
    await asyncio.sleep(0)
    assert len(sent) == 1


async def test_discarded_batch_completes_without_running_callbacks():
    owner = SimpleNamespace(_async_tasks={"one": (None, (), {})})
    sent = []
    done = asyncio.Event()

    async def send(frame):
        sent.append(frame)
        done.set()

    batch = AsyncBatch(owner)
    transport = SimpleNamespace(send=send)
    batch.discard(transport)
    batch.discard(transport)
    batch.dispatch(transport, None)
    await asyncio.wait_for(done.wait(), 1)
    assert sent == [{"type": "async_complete", "async_batch": batch.token}]


async def test_empty_batch_never_advertises_or_emits_background_work():
    owner = SimpleNamespace()
    batch = AsyncBatch(owner)
    assert batch.fields() == {}
    batch.dispatch(None, None)


async def test_captured_batches_have_distinct_tokens_and_do_not_drain_later_work():
    owner = SimpleNamespace(_async_tasks={"one": (None, (), {})})
    first = AsyncBatch(owner)
    owner._async_tasks["two"] = (None, (), {})
    second = AsyncBatch(owner)
    assert first.token != second.token
    assert [name for name, _ in first.queued] == ["one"]
    assert [name for name, _ in second.queued] == ["two"]
