"""Callback failures retain legacy diagnostics without exposing protected values."""

import json
from collections import deque
from types import SimpleNamespace

import pytest
from django.test import override_settings

from djust import event_handler
from djust.components.base import LiveComponent
from djust.observability import tracebacks
from djust.runtime import ViewRuntime
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport


class CallbackComponent(LiveComponent):
    @event_handler()
    def click(self, value: int):
        self.received = value


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("hook", ["deferred_flush", "time_travel"])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
async def test_callback_diagnostic_destinations(monkeypatch, caplog, debug, hook, initial, final):
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    owner = SimpleNamespace(exposure_policy=initial)
    runtime.view_instance = owner
    called = []
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))

    async def fail(*args):
        called.append(True)
        owner.exposure_policy = final
        raise ValueError("CALLBACK_DIAGNOSTIC_SENTINEL")

    with override_settings(DEBUG=debug):
        if hook == "deferred_flush":
            owner._flush_deferred_activity_events = fail
            await runtime._flush_deferred_activity_events()
        else:
            transport.on_event_recorded = fail
            await runtime._push_tt_event(owner, object())
    assert called == [True]
    assert ("CALLBACK_DIAGNOSTIC_SENTINEL" in caplog.text) == (initial == final == "legacy")
    assert "CALLBACK_DIAGNOSTIC_SENTINEL" not in json.dumps(transport.sent)
    assert tracebacks.get_recent_tracebacks(50) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("route", ["foreground", "deferred"])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
async def test_waiter_diagnostic_destinations(monkeypatch, caplog, debug, route, initial, final):
    from asgiref.sync import sync_to_async

    from djust import LiveView
    from djust.tests.test_exposure_event_diagnostics import EventFailureView
    from djust.tests.test_exposure_runtime import make_request, mount

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(EventFailureView, "exposure_policy", initial)
    request = await sync_to_async(make_request)()
    called = []
    with override_settings(DEBUG=debug):
        runtime, transport = await mount(
            request,
            EventFailureView,
            view="djust.tests.test_exposure_event_diagnostics.EventFailureView",
        )
        owner = runtime.view_instance

        def fail(*args):
            called.append(True)
            owner.exposure_policy = final
            raise ValueError("WAITER_DIAGNOSTIC_SENTINEL")

        owner._notify_waiters = fail
        caplog.clear()
        if route == "foreground":
            await runtime.dispatch_event(
                {"type": "event", "event": "explode", "params": {"stage": "render"}}
            )
        else:
            await runtime._dispatch_single_event(owner, "explode", {"stage": "render"})
    assert called == [True]
    assert ("WAITER_DIAGNOSTIC_SENTINEL" in caplog.text) == (initial == final == "legacy")
    assert "WAITER_DIAGNOSTIC_SENTINEL" not in json.dumps(transport.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["waiter", "time_travel", "deferred_flush"])
@pytest.mark.parametrize("policy", ["explicit", "unknown", None])
async def test_protected_callbacks_do_not_inspect_exceptions_or_metadata(route, policy, caplog):
    from djust._exposure_diagnostics import diagnostics_allowed
    from djust.tests.test_exposure_event_diagnostics import UnprintableFailure

    class UnprintableMetadata:
        def __str__(self):
            raise AssertionError("Protected metadata must not be inspected")

    runtime = ViewRuntime(MockTransport())
    owner = SimpleNamespace(exposure_policy=policy)
    runtime.view_instance = owner
    called = []

    def fail(*args):
        called.append(args)
        owner.exposure_policy = "legacy"
        raise UnprintableFailure()

    async def async_fail(*args):
        fail(*args)

    if route == "waiter":
        owner._notify_waiters = fail
        runtime._notify_waiters_safely(
            owner,
            "click",
            {},
            log_message="Failed %s: %s",
            log_args=(UnprintableMetadata(),),
        )
    elif route == "time_travel":
        runtime.transport.on_event_recorded = async_fail
        await runtime._push_tt_event(owner, object())
    else:
        owner._flush_deferred_activity_events = async_fail
        await runtime._flush_deferred_activity_events()
    assert len(called) == 1
    assert "Protected" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert diagnostics_allowed(), "Owned callback scope leaked into its caller"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["waiter", "time_travel"])
