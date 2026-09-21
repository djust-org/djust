"""Strict replay validates before restoration and uses the canonical call plan."""

import asyncio

import pytest
from asgiref.sync import sync_to_async

from djust import event_handler
from djust.tests.test_debug_render_contracts import DebugView, setup
from djust.time_travel import replay_event


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [True, False])
@pytest.mark.parametrize("override", [True, False])
async def test_strict_replay_converts_before_invocation(record, override):
    _, view = await setup()
    snapshot = view._time_travel_buffer.jump(0)
    if not override:
        snapshot.params = {"value": "2"}
    result = await sync_to_async(replay_event)(
        view, snapshot, {"value": "2"} if override else None, record_replay=record
    )
    assert view.count == 9
    assert view._components["part"].value == 5
    assert len(view._time_travel_buffer) == (2 if record else 1)
    if record:
        assert result.error is None
        assert result.params == {"value": "2"}  # no bound call object is persisted
    else:
        assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"value": True},
        {"value": None},
        {"value": "bad"},
        {"value": 2, "extra": 3},
        {"_args": [2], "value": 3},
        {"value": 2, "component": "forged"},
    ],
)
async def test_invalid_strict_replay_does_not_restore_or_invoke(params, monkeypatch):
    called = []

    @event_handler(parameter_policy="strict")
    def advance(self, value: int):
        called.append(value)
        self.count += value

    monkeypatch.setattr(DebugView, "advance", advance)
    _, view = await setup()
    result = await sync_to_async(replay_event)(view, view._time_travel_buffer.jump(0), params)
    assert result is None
    assert called == []
    assert view.count == 42
    assert view._components["part"].value == 99
    assert len(view._time_travel_buffer) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_replay_preserves_positional_and_keyword_only_binding(asynchronous, monkeypatch):
    called = []

    def advance(self, value: int, /, *, scale: int):
        called.append((value, scale))
        self.count += value * scale

    async def advance_async(self, value: int, /, *, scale: int):
        await asyncio.sleep(0)
        advance(self, value, scale=scale)

    handler = event_handler(parameter_policy="strict")(advance_async if asynchronous else advance)
    monkeypatch.setattr(DebugView, "advance", handler)
    _, view = await setup()
    result = await sync_to_async(replay_event)(
        view, view._time_travel_buffer.jump(0), {"_args": ["2"], "scale": "3"}
    )
    assert result.error is None
    assert called == [(2, 3)]
    assert view.count == 13


@pytest.mark.asyncio
async def test_strict_replay_coercion_can_be_disabled(monkeypatch):
    @event_handler(parameter_policy="strict", coerce_types=False)
    def advance(self, value: int):
        self.count += value

    monkeypatch.setattr(DebugView, "advance", advance)
    _, view = await setup()
    snapshot = view._time_travel_buffer.jump(0)
    assert await sync_to_async(replay_event)(view, snapshot, {"value": "2"}) is None
    assert view.count == 42
    assert (await sync_to_async(replay_event)(view, snapshot, {"value": 2})).error is None
    assert view.count == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_legacy_replay_keeps_raw_keyword_values(monkeypatch, asynchronous):
    called = []

    def advance(self, value: int):
        called.append(value)

    async def advance_async(self, value: int):
        await asyncio.sleep(0)
        advance(self, value)

    monkeypatch.setattr(
        DebugView,
        "advance",
        event_handler(parameter_policy="legacy")(advance_async if asynchronous else advance),
    )
    _, view = await setup()
    result = await sync_to_async(replay_event)(
        view, view._time_travel_buffer.jump(0), {"value": "2"}
    )
    assert result.error is None
    assert called == ["2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["strict", "legacy"])
async def test_failed_restore_prevents_replay_handler(monkeypatch, policy):
    called = []

    @event_handler(parameter_policy=policy)
    def advance(self, value: int):
        called.append(value)

    monkeypatch.setattr(DebugView, "advance", advance)
    monkeypatch.setattr("djust.time_travel.restore_snapshot", lambda *args, **kwargs: False)
    _, view = await setup()
    result = await sync_to_async(replay_event)(view, view._time_travel_buffer.jump(0))
    assert result is None
    assert called == []
    assert view.count == 42


@pytest.mark.asyncio
async def test_invalid_strict_declaration_is_redacted_before_restore(monkeypatch, caplog):
    @event_handler(parameter_policy="strict")
    def advance(self, value):
        raise AssertionError("must not invoke")

    monkeypatch.setattr(DebugView, "advance", advance)
    _, view = await setup()
    result = await sync_to_async(replay_event)(
        view, view._time_travel_buffer.jump(0), {"value": "SECRET_INPUT"}
    )
    assert result is None
    assert view.count == 42
    assert "SECRET_INPUT" not in caplog.text


@pytest.mark.asyncio
async def test_consumer_replay_rejects_invalid_input_before_branch_and_render(settings):
    settings.DEBUG = True
    consumer, view = await setup()
    await consumer.handle_forward_replay({"from_index": 0, "override_params": {"value": True}})
    assert view.count == 42
    assert view._time_travel_branch_id == "main"
    assert len(view._time_travel_buffer) == 1
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [None, "legacy"])
async def test_replay_honors_global_policy_and_legacy_override(policy, monkeypatch):
    from djust.config import config

    called = []

    @event_handler(parameter_policy=policy)
    def advance(self, value: int):
        called.append(value)

    monkeypatch.setattr(DebugView, "advance", advance)
    old = config.get("event_parameter_policy", "legacy")
    try:
        config.set("event_parameter_policy", "strict")
        _, view = await setup()
        result = await sync_to_async(replay_event)(
            view, view._time_travel_buffer.jump(0), {"value": "2"}
        )
        assert result.error is None
        assert called == ([2] if policy is None else ["2"])
    finally:
        config.set("event_parameter_policy", old)


