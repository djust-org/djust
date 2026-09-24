"""Debug restoration renders obey the same owner, lock and metadata boundary."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView, event_handler
from djust.runtime import ViewRuntime, WSConsumerTransport
from djust.time_travel import EventSnapshot
from djust.websocket import LiveViewConsumer


class DebugPart:
    def __init__(self):
        self._component_id = "part"
        self.value = 99

    @event_handler(parameter_policy="strict")
    def choose(self, value: int):
        self.value = value


class DebugView(LiveView):
    template = "<div dj-root>{{ count }} / {{ component_value }}</div>"
    time_travel_enabled = True

    def mount(self, request=None, **kwargs):
        self.count = 42
        self._components = {"part": DebugPart()}

    def get_context_data(self, **kwargs):
        return {"count": self.count, "component_value": self._components["part"].value}

    @event_handler(parameter_policy="strict")
    def advance(self, value: int):
        self.count += value


async def setup(full_html=False):
    view = DebugView()
    view.mount()
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="advance",
            params={"value": 2},
            ref=None,
            ts=0.0,
            state_before={"count": 7, "__components__": {"part": {"value": 5}}},
            state_after={"count": 9, "__components__": {"part": {"value": 6}}},
        )
    )
    await sync_to_async(view.render_with_diff)()
    if full_html:
        view._rust_view.reset()
    consumer = LiveViewConsumer()
    consumer.view_instance = view
    consumer.use_binary = False
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    consumer._flush_all_pending = AsyncMock()
    runtime = ViewRuntime(WSConsumerTransport(consumer))
    runtime.view_instance = view
    runtime._parameter_contract_view = "app.DebugPage"
    consumer._runtime = runtime
    return consumer, view


async def produce(consumer, operation):
    if operation == "jump":
        await consumer.handle_time_travel_jump({"index": 0, "which": "before"})
    elif operation == "component":
        await consumer.handle_time_travel_component_jump(
            {"index": 0, "component_id": "part", "which": "before"}
        )
    else:
        await consumer.handle_forward_replay({"from_index": 0})


OPERATIONS = ["jump", "component", "replay"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("full_html", [False, True])
async def test_debug_frame_and_recovery_carry_render_contracts(operation, full_html, settings):
    settings.DEBUG = True
    consumer, view = await setup(full_html)
    await produce(consumer, operation)
    consumer.send_error.assert_not_awaited()
    frames = [call.args[0] for call in consumer.send_json.call_args_list]
    frame, acknowledgement = frames
    assert frame["type"] == ("html_update" if full_html else "patch")
    assert frame.get("source") == "broadcast"
    assert frame["parameter_contract_view"] == "app.DebugPage"
    owners = frame["parameter_contracts"]["owners"]
    assert owners[0]["handlers"]["advance"]["policy"] == "strict"
    assert owners[1]["component_id"] == "part"
    assert owners[1]["handlers"]["choose"]["policy"] == "strict"
    assert acknowledgement["type"] == "time_travel_state"
    assert frame["version"] == consumer._recovery_version == 1
    assert view.count == {"jump": 7, "component": 42, "replay": 9}[operation]
    assert view._components["part"].value == 5
    view.advance = None
    await consumer.handle_request_html({})
    recovered = consumer.send_json.call_args.args[0]
    assert recovered["parameter_contracts"] == frame["parameter_contracts"]
    assert recovered["version"] == frame["version"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_debug_restoration_waits_for_render_lock(operation, settings):
    settings.DEBUG = True
    consumer, view = await setup()
    try:
        async with consumer._render_lock:
            task = asyncio.create_task(produce(consumer, operation))
            await asyncio.sleep(0.02)
            assert view.count == 42
            assert view._components["part"].value == 99
            consumer.send_json.assert_not_awaited()
    finally:
        await asyncio.wait_for(task, 5)
    assert consumer.send_json.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_debug_owner_replaced_while_waiting_is_not_restored(operation, settings):
    settings.DEBUG = True
    consumer, view = await setup()
    replacement = DebugView()
    replacement.mount()
    async with consumer._render_lock:
        task = asyncio.create_task(produce(consumer, operation))
        await asyncio.sleep(0.01)
        consumer.view_instance = replacement
    await task
    assert view.count == replacement.count == 42
    assert view._components["part"].value == replacement._components["part"].value == 99
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_not_awaited()


RESTORE_FUNCTIONS = {
    "jump": "restore_snapshot",
    "component": "restore_component_snapshot",
    "replay": "replay_event",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("phase", ["restore", "render"])
async def test_cancelled_debug_operation_retains_lock_until_worker_finishes(
    operation, phase, settings, monkeypatch
):
    import djust.time_travel as time_travel

    settings.DEBUG = True
    consumer, view = await setup()
    started, release = threading.Event(), threading.Event()
    # Root restore deliberately deletes public instance attributes absent from
    # the snapshot, so patch the render declaration rather than a ghost assign.
    target = time_travel if phase == "restore" else type(view)
    name = RESTORE_FUNCTIONS[operation] if phase == "restore" else "render_with_diff"
    original = getattr(target, name)

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5), "test did not release worker"
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(target, name, blocked)
        task = asyncio.create_task(produce(consumer, operation))
        try:
            for _ in range(200):
                if started.is_set():
                    break
                await asyncio.sleep(0.005)
            assert started.is_set()
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                assert consumer._render_lock.locked()
                assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_not_awaited()
    assert not consumer._render_lock.locked()
    assert consumer._last_sent_version == 0
    await produce(consumer, operation)
    frames = [call.args[0] for call in consumer.send_json.call_args_list]
    assert len(frames) == 2
    assert frames[0]["version"] == 1
    assert frames[0]["parameter_contracts"] is not None
    if phase == "render":
        assert frames[0]["type"] == "html_update"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("phase", ["restore", "render"])
async def test_debug_owner_replaced_during_worker_never_delivers_old_state(
    operation, phase, settings, monkeypatch
):
    import djust.time_travel as time_travel

    settings.DEBUG = True
    consumer, view = await setup()
    replacement = DebugView()
    replacement.mount()
    target = time_travel if phase == "restore" else type(view)
    name = RESTORE_FUNCTIONS[operation] if phase == "restore" else "render_with_diff"
    original = getattr(target, name)

    def replace(*args, **kwargs):
        result = original(*args, **kwargs)
        consumer.view_instance = replacement
        return result

    monkeypatch.setattr(target, name, replace)
    await produce(consumer, operation)
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_not_awaited()
    assert replacement.count == 42
    assert replacement._components["part"].value == 99
    assert consumer._last_sent_version == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_debug_metadata_failure_withholds_dom_and_ack_then_recovers(
    operation, settings, monkeypatch, caplog
):
    settings.DEBUG = True
    consumer, _ = await setup()
    consumer.send_error = LiveViewConsumer.send_error.__get__(consumer)

    def broken(_view):
        raise ValueError("SECRET_DEBUG_CONTRACT_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr("djust._parameter_metadata.parameter_contract_manifest", broken)
        await produce(consumer, operation)
    assert consumer.send_json.await_count == 1
    error = consumer.send_json.call_args.args[0]
    assert error["type"] == "error"
    assert error["source"] == "async"  # never settle an unrelated foreground request
    assert error["code"] == "render_error"
    assert "traceback" not in error
    assert "SECRET_DEBUG_CONTRACT_FAILURE" not in str(error) + caplog.text
    assert consumer._last_sent_version == 0
    consumer.send_json.reset_mock()
    await produce(consumer, operation)
    frame, ack = [call.args[0] for call in consumer.send_json.call_args_list]
    assert frame["type"] == "html_update"
    assert frame["version"] == 1
    assert frame["parameter_contracts"] is not None
    assert ack["type"] == "time_travel_state"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_debug_production_guard_still_precedes_mutation(operation, settings):
    settings.DEBUG = False
    consumer, view = await setup()
    await produce(consumer, operation)
    assert view.count == 42
    assert view._components["part"].value == 99
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited_once()
    assert consumer.send_error.call_args.kwargs.get("source") == "async"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_debug_strict_removal_clears_and_legacy_session_omits_metadata(
    operation, settings, monkeypatch
):
    settings.DEBUG = True
    consumer, _ = await setup()
    await produce(consumer, operation)

    @event_handler(parameter_policy="legacy")
    def advance(self, value):
        self.count += value

    @event_handler(parameter_policy="legacy")
    def choose(self, value):
        self.value = value

    monkeypatch.setattr(DebugView, "advance", advance)
    monkeypatch.setattr(DebugPart, "choose", choose)
    for _ in range(2):
        consumer.send_json.reset_mock()
        await produce(consumer, operation)
        frame = consumer.send_json.call_args_list[0].args[0]
        assert frame["parameter_contracts"] is None

    legacy_consumer, _ = await setup()
    await produce(legacy_consumer, operation)
    frame = legacy_consumer.send_json.call_args_list[0].args[0]
    assert "parameter_contracts" not in frame
    assert "parameter_contract_view" not in frame


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_debug_strict_frames_use_json_when_binary_is_negotiated(operation, settings):
    settings.DEBUG = True
    consumer, _ = await setup()
    consumer.use_binary = True
    consumer._send_frame = AsyncMock()
    await produce(consumer, operation)
    consumer._send_frame.assert_not_awaited()
    assert consumer.send_json.call_args_list[0].args[0]["parameter_contracts"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_nonrestorable_debug_projection_is_not_restored(operation, settings):
    settings.DEBUG = True
    consumer, view = await setup()
    view._time_travel_buffer.jump(0).restorable = False
    await produce(consumer, operation)
    assert view.count == 42
    assert view._components["part"].value == 99
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited_once()