@pytest.mark.parametrize(
    "root_policy,target_policy", [("explicit", "legacy"), ("legacy", "explicit")]
)
async def test_callback_restrictions_include_both_root_and_target(
    route, root_policy, target_policy, caplog
):
    runtime = ViewRuntime(MockTransport())
    runtime.view_instance = SimpleNamespace(exposure_policy=root_policy)
    target = SimpleNamespace(exposure_policy=target_policy)

    def fail(*args):
        runtime.view_instance.exposure_policy = "legacy"
        target.exposure_policy = "legacy"
        raise ValueError("MIXED_OWNER_SENTINEL")

    if route == "waiter":
        target._notify_waiters = fail
        runtime._notify_waiters_safely(
            target, "click", {}, log_message="Failed %s: %s", log_args=("click",)
        )
    else:

        async def async_fail(*args):
            fail(*args)

        runtime.transport.on_event_recorded = async_fail
        await runtime._push_tt_event(target, object())
    assert "Protected" in caplog.text
    assert "MIXED_OWNER_SENTINEL" not in caplog.text


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
async def test_component_waiter_failure_is_protected_without_losing_injection(
    monkeypatch, caplog, debug, initial, final
):
    from asgiref.sync import sync_to_async

    from djust import LiveView
    from djust.tests.test_exposure_event_diagnostics import EventFailureView
    from djust.tests.test_exposure_runtime import make_request, mount

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(EventFailureView, "exposure_policy", initial)
    request = await sync_to_async(make_request)()
    with override_settings(DEBUG=debug):
        runtime, transport = await mount(
            request, EventFailureView, view=EventFailureView.__module__ + ".EventFailureView"
        )
        owner = runtime.view_instance
        component = CallbackComponent()
        owner._components["probe"] = component
        called = []

        def fail(event_name, params):
            called.append((event_name, params))
            owner.exposure_policy = final
            raise ValueError("COMPONENT_WAITER_SENTINEL")

        owner._notify_waiters = fail
        caplog.clear()
        transport.sent.clear()
        await runtime.dispatch_event(
            {"type": "event", "event": "click", "params": {"component_id": "probe", "value": 7}}
        )
    assert component.received == 7
    assert called == [("click", {"component_id": "probe", "value": 7})]
    assert ("COMPONENT_WAITER_SENTINEL" in caplog.text) == (initial == final == "legacy")
    assert "COMPONENT_WAITER_SENTINEL" not in json.dumps(transport.sent)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("final", ["explicit", "legacy"])
async def test_sticky_child_waiter_failure_is_protected(
    monkeypatch, settings, caplog, debug, final
):
    from djust import LiveView
    from djust.tests.test_exposure_child_events import increment, mount

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_events"]
    with override_settings(DEBUG=debug):
        runtime, transport, _ = await mount()
        child = runtime.view_instance._get_child_view("menu")
        called = []

        def fail(event_name, params):
            called.append((event_name, params))
            child.exposure_policy = final
            raise ValueError("CHILD_WAITER_SENTINEL")

        child._notify_waiters = fail
        caplog.clear()
        transport.sent.clear()
        await increment(runtime)
    assert called == [("increment", {"mode": "ok"})]
    assert "CHILD_WAITER_SENTINEL" not in caplog.text
    assert "CHILD_WAITER_SENTINEL" not in json.dumps(transport.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["waiter", "time_travel", "deferred_flush"])
async def test_callback_replacement_cannot_grant_diagnostics(route, caplog):
    runtime = ViewRuntime(MockTransport())
    owner = SimpleNamespace(exposure_policy="legacy")
    runtime.view_instance = owner

    def fail(*args):
        runtime.view_instance = SimpleNamespace(exposure_policy="explicit")
        raise ValueError("REPLACEMENT_CALLBACK_SENTINEL")

    async def async_fail(*args):
        fail(*args)

    if route == "waiter":
        owner._notify_waiters = fail
        runtime._notify_waiters_safely(
            owner, "click", {}, log_message="Failed %s: %s", log_args=("click",)
        )
    elif route == "time_travel":
        runtime.transport.on_event_recorded = async_fail
        await runtime._push_tt_event(owner, object())
    else:
        owner._flush_deferred_activity_events = async_fail
        await runtime._flush_deferred_activity_events()
    assert runtime.view_instance is not owner
    assert "Protected" in caplog.text
    assert "REPLACEMENT_CALLBACK_SENTINEL" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("route", ["predicate", "activity"])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
