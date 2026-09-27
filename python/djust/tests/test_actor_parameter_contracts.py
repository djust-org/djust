"""Render-bound actor metadata and transport serialization."""

import asyncio
import gc
import json
import sys
import uuid
import weakref
from unittest.mock import AsyncMock

import pytest

from djust import event_handler
from djust.tests.test_transport_actor_event_1901 import (
    _ActorView,
    _FakeActorHandle,
    _FakeConsumer,
    _ws_runtime,
)


class ActorOwner:
    def __init__(self):
        self.count = 0
        self.bad_context = False

    def get_context_data(self):
        if self.bad_context:
            raise ValueError("SECRET_CONTEXT_FAILURE")
        return {"count": self.count}

    @event_handler(parameter_policy="strict")
    def advance(self, value: int):
        self.count = value

    @event_handler()
    def clear(self):
        self.advance = None
        self.count += 1

    @event_handler()
    def fail(self, context=False):
        if context:
            self.bad_context = True
        else:
            raise ValueError("SECRET_HANDLER_FAILURE")

    @event_handler()
    def clear_and_fail(self):
        self.advance = None
        raise ValueError("SECRET_REMOVED_HANDLER_FAILURE")


async def mount_actor(owner):
    from djust._rust import create_session_actor

    handle = await create_session_actor(f"contracts-{uuid.uuid4()}")
    try:
        await handle.mount(
            "tests.ActorOwner",
            owner.get_context_data(),
            owner,
            template="<div>{{ count }}</div>",
        )
    except BaseException:
        await handle.shutdown()
        raise
    return handle


def _apply_deferred_decrefs():
    """Enter pyo3 once, so it applies the decrefs Rust threads queued (#3222).

    A Rust thread that drops a ``Py<...>`` while not attached to the
    interpreter cannot decref it; pyo3 queues the decref and applies it at the
    next pyo3 entry on any thread (``pyo3-0.29.2/src/internal/state.rs``,
    ``ReferencePool``). The ViewActor that a failed mount shuts down drops its
    Python view that way, at the end of its task on a tokio worker. Normally
    that happens before the mount's result reaches Python, and completing the
    future enters pyo3. On a starved runner the worker can run after it, and
    then nothing in a ``gc.collect()`` + ``asyncio.sleep`` loop enters pyo3:
    Rust has let go, but the count is still pending. Any ``djust._rust`` call
    applies it; this one only reads a counter.
    """
    from djust._rust import registry_generation

    registry_generation()


async def _released(reference, attempts=100):
    """Wait for ``reference`` to die: apply queued pyo3 decrefs, collect, repeat."""
    for _ in range(attempts):
        _apply_deferred_decrefs()
        gc.collect()
        if reference() is None:
            return True
        await asyncio.sleep(0.01)
    return False


def _holders(reference):
    obj = reference()
    if obj is None:
        return "released"
    return "refcount=%d python referrers=%r" % (
        sys.getrefcount(obj) - 2,
        [type(r).__name__ for r in gc.get_referrers(obj)],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["import", "discovery"])
async def test_failed_actor_mount_does_not_retain_an_unregistered_view(monkeypatch, failure):
    from djust._rust import create_session_actor

    handle = await create_session_actor(f"failed-contracts-{uuid.uuid4()}")
    owner = ActorOwner()
    reference = weakref.ref(owner)
    try:
        with monkeypatch.context() as patch:
            if failure == "import":
                patch.setitem(sys.modules, "djust._parameter_metadata", None)
            else:

                def broken(_owner):
                    raise ValueError("SECRET_MOUNT_FAILURE")

                patch.setattr("djust._parameter_metadata.parameter_contract_manifest", broken)
            with pytest.raises(RuntimeError, match="Actor render parameter contracts unavailable"):
                await handle.mount("tests.ActorOwner", {}, owner, template="<div></div>")
        del owner
        assert await _released(reference), _holders(reference)
        await handle.ping()
    finally:
        await handle.shutdown()


@pytest.mark.asyncio
async def test_the_release_wait_still_sees_a_view_the_actor_holds():
    """Gate for the wait above (#3222): applying pyo3's queued decrefs frees
    only what Rust has let go of. A view a live actor still holds stays alive
    through it, so a real retention still fails that test."""
    owner = ActorOwner()
    reference = weakref.ref(owner)
    handle = await mount_actor(owner)
    try:
        del owner
        assert not await _released(reference, attempts=5)
    finally:
        await handle.shutdown()
    assert await _released(reference), _holders(reference)


@pytest.mark.asyncio
async def test_real_actor_result_keeps_its_snapshot_after_later_event():
    owner = ActorOwner()
    handle = await mount_actor(owner)
    try:
        first = await handle.event("advance", {"value": "7"})
        snapshot = json.dumps(first["parameter_contracts"], sort_keys=True)
        assert (
            first["parameter_contracts"]["owners"][0]["handlers"]["advance"]["policy"] == "strict"
        )
        assert first["recovery_html"] == '<div dj-id="0">7</div>'
        cleared = await handle.event("clear", {})
        assert cleared["parameter_contracts"] is None
        assert cleared["recovery_html"] == '<div dj-id="0">8</div>'
        assert json.dumps(first["parameter_contracts"], sort_keys=True) == snapshot
        assert "count" not in snapshot
    finally:
        await handle.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["handler", "context", "contracts", "removed"])
