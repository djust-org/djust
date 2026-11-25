"""Callback failures keep their diagnostics without exposing protected values.

Contract (ADR-038 D-a, revised 2026-09-22): in production (``DEBUG=False``) an
explicit owner's callback failure logs value-free. Under ``DEBUG=True`` the
failure logs its exception. Tests without a ``debug`` parameter run under
pytest-django's default ``DEBUG=False``.
"""

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
async def test_callback_diagnostic_destinations(monkeypatch, caplog, debug):
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    owner = SimpleNamespace(exposure_policy="explicit")
    runtime.view_instance = owner
    called = []
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))

    async def fail(*args):
        called.append(True)
        raise ValueError("CALLBACK_DIAGNOSTIC_SENTINEL")

    with override_settings(DEBUG=debug):
        owner._flush_deferred_activity_events = fail
        await runtime._flush_deferred_activity_events()
    assert called == [True]
    assert ("CALLBACK_DIAGNOSTIC_SENTINEL" in caplog.text) == debug
    assert "CALLBACK_DIAGNOSTIC_SENTINEL" not in json.dumps(transport.sent)
    assert tracebacks.get_recent_tracebacks(50) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("route", ["foreground", "deferred"])
async def test_waiter_diagnostic_destinations(caplog, debug, route):
    from asgiref.sync import sync_to_async

    from djust.tests.test_exposure_event_diagnostics import EventFailureView
    from djust.tests.test_exposure_runtime import make_request, mount

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
    assert ("WAITER_DIAGNOSTIC_SENTINEL" in caplog.text) == debug
    assert "WAITER_DIAGNOSTIC_SENTINEL" not in json.dumps(transport.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["waiter", "deferred_flush"])
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
    else:
        owner._flush_deferred_activity_events = async_fail
        await runtime._flush_deferred_activity_events()
    assert len(called) == 1
    assert "Protected" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert diagnostics_allowed(), "Owned callback scope leaked into its caller"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
async def test_component_waiter_failure_is_protected_without_losing_injection(caplog, debug):
    from asgiref.sync import sync_to_async

    from djust.tests.test_exposure_event_diagnostics import EventFailureView
    from djust.tests.test_exposure_runtime import make_request, mount

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
            raise ValueError("COMPONENT_WAITER_SENTINEL")

        owner._notify_waiters = fail
        caplog.clear()
        transport.sent.clear()
        await runtime.dispatch_event(
            {"type": "event", "event": "click", "params": {"component_id": "probe", "value": 7}}
        )
    assert component.received == 7
    # Waiters receive the component's own component_id, not the id the event named (#4046;
    # registered under "probe", the component's own id differs here).
    assert called == [("click", {"component_id": component.component_id, "value": 7})]
    assert ("COMPONENT_WAITER_SENTINEL" in caplog.text) == debug
    assert "COMPONENT_WAITER_SENTINEL" not in json.dumps(transport.sent)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
async def test_sticky_child_waiter_failure_is_protected(settings, caplog, debug):
    from djust.tests.test_exposure_child_events import increment, mount

    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_events"]
    with override_settings(DEBUG=debug):
        runtime, transport, _ = await mount()
        child = runtime.view_instance._get_child_view("menu")
        called = []

        def fail(event_name, params):
            called.append((event_name, params))
            raise ValueError("CHILD_WAITER_SENTINEL")

        child._notify_waiters = fail
        caplog.clear()
        transport.sent.clear()
        await increment(runtime)
    assert called == [("increment", {"mode": "ok"})]
    # The child is explicit, so only DEBUG allows its failure's detail.
    assert ("CHILD_WAITER_SENTINEL" in caplog.text) == debug
    assert "CHILD_WAITER_SENTINEL" not in json.dumps(transport.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("route", ["predicate", "activity"])
async def test_native_mixin_catches_preserve_progress_without_exposing_errors(caplog, debug, route):
    from djust.mixins.activity import ActivityMixin
    from djust.mixins.waiters import WaiterMixin, _Waiter

    called = []
    with override_settings(DEBUG=debug):
        if route == "predicate":
            owner = WaiterMixin()
            owner.exposure_policy = "explicit"

            def fail(params):
                called.append(params)
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
            owner.exposure_policy = "explicit"
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
                    raise ValueError("NATIVE_CALLBACK_SENTINEL")

            await owner._flush_deferred_activity_events(
                SimpleNamespace(_dispatch_single_event=dispatch)
            )
            assert called == [("first", {"value": 7}), ("second", {"value": 8})]
            assert not owner._deferred_activity_events
    assert ("NATIVE_CALLBACK_SENTINEL" in caplog.text) == debug


@pytest.fixture
def debug_on(settings):
    """The owner restriction is visible against a ``DEBUG`` baseline that allows details."""
    settings.DEBUG = True


class _BrokenOwner:
    """An owner whose slot cannot be read, until ``readable`` is set."""

    readable = False

    @property
    def view_instance(self):
        if self.readable:
            return SimpleNamespace(exposure_policy="explicit")
        raise ValueError("OWNER_LOOKUP_SENTINEL")


def test_watched_owner_scope_resets_and_fails_closed_without_inspection(caplog, debug_on):
    from djust._exposure_diagnostics import (
        diagnostic_scope,
        diagnostics_allowed,
        watch_diagnostic_owner,
    )

    root = SimpleNamespace(view_instance=SimpleNamespace(exposure_policy="explicit"))
    with diagnostic_scope():
        watch_diagnostic_owner(root, "view_instance")
        assert diagnostics_allowed()
        with diagnostic_scope():
            watch_diagnostic_owner(_BrokenOwner(), "view_instance")
            assert not diagnostics_allowed()
        assert diagnostics_allowed(), "A nested scope's restriction ends with it"
        flaky = _BrokenOwner()
        watch_diagnostic_owner(flaky, "view_instance")
        assert not diagnostics_allowed()
        flaky.readable = True
        assert not diagnostics_allowed(), "Observed restriction cannot be reversed in a scope"
    assert diagnostics_allowed(), "An exited scope must not retain watched owners"

    with diagnostic_scope():
        watch_diagnostic_owner(_BrokenOwner(), "view_instance")
        assert not diagnostics_allowed()
    assert diagnostics_allowed()
    assert "OWNER_LOOKUP_SENTINEL" not in caplog.text


@pytest.mark.asyncio
async def test_watched_owner_isolation_and_worker_propagation(debug_on):
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
            root = _BrokenOwner()
            root.readable = True
            watch_diagnostic_owner(root, "view_instance")
            entered.set()
            await completed.wait()

            def lose_owner():
                root.readable = False
                return diagnostics_allowed()

            assert await sync_to_async(lose_owner)() is False
            assert not diagnostics_allowed()
        assert diagnostics_allowed()

    async def unrestricted():
        await entered.wait()
        assert diagnostics_allowed()
        with diagnostic_scope():
            root = SimpleNamespace(view_instance=SimpleNamespace(exposure_policy="explicit"))
            watch_diagnostic_owner(root, "view_instance")
            assert await sync_to_async(diagnostics_allowed)()
        completed.set()

    await asyncio.wait_for(asyncio.gather(protected(), unrestricted()), timeout=5)
    assert diagnostics_allowed()