async def test_native_mixin_catches_preserve_progress_without_exposing_errors(
    caplog, debug, route, initial, final
):
    from djust.mixins.activity import ActivityMixin
    from djust.mixins.waiters import WaiterMixin, _Waiter

    called = []
    with override_settings(DEBUG=debug):
        if route == "predicate":
            owner = WaiterMixin()
            owner.exposure_policy = initial

            def fail(params):
                called.append(params)
                owner.exposure_policy = final
                raise ValueError("NATIVE_CALLBACK_SENTINEL")

            failing = _Waiter("click", predicate=fail)
            succeeding = _Waiter("click")
            owner._waiters["click"] = [failing, succeeding]
            owner._notify_waiters("click", {"value": 7})
            assert not failing.future.done()
            assert succeeding.future.result() == {"value": 7}
            assert owner._waiters["click"] == [failing]
            failing.future.cancel()
            assert called == [{"value": 7}]
        else:
            owner = ActivityMixin()
            owner.exposure_policy = initial
            owner._init_activity()
            owner._register_activity("panel", visible=True, eager=False)
            owner._queue_deferred_activity_event(
                "panel", "first", {"value": 7, "_activity": "panel"}
            )
            owner._queue_deferred_activity_event("panel", "second", {"value": 8})

            async def dispatch(target, event, params):
                assert target is owner
                called.append((event, params))
                if event == "first":
                    owner.exposure_policy = final
                    raise ValueError("NATIVE_CALLBACK_SENTINEL")

            await owner._flush_deferred_activity_events(
                SimpleNamespace(_dispatch_single_event=dispatch)
            )
            assert called == [("first", {"value": 7}), ("second", {"value": 8})]
            assert not owner._deferred_activity_events
    assert ("NATIVE_CALLBACK_SENTINEL" in caplog.text) == (initial == final == "legacy")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["predicate", "activity"])
async def test_native_pass_remembers_successful_policy_restriction(route, caplog):
    from djust._exposure_diagnostics import diagnostics_allowed
    from djust.mixins.activity import ActivityMixin
    from djust.mixins.waiters import WaiterMixin, _Waiter

    called = []
    if route == "predicate":
        owner = WaiterMixin()
        owner.exposure_policy = "legacy"

        def first(params):
            called.append("first")
            owner.exposure_policy = "explicit"
            return False

        def second(params):
            called.append("second")
            owner.exposure_policy = "legacy"
            raise ValueError("LATER_NATIVE_CALLBACK_SENTINEL")

        pending = [_Waiter("click", predicate=first), _Waiter("click", predicate=second)]
        owner._waiters["click"] = pending
        owner._notify_waiters("click", {})
        assert owner._waiters["click"] == pending
        for waiter in pending:
            waiter.future.cancel()
    else:
        owner = ActivityMixin()
        owner.exposure_policy = "legacy"
        owner._init_activity()
        owner._register_activity("panel", visible=True, eager=False)
        owner._queue_deferred_activity_event("panel", "first", {})
        owner._queue_deferred_activity_event("panel", "second", {})

        async def dispatch(target, event, params):
            called.append(event)
            if event == "first":
                owner.exposure_policy = "explicit"
            else:
                owner.exposure_policy = "legacy"
                raise ValueError("LATER_NATIVE_CALLBACK_SENTINEL")

        await owner._flush_deferred_activity_events(
            SimpleNamespace(_dispatch_single_event=dispatch)
        )
        assert not owner._deferred_activity_events
    assert called == ["first", "second"]
    assert "Protected" in caplog.text
    assert "LATER_NATIVE_CALLBACK_SENTINEL" not in caplog.text
    assert diagnostics_allowed(), "Native callback scope must not affect the next caller"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["predicate", "activity"])