async def test_real_actor_strict_render_failure_is_redacted(failure, monkeypatch, capfd):
    owner = ActorOwner()
    handle = await mount_actor(owner)
    try:
        if failure == "contracts":

            def broken(_owner):
                raise ValueError("SECRET_CONTRACT_FAILURE")

            monkeypatch.setattr("djust._parameter_metadata.parameter_contract_manifest", broken)
            event, params = "advance", {"value": "7"}
        elif failure == "removed":
            event, params = "clear_and_fail", {}
        else:
            event, params = "fail", {"context": failure == "context"}
        with pytest.raises(
            RuntimeError, match="Actor render parameter contracts unavailable"
        ) as exc:
            await handle.event(event, params)
        assert "SECRET_" not in str(exc.value)
        captured = capfd.readouterr()
        assert "SECRET_" not in captured.err + captured.out
        monkeypatch.undo()
        owner.bad_context = False
        owner.advance = ActorOwner.advance.__get__(owner, ActorOwner)
        recovered = await handle.event("advance", {"value": "8"})
        assert recovered["patches"] is None
        assert ">8</div>" in recovered["html"]
    finally:
        await handle.shutdown()


@pytest.mark.asyncio
async def test_actor_waits_for_lock_and_rechecks_owner():
    actor = _FakeActorHandle()
    consumer = _FakeConsumer(actor_handle=actor)
    runtime = _ws_runtime(consumer)
    async with consumer._render_lock:
        task = asyncio.create_task(runtime.dispatch_event({"event": "bump", "params": {}}))
        await asyncio.sleep(0)
        assert actor.event_calls == []
        consumer.view_instance = _ActorView()
    await task
    assert actor.event_calls == []
    assert consumer.send_update_calls == []
    assert consumer.send_error_calls
    assert not consumer._render_lock.locked()


@pytest.mark.asyncio
async def test_missing_actor_snapshot_cannot_silently_clear_a_strict_scope():
    consumer = _FakeConsumer(actor_handle=_FakeActorHandle())
    runtime = _ws_runtime(consumer)
    runtime._parameter_contracts_active = True
    await runtime.dispatch_event({"event": "bump", "params": {}})
    assert consumer.send_update_calls == []
    assert consumer._wire_version == 0
    assert consumer.send_error_calls == [
        ("Actor render parameter contracts unavailable.", {"recoverable": False})
    ]


@pytest.mark.asyncio
async def test_cancelled_actor_waits_for_result_without_sending_or_redispatching():
    started, release = asyncio.Event(), asyncio.Event()
    actor = _FakeActorHandle()
    consumer = _FakeConsumer(actor_handle=actor)
    runtime = _ws_runtime(consumer)
    runtime.view_instance._flush_deferred_activity_events = AsyncMock()

    async def event(*_args):
        started.set()
        await release.wait()
        return actor.result

    actor.event = event
    task = asyncio.create_task(runtime.dispatch_event({"event": "bump", "params": {}}))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert consumer._render_lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert consumer.send_update_calls == []
    runtime.view_instance._flush_deferred_activity_events.assert_not_awaited()
    assert not consumer._render_lock.locked()


@pytest.mark.asyncio
async def test_actor_deferred_flush_runs_outside_render_lock():
    consumer = _FakeConsumer(actor_handle=_FakeActorHandle())
    runtime = _ws_runtime(consumer)
    called = []

    async def deferred(current):
        async with current._render_lock:
            called.append(True)

    runtime.view_instance._flush_deferred_activity_events = deferred
    await asyncio.wait_for(runtime.dispatch_event({"event": "bump", "params": {}}), 1)
    assert called == [True]


@pytest.mark.asyncio
async def test_replaced_owner_during_actor_operation_cannot_receive_result():
    started, release = asyncio.Event(), asyncio.Event()
    actor = _FakeActorHandle()
    consumer = _FakeConsumer(actor_handle=actor)
    runtime = _ws_runtime(consumer)

    async def event(*_args):
        started.set()
        await release.wait()
        return actor.result

    actor.event = event
    task = asyncio.create_task(runtime.dispatch_event({"event": "bump", "params": {}}))
    try:
        await asyncio.wait_for(started.wait(), 1)
        consumer.view_instance = _ActorView()
    finally:
        release.set()
        await task
    assert consumer.send_update_calls == []
    assert consumer.send_error_calls
    assert not consumer._render_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest", [None, {"version": 1, "owners": []}])
async def test_binary_preference_does_not_drop_contract_envelope(manifest):
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.use_binary = True
    consumer.view_instance = ActorOwner()
    consumer.send_json = AsyncMock()
    consumer._send_frame = AsyncMock()
    consumer._flush_all_pending = AsyncMock()
    consumer._attach_debug_payload = lambda *args: None
    version = consumer._next_version_armed("<div>7</div>")
    snapshot = {"parameter_contracts": manifest, "parameter_contract_view": "tests.ActorOwner"}
    await consumer._send_update(patches=[], version=version, parameter_contract_snapshot=snapshot)
    consumer._send_frame.assert_not_awaited()
    frame = consumer.send_json.call_args.args[0]
    assert frame["parameter_contracts"] == manifest
    assert json.loads(consumer._recovery_contracts) == snapshot
