"""Bespoke background producers deliver the contract of their actual render."""

import json
import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async

from djust import event_handler
from djust.runtime import ViewRuntime, WSConsumerTransport
from djust.tests.test_background_html_fallback import BackgroundView
from djust.websocket import LiveViewConsumer


class ContractView(BackgroundView):
    @event_handler(parameter_policy="strict")
    def choose(self, value: int):
        self.count = value

    def handle_async_result(self, name, result=None, error=None):
        self.count += 1


async def setup(full_html=False, view_class=ContractView):
    view = view_class()
    view.mount(None)
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
    runtime._parameter_contract_view = "app.Background"
    consumer._runtime = runtime
    return consumer, view, runtime


async def produce(consumer, source):
    if source == "tick":
        return await consumer._tick_once()
    if source == "push":
        return await consumer.server_push({"state": {"count": consumer.view_instance.count + 1}})
    if source == "notify":
        return await consumer.db_notify({"channel": "counts", "payload": {}})

    async def callback():
        if source == "async_error":
            raise ValueError("application failure")
        return 1

    return await consumer._run_async_work("work", callback, (), {}, event_name="load")


SOURCES = ["tick", "push", "notify", "async", "async_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("full_html", [False, True])
async def test_background_frame_and_recovery_share_render_contract(source, full_html):
    consumer, view, runtime = await setup(full_html)
    await produce(consumer, source)
    frame = consumer.send_json.call_args.args[0]
    assert frame["type"] == ("html_update" if full_html else "patch")
    assert frame["parameter_contract_view"] == "app.Background"
    assert frame["parameter_contracts"]["owners"][0]["handlers"]["choose"]["policy"] == "strict"
    assert frame["version"] == consumer._recovery_version == 1
    assert runtime._parameter_contracts_active is True
    snapshot = json.loads(consumer._recovery_contracts)
    assert snapshot["parameter_contracts"] == frame["parameter_contracts"]
    # A later declaration cannot change the snapshot of the recovery HTML.
    view.choose = None
    await consumer.handle_request_html({})
    recovery = consumer.send_json.call_args.args[0]
    assert recovery["parameter_contracts"] == snapshot["parameter_contracts"]
    assert "Count: 1" in recovery["html"]
    assert recovery["version"] == frame["version"]
    consumer.send_error.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_legacy_background_frames_keep_legacy_shape(source):
    consumer, _, _ = await setup(view_class=BackgroundView)
    if source.startswith("async"):
        consumer.view_instance.handle_async_result = lambda *args, **kwargs: None
    await produce(consumer, source)
    frame = consumer.send_json.call_args.args[0]
    assert "parameter_contracts" not in frame
    assert "parameter_contract_view" not in frame
    assert consumer._recovery_contracts is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_last_strict_handler_removal_keeps_emitting_explicit_clear(source):
    consumer, view, _ = await setup()
    await produce(consumer, source)
    view.choose = None
    for _ in range(2):
        await produce(consumer, source)
        assert consumer.send_json.call_args.args[0]["parameter_contracts"] is None
        assert json.loads(consumer._recovery_contracts)["parameter_contracts"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_contract_failure_withholds_frame_and_resets_unsent_baseline(source, monkeypatch):
    consumer, view, _ = await setup()

    def broken(_view):
        raise ValueError("SECRET_DISCOVERY_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr("djust._parameter_metadata.parameter_contract_manifest", broken)
        await produce(consumer, source)
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited_once()
    assert consumer.send_error.call_args.kwargs["source"] == "async"
    assert "SECRET" not in str(consumer.send_error.call_args)
    assert view.count == 1  # a metadata error must not rerun the application callback
    assert consumer._last_sent_version == 0
    assert getattr(consumer, "_recovery_html", None) is None

    await produce(consumer, source)
    frame = consumer.send_json.call_args.args[0]
    assert frame["type"] == "html_update"
    assert "Count: 2" in frame["html"]
    assert frame["version"] == 1
    assert frame["parameter_contracts"] is not None
    assert view._force_full_html is False


@pytest.mark.asyncio
async def test_snapshot_is_captured_after_render_declares_owners():
    consumer, view, runtime = await setup()
    runtime._parameter_contracts_active = True
    original = view.render_with_diff

    def render():
        result = original()
        view.choose = None
        return result

    view.render_with_diff = render
    await produce(consumer, "tick")
    assert consumer.send_json.call_args.args[0]["parameter_contracts"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_cancelled_render_retains_lock_until_worker_settles(source):
    consumer, view, _ = await setup()
    started = threading.Event()
    release = threading.Event()
    original = view.render_with_diff

    def render():
        started.set()
        assert release.wait(5), "test did not release the render worker"
        return original()

    view.render_with_diff = render
    task = asyncio.create_task(produce(consumer, source))
    try:
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        assert started.is_set()
        task.cancel()
        await asyncio.sleep(0.01)
        assert consumer._render_lock.locked()
        assert not task.done()
        task.cancel()  # repeated cancellation must not release the render lock
        await asyncio.sleep(0.01)
        assert consumer._render_lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    consumer.send_json.assert_not_awaited()
    assert not consumer._render_lock.locked()
    view.render_with_diff = original
    await produce(consumer, source)
    frame = consumer.send_json.call_args.args[0]
    assert frame["type"] == "html_update"
    assert frame["version"] == 1
    assert "Count: 2" in frame["html"]
    assert view._force_full_html is False


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_replaced_owner_during_render_cannot_deliver(source):
    consumer, view, _ = await setup()
    replacement = BackgroundView()
    replacement.mount(None)
    original = view.render_with_diff

    def render():
        result = original()
        consumer.view_instance = replacement
        return result

    view.render_with_diff = render
    await produce(consumer, source)
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_not_awaited()
    assert consumer._last_sent_version == 0
    assert replacement.count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_replaced_owner_while_waiting_never_runs_result_handler(source):
    consumer, view, _ = await setup()
    replacement = BackgroundView()
    replacement.mount(None)
    async with consumer._render_lock:
        task = asyncio.create_task(produce(consumer, source))
        await asyncio.sleep(0.01)
        assert not task.done()
        consumer.view_instance = replacement
    await task
    assert view.count == replacement.count == 0
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["path", "owner", "import"])
async def test_strict_background_snapshot_requires_its_mounted_runtime(failure, monkeypatch):
    import sys

    consumer, _, runtime = await setup()
    if failure == "path":
        runtime._parameter_contract_view = None
    elif failure == "owner":
        runtime.view_instance = None
    else:
        monkeypatch.setitem(sys.modules, "djust._parameter_metadata", None)
    await produce(consumer, "tick")
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["tick", "async_error"])
async def test_real_debug_error_is_redacted(source, monkeypatch, settings, caplog):
    consumer, _, _ = await setup()
    settings.DEBUG = True
    consumer.send_error = LiveViewConsumer.send_error.__get__(consumer)

    def broken(_view):
        raise ValueError("SECRET_CONTRACT_FAILURE")

    monkeypatch.setattr("djust._parameter_metadata.parameter_contract_manifest", broken)
    await produce(consumer, source)
    frame = consumer.send_json.call_args.args[0]
    assert frame["type"] == "error"
    assert frame["code"] == "render_error"
    assert frame["source"] == "async"
    assert "traceback" not in frame
    assert "SECRET_CONTRACT_FAILURE" not in json.dumps(frame) + caplog.text


@pytest.mark.asyncio
async def test_strict_background_uses_json_envelope_even_with_binary_transport():
    consumer, _, _ = await setup()
    consumer.use_binary = True
    consumer._send_frame = AsyncMock()
    await produce(consumer, "tick")
    consumer._send_frame.assert_not_awaited()
    assert consumer.send_json.call_args.args[0]["parameter_contracts"] is not None


@pytest.mark.asyncio
async def test_failed_background_render_preserves_previous_recovery_pair(monkeypatch):
    consumer, _, _ = await setup()
    await produce(consumer, "tick")
    original = consumer.send_json.call_args.args[0]
    raw_html = consumer._recovery_html
    consumer.send_json.reset_mock()

    def broken(_view):
        raise ValueError("SECRET_CONTRACT_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr("djust._parameter_metadata.parameter_contract_manifest", broken)
        await produce(consumer, "tick")
    consumer.send_json.assert_not_awaited()
    assert consumer._recovery_html == raw_html
    await consumer.handle_request_html({})
    recovered = consumer.send_json.call_args.args[0]
    assert "Count: 1" in recovered["html"]
    assert recovered["version"] == original["version"]
    assert recovered["parameter_contracts"] == original["parameter_contracts"]