@pytest.mark.asyncio
async def test_replay_does_not_record_bound_defaults(monkeypatch):
    @event_handler(parameter_policy="strict")
    def advance(self, value: int, *, label: str = "SERVER_ONLY_DEFAULT"):
        assert label == "SERVER_ONLY_DEFAULT"
        self.count += value

    monkeypatch.setattr(DebugView, "advance", advance)
    _, view = await setup()
    result = await sync_to_async(replay_event)(
        view, view._time_travel_buffer.jump(0), {"value": "2"}
    )
    assert result.error is None
    assert result.params == {"value": "2"}
    assert "SERVER_ONLY_DEFAULT" not in str(view._time_travel_buffer.history())


@pytest.mark.asyncio
async def test_direct_async_calling_context_is_rejected_before_restore(monkeypatch, caplog):
    called = []

    @event_handler(parameter_policy="strict")
    async def advance(self, value: int):
        called.append(value)

    monkeypatch.setattr(DebugView, "advance", advance)
    _, view = await setup()
    assert replay_event(view, view._time_travel_buffer.jump(0)) is None
    assert called == []
    assert view.count == 42
    assert view._components["part"].value == 99
    assert "sync_to_async" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_consumer_waits_for_async_replay_handler(monkeypatch, settings):
    settings.DEBUG = True
    entered, release = asyncio.Event(), asyncio.Event()

    @event_handler(parameter_policy="strict")
    async def advance(self, value: int):
        entered.set()
        await release.wait()
        self.count += value

    monkeypatch.setattr(DebugView, "advance", advance)
    consumer, view = await setup()
    task = asyncio.create_task(
        consumer.handle_forward_replay({"from_index": 0, "override_params": {"value": "2"}})
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.sleep(0.01)
        assert consumer._render_lock.locked()
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert view.count == 9
    assert not consumer._render_lock.locked()
    consumer.send_json.assert_not_awaited()
    consumer.send_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_replay_awaits_async_handler_before_render(monkeypatch, settings):
    settings.DEBUG = True

    @event_handler(parameter_policy="strict")
    async def advance(self, value: int):
        def increment():
            self.count += value

        # A Django-style sync operation inside the async handler must not
        # deadlock the outer sync replay worker and its async bridge.
        await sync_to_async(increment)()

    monkeypatch.setattr(DebugView, "advance", advance)
    consumer, view = await setup(full_html=True)
    await consumer.handle_forward_replay({"from_index": 0, "override_params": {"value": "2"}})
    frame, ack = [call.args[0] for call in consumer.send_json.call_args_list]
    assert view.count == 9
    assert "9 / 5" in frame["html"]
    assert frame["parameter_contracts"] is not None
    assert ack["type"] == "time_travel_state"
