"""The pool's slots are what asgiref expects of a thread-sensitive context.

djust's worker pool (#3074) and HTTP pool (#3114) set a slot object as
``SyncToAsync.thread_sensitive_context``. asgiref 3.13.0 reads
``thread_sensitive_context._exited`` in ``SyncToAsync.__call__`` before it picks
the executor, and the slots (``__slots__`` without ``_exited``) raised
``AttributeError: '_Slot' object has no attribute '_exited'`` on every
``sync_to_async`` of a pooled session. A slot never exits while the pool lives.
"""

from __future__ import annotations

import threading

import pytest
from asgiref.sync import sync_to_async

from djust import worker_pool
from djust.config import config as djust_config


@pytest.mark.parametrize("slot_class", [worker_pool._Slot, worker_pool._HTTPSlot])
def test_a_slot_says_it_has_not_exited(slot_class):
    slot = slot_class(0)
    assert slot._exited is False
    with pytest.raises(AttributeError):
        slot._exited = True  # asgiref never writes it on a foreign context


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setitem(djust_config._config, "worker_threads", 2)
    monkeypatch.setattr(worker_pool, "_pool", [])
    monkeypatch.setattr(worker_pool, "_http_pools", {})


@pytest.mark.asyncio
async def test_a_pooled_session_hop_runs_on_its_slot_thread(pool):
    binding = worker_pool.bind_session()
    assert binding is not None
    try:
        first = await sync_to_async(threading.current_thread)()
        second = await sync_to_async(threading.current_thread)()
    finally:
        binding.release()
    assert first is second
    assert first.name.startswith("djust-worker")


@pytest.mark.asyncio
async def test_a_pooled_http_request_hop_runs_on_its_slot_thread(pool):
    binding = worker_pool.bind_http_request(2)
    assert binding is not None
    try:
        thread = await sync_to_async(threading.current_thread)()
    finally:
        binding.release()
    assert thread.name.startswith("djust-http")
