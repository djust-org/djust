"""Recovery serves metadata from its HTML render, never a later owner state."""

import asyncio
import copy
import gc
import threading
import weakref
from unittest.mock import AsyncMock, Mock

import pytest

from djust.runtime import ViewRuntime, WSConsumerTransport
from djust.tests.test_parameter_metadata import LegacyOwner, StrictOwner
from djust.websocket import LiveViewConsumer


class RecoveryOwner(StrictOwner):
    _strip_comments_and_whitespace = staticmethod(lambda html: html)
    _extract_liveview_content = staticmethod(lambda html: html)


def setup():
    consumer = LiveViewConsumer()
    consumer.view_instance = RecoveryOwner()
    consumer.send_json = AsyncMock()
    consumer.send_error = AsyncMock()
    transport = WSConsumerTransport(consumer)
    runtime = ViewRuntime(transport)
    runtime.view_instance = consumer.view_instance
    runtime._parameter_contract_view = "app.Page"
    consumer._runtime = runtime
    return consumer, runtime, transport


async def rendered(consumer, runtime, transport):
    frame = {
        "type": "html_update",
        "html": "<span>original</span>",
        "version": transport.next_client_version("<span>original</span>", 1),
    }
    await runtime._send_render_frame(frame)
    consumer.send_json.reset_mock()
    return frame


@pytest.mark.asyncio
async def test_cached_recovery_keeps_detached_render_snapshot(monkeypatch):
    consumer, runtime, transport = setup()
    sent = await rendered(consumer, runtime, transport)
    consumer.view_instance.choose = LegacyOwner().choose
    original = copy.deepcopy(sent["parameter_contracts"])
    sent["parameter_contracts"]["owners"][0]["handlers"].clear()
    sent["parameter_contracts"] = None

    def forbidden(_view):
        raise AssertionError("Cached recovery must not inspect a newer owner")

    monkeypatch.setattr("djust._parameter_metadata.parameter_contract_manifest", forbidden)
    await consumer.handle_request_html({})
    recovered = consumer.send_json.call_args.args[0]
    assert recovered["html"] == "<span>original</span>"
    assert recovered["parameter_contracts"] == original
    assert recovered["parameter_contract_view"] == "app.Page"
    assert recovered["version"] == sent["version"]
    assert consumer._recovery_html is None


@pytest.mark.asyncio
async def test_recovery_rejects_a_replaced_owner():
    consumer, runtime, transport = setup()
    await rendered(consumer, runtime, transport)
    consumer.view_instance = RecoveryOwner()
    await consumer.handle_request_html({})
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited()


@pytest.mark.asyncio
async def test_recovery_waits_for_render_lock_and_rechecks_owner():
    consumer, runtime, transport = setup()
    await rendered(consumer, runtime, transport)
    async with consumer._render_lock:
        task = asyncio.create_task(consumer.handle_request_html({}))
        await asyncio.sleep(0)
        sent_while_locked = consumer.send_json.await_count
        consumer.view_instance = RecoveryOwner()
    await task
    assert not sent_while_locked
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited()
    assert not consumer._render_lock.locked()


@pytest.mark.asyncio
async def test_uncaptured_strict_recovery_fails_closed():
    consumer, runtime, transport = setup()
    # A producer that has not supplied render-bound metadata cannot fabricate
    # it later from whatever declarations happen to exist at recovery time.
    transport.next_client_version("<span>uncaptured</span>", 1)
    await consumer.handle_request_html({})
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited()


@pytest.mark.asyncio
async def test_rearming_cannot_reuse_previous_render_contracts():
    consumer, runtime, transport = setup()
    await rendered(consumer, runtime, transport)
    transport.next_client_version("<span>different</span>", 2)
    await consumer.handle_request_html({})
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited()