@pytest.mark.parametrize("mutation", ["policy", "replacement", "successful_transition"])
async def test_native_child_callback_rechecks_current_root(route, mutation, caplog):
    from djust.mixins.activity import ActivityMixin
    from djust.mixins.waiters import WaiterMixin, _Waiter

    runtime = ViewRuntime(MockTransport())
    root = SimpleNamespace(exposure_policy="legacy")
    runtime.view_instance = root
    called = []

    def transition():
        if mutation == "replacement":
            runtime.view_instance = SimpleNamespace(exposure_policy="explicit")
        else:
            root.exposure_policy = "explicit"

    if route == "predicate":
        child = WaiterMixin()
        child.exposure_policy = "legacy"

        def first(params):
            called.append("first")
            transition()
            if mutation != "successful_transition":
                raise ValueError("ROOT_NATIVE_CALLBACK_SENTINEL")
            return False

        def second(params):
            called.append("second")
            root.exposure_policy = "legacy"
            raise ValueError("ROOT_NATIVE_CALLBACK_SENTINEL")

        pending = [_Waiter("click", predicate=first)]
        if mutation == "successful_transition":
            pending.append(_Waiter("click", predicate=second))
        child._waiters["click"] = pending
        runtime._notify_waiters_safely(
            child, "click", {}, log_message="Failed %s: %s", log_args=("click",)
        )
        for waiter in pending:
            assert not waiter.future.done()
            waiter.future.cancel()
    else:
        child = ActivityMixin()
        child.exposure_policy = "legacy"
        child._init_activity()
        child._register_activity("panel", visible=True, eager=False)
        child._queue_deferred_activity_event("panel", "first", {})
        if mutation == "successful_transition":
            child._queue_deferred_activity_event("panel", "second", {})

        async def dispatch(target, event, params):
            called.append(event)
            if event == "first":
                transition()
                if mutation != "successful_transition":
                    raise ValueError("ROOT_NATIVE_CALLBACK_SENTINEL")
            else:
                root.exposure_policy = "legacy"
                raise ValueError("ROOT_NATIVE_CALLBACK_SENTINEL")

        runtime._dispatch_single_event = dispatch
        await child._flush_deferred_activity_events(runtime)
        assert not child._deferred_activity_events
    assert called == (["first", "second"] if mutation == "successful_transition" else ["first"])
    assert "Protected" in caplog.text
    assert "ROOT_NATIVE_CALLBACK_SENTINEL" not in caplog.text


def test_watched_owner_scope_resets_and_fails_closed_without_inspection(caplog):
    from djust._exposure_diagnostics import (
        diagnostic_scope,
        diagnostics_allowed,
        watch_diagnostic_owner,
    )

    root = SimpleNamespace(view_instance=SimpleNamespace(exposure_policy="legacy"))
    with diagnostic_scope():
        watch_diagnostic_owner(root, "view_instance")
        assert diagnostics_allowed()
        with diagnostic_scope():
            child = SimpleNamespace(view_instance=SimpleNamespace(exposure_policy="explicit"))
            watch_diagnostic_owner(child, "view_instance")
            assert not diagnostics_allowed()
        assert diagnostics_allowed()
        root.view_instance = SimpleNamespace(exposure_policy="explicit")
        assert not diagnostics_allowed()
        root.view_instance = SimpleNamespace(exposure_policy="legacy")
        assert not diagnostics_allowed(), "Observed restriction cannot be reversed in a scope"
    root.view_instance = SimpleNamespace(exposure_policy="explicit")
    assert diagnostics_allowed(), "An exited scope must not retain watched owners"

    class BrokenOwner:
        @property
        def view_instance(self):
            raise ValueError("OWNER_LOOKUP_SENTINEL")

    with diagnostic_scope():
        watch_diagnostic_owner(BrokenOwner(), "view_instance")
        assert not diagnostics_allowed()
    assert diagnostics_allowed()
    assert "OWNER_LOOKUP_SENTINEL" not in caplog.text


@pytest.mark.asyncio
async def test_watched_owner_isolation_and_worker_propagation():
    import asyncio

    from asgiref.sync import sync_to_async

    from djust._exposure_diagnostics import (
        diagnostic_scope,
        diagnostics_allowed,
        watch_diagnostic_owner,
    )

    entered = asyncio.Event()
    completed = asyncio.Event()

    async def protected():
        with diagnostic_scope():
            root = SimpleNamespace(view_instance=SimpleNamespace(exposure_policy="legacy"))
            watch_diagnostic_owner(root, "view_instance")
            entered.set()
            await completed.wait()

            def switch_owner():
                root.view_instance = SimpleNamespace(exposure_policy="explicit")
                return diagnostics_allowed()

            assert await sync_to_async(switch_owner)() is False
            assert not diagnostics_allowed()
        assert diagnostics_allowed()

    async def legacy():
        await entered.wait()
        assert diagnostics_allowed()
        with diagnostic_scope():
            root = SimpleNamespace(view_instance=SimpleNamespace(exposure_policy="legacy"))
            watch_diagnostic_owner(root, "view_instance")
            assert await sync_to_async(diagnostics_allowed)()
        completed.set()

    await asyncio.wait_for(asyncio.gather(protected(), legacy()), timeout=5)
    assert diagnostics_allowed()