@pytest.mark.asyncio
async def test_cancelled_recovery_preserves_snapshot_and_releases_lock():
    consumer, runtime, transport = setup()
    sent = await rendered(consumer, runtime, transport)
    async with consumer._render_lock:
        task = asyncio.create_task(consumer.handle_request_html({}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not consumer._render_lock.locked()
    consumer.send_json.assert_not_awaited()
    await consumer.handle_request_html({})
    assert (
        consumer.send_json.call_args.args[0]["parameter_contracts"] == sent["parameter_contracts"]
    )


@pytest.mark.asyncio
async def test_fresh_sticky_render_captures_new_contract_and_preserves_wire_version():
    consumer, runtime, transport = setup()
    sent = await rendered(consumer, runtime, transport)
    consumer._has_live_sticky_children = Mock(return_value=True)

    def render():
        consumer.view_instance.choose = LegacyOwner().choose
        return "<span>fresh</span>", [], 999

    consumer.view_instance.render_with_diff = render
    await consumer.handle_request_html({})
    recovered = consumer.send_json.call_args.args[0]
    assert recovered == {
        "type": "html_recovery",
        "html": "<span>fresh</span>",
        "version": sent["version"],
        "parameter_contracts": None,
        "parameter_contract_view": "app.Page",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["render", "contracts"])
async def test_fresh_failure_uses_matching_cached_snapshot(failure, monkeypatch, caplog):
    consumer, runtime, transport = setup()
    sent = await rendered(consumer, runtime, transport)
    consumer._has_live_sticky_children = Mock(return_value=True)
    consumer.view_instance.render_with_diff = Mock(return_value=("<span>fresh</span>", [], 999))
    error = RuntimeError("SECRET_RENDER_FAILURE")
    if failure == "render":
        consumer.view_instance.render_with_diff.side_effect = error
    else:
        monkeypatch.setattr(
            "djust._parameter_metadata.parameter_contract_manifest", Mock(side_effect=error)
        )
    await consumer.handle_request_html({})
    recovered = consumer.send_json.call_args.args[0]
    assert recovered["html"] == "<span>original</span>"
    assert recovered["parameter_contracts"] == sent["parameter_contracts"]
    assert "SECRET_RENDER_FAILURE" not in caplog.text
    assert "SECRET_RENDER_FAILURE" not in repr(recovered)


@pytest.mark.asyncio
async def test_invalid_uncaptured_contract_is_redacted(monkeypatch, caplog):
    consumer, runtime, transport = setup()
    transport.next_client_version("<span>uncaptured</span>", 1)
    monkeypatch.setattr(
        "djust._parameter_metadata.parameter_contract_manifest",
        Mock(side_effect=RuntimeError("SECRET_CONTRACT_FAILURE")),
    )
    await consumer.handle_request_html({})
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited_once()
    assert "SECRET_CONTRACT_FAILURE" not in repr(consumer.send_error.call_args)
    assert "SECRET_CONTRACT_FAILURE" not in caplog.text


@pytest.mark.asyncio
async def test_legacy_recovery_keeps_existing_wire_shape():
    consumer, runtime, transport = setup()
    consumer.view_instance.choose = LegacyOwner().choose
    sent = await rendered(consumer, runtime, transport)
    await consumer.handle_request_html({})
    assert consumer.send_json.call_args.args[0] == {
        "type": "html_recovery",
        "html": "<span>original</span>",
        "version": sent["version"],
    }


@pytest.mark.asyncio
async def test_embedded_frame_cannot_overwrite_parent_recovery_contract():
    consumer, runtime, transport = setup()
    sent = await rendered(consumer, runtime, transport)
    await transport.send(
        {
            "type": "embedded_update",
            "version": sent["version"],
            "parameter_contracts": None,
            "parameter_contract_view": "app.Page",
        }
    )
    consumer.send_json.reset_mock()
    await consumer.handle_request_html({})
    assert (
        consumer.send_json.call_args.args[0]["parameter_contracts"] == sent["parameter_contracts"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_render", [False, True])
async def test_cancellation_does_not_unlock_a_running_render(fail_render):
    consumer, runtime, transport = setup()
    await rendered(consumer, runtime, transport)
    consumer._has_live_sticky_children = Mock(return_value=True)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finish = threading.Event()

    def render():
        loop.call_soon_threadsafe(started.set)
        assert finish.wait(timeout=5)
        if fail_render:
            raise RuntimeError("SECRET_CANCELLED_RENDER")
        return "<span>fresh</span>", [], 999

    consumer.view_instance.render_with_diff = render
    task = asyncio.create_task(consumer.handle_request_html({}))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        locked_during_render = consumer._render_lock.locked()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert locked_during_render
    assert not consumer._render_lock.locked()
    consumer.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_snapshot_does_not_retain_removed_owner():
    consumer, runtime, transport = setup()
    await rendered(consumer, runtime, transport)
    owner = weakref.ref(consumer.view_instance)
    consumer.view_instance = runtime.view_instance = None
    gc.collect()
    assert owner() is None
    assert consumer._recovery_contracts is not None


@pytest.mark.asyncio
async def test_old_frame_cannot_replace_new_recovery_contracts():
    consumer, runtime, transport = setup()
    old = await rendered(consumer, runtime, transport)
    current = await rendered(consumer, runtime, transport)
    old["parameter_contracts"] = None
    await transport.send(old)
    consumer.send_json.reset_mock()
    await consumer.handle_request_html({})
    recovered = consumer.send_json.call_args.args[0]
    assert recovered["version"] == current["version"]
    assert recovered["parameter_contracts"] == current["parameter_contracts"]


@pytest.mark.asyncio
async def test_cached_explicit_clear_does_not_rediscover_contracts(monkeypatch):
    consumer, runtime, transport = setup()
    await rendered(consumer, runtime, transport)
    consumer.view_instance.choose = LegacyOwner().choose
    await rendered(consumer, runtime, transport)
    discovery = Mock(side_effect=AssertionError("must not rediscover cached metadata"))
    monkeypatch.setattr("djust._parameter_metadata.parameter_contract_manifest", discovery)
    await consumer.handle_request_html({})
    discovery.assert_not_called()
    recovered = consumer.send_json.call_args.args[0]
    assert recovered["parameter_contracts"] is None
    assert recovered["parameter_contract_view"] == "app.Page"


@pytest.mark.asyncio
async def test_first_strict_recovery_requires_later_explicit_clear():
    consumer, runtime, transport = setup()
    consumer.view_instance.choose = LegacyOwner().choose
    await rendered(consumer, runtime, transport)
    consumer._has_live_sticky_children = Mock(return_value=True)

    def render():
        consumer.view_instance.choose = StrictOwner().choose
        return "<span>new strict owner</span>", [], 999

    consumer.view_instance.render_with_diff = render
    await consumer.handle_request_html({})
    assert consumer.send_json.call_args.args[0]["parameter_contracts"] is not None
    consumer.view_instance.choose = LegacyOwner().choose
    frame = await rendered(consumer, runtime, transport)
    assert "parameter_contracts" in frame
    assert frame["parameter_contracts"] is None
