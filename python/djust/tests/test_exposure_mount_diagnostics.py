"""Mount failures must not export undeclared values through diagnostics."""

import asyncio
import json
from collections import deque
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust._exposure_diagnostics import diagnostic_scope, diagnostics_allowed, restrict_diagnostics
from djust.observability import tracebacks
from djust.runtime import ViewRuntime
from djust.security import handle_exception

from .test_exposure_runtime import make_request
from .test_runtime_state_save_tt_1894 import MockTransport


class FailureView(LiveView):
    template = "<div dj-root>ready</div>"
    failure_stage = "mount"
    failure_policy = "explicit"

    def fail_at(self, stage):
        if stage == self.failure_stage:
            self.exposure_policy = self.failure_policy
            raise ValueError("MOUNT_DIAGNOSTIC_SENTINEL")

    def _initialize_temporary_assigns(self):
        self.fail_at("initialize")
        return super()._initialize_temporary_assigns()

    def check_permissions(self, request):
        self.fail_at("auth")
        return True

    def mount(self, request, **kwargs):
        self.fail_at("mount")

    def handle_params(self, params, uri):
        self.fail_at("params")

    def get_template(self):
        self.fail_at("render")
        return super().get_template()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("stage", ["initialize", "auth", "mount", "params", "render"])
@pytest.mark.parametrize(
    "initial_policy,final_policy",
    [
        ("explicit", "explicit"),
        ("legacy", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "legacy"),
    ],
)
async def test_mount_diagnostics_respect_policy_at_entry_and_failure(
    monkeypatch, caplog, debug, stage, initial_policy, final_policy
):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(FailureView, "exposure_policy", initial_policy)
    monkeypatch.setattr(FailureView, "failure_policy", final_policy)
    monkeypatch.setattr(FailureView, "failure_stage", stage)
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=debug):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".FailureView", "url": request.path}
        )
    assert transport.sent and transport.sent[-1]["type"] == "error"
    assert not any(frame.get("type") == "mount" for frame in transport.sent)
    legacy = initial_policy == final_policy == "legacy"
    assert ("MOUNT_DIAGNOSTIC_SENTINEL" in json.dumps(transport.sent)) == (legacy and debug)
    assert ("MOUNT_DIAGNOSTIC_SENTINEL" in caplog.text) == legacy
    assert (
        "MOUNT_DIAGNOSTIC_SENTINEL" in json.dumps(tracebacks.get_recent_tracebacks(50))
    ) == legacy


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("permission", [False, None, 0, 1, "yes", object()])
def test_redacted_error_never_inspects_payload_or_records_traceback(
    monkeypatch, caplog, debug, permission
):
    class UnprintableError(Exception):
        def __str__(self):
            raise AssertionError("exception must not be inspected")

    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))
    with override_settings(DEBUG=debug):
        response = handle_exception(
            UnprintableError(),
            error_type="mount",
            expose_details=permission,
            event_name="EVENT_SENTINEL",
            view_class="VIEW_SENTINEL",
            log_message="MESSAGE_SENTINEL",
            extra={"value": "EXTRA_SENTINEL"},
        )
    assert response == {"type": "error", "error": "Failed to load view. Please refresh the page."}
    assert "SENTINEL" not in caplog.text
    assert tracebacks.get_recent_tracebacks(50) == []
    assert caplog.records[-1].exc_info is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("final_policy", ["explicit", "legacy"])
async def test_actor_mount_failure_rechecks_owner_policy(monkeypatch, caplog, debug, final_policy):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(FailureView, "exposure_policy", "legacy")
    monkeypatch.setattr(FailureView, "failure_policy", final_policy)
    monkeypatch.setattr(FailureView, "failure_stage", "actor")
    monkeypatch.setattr(FailureView, "use_actors", True, raising=False)
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request
    transport.uses_actors_for_mount = lambda view: True
    entered = []

    async def actor_mount(view, data):
        entered.append(view)
        view.fail_at("actor")

    transport.dispatch_actor_mount = actor_mount
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=debug):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".FailureView", "url": request.path}
        )
    assert len(entered) == 1
    assert transport.sent[-1]["type"] == "error"
    legacy = final_policy == "legacy"
    assert ("MOUNT_DIAGNOSTIC_SENTINEL" in json.dumps(transport.sent)) == (legacy and debug)
    assert ("MOUNT_DIAGNOSTIC_SENTINEL" in caplog.text) == legacy
    assert (
        "MOUNT_DIAGNOSTIC_SENTINEL" in json.dumps(tracebacks.get_recent_tracebacks(50))
    ) == legacy


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
async def test_successful_policy_change_cannot_enable_nested_diagnostics(
    monkeypatch, caplog, debug
):
    def change_policy(self, params, uri):
        self.exposure_policy = "legacy"

    monkeypatch.setattr(FailureView, "handle_params", change_policy)
    monkeypatch.delattr(FailureView, "_djust_template_hash_slot", raising=False)
    await test_mount_diagnostics_respect_policy_at_entry_and_failure(
        monkeypatch, caplog, debug, "render", "explicit", "legacy"
    )


def test_diagnostic_scope_is_restrictive_and_resets_after_exception():
    assert diagnostics_allowed()
    with diagnostic_scope():
        restrict_diagnostics(SimpleNamespace(exposure_policy="explicit"))
        assert not diagnostics_allowed()
        with diagnostic_scope():
            restrict_diagnostics(SimpleNamespace(exposure_policy="legacy"))
            assert not diagnostics_allowed()
    assert diagnostics_allowed()
    with pytest.raises(ValueError), diagnostic_scope():
        restrict_diagnostics(SimpleNamespace(exposure_policy=None))
        assert not diagnostics_allowed()
        raise ValueError("scope exit")
    assert diagnostics_allowed()


@pytest.mark.asyncio
async def test_diagnostic_scope_crosses_worker_threads_without_crossing_requests():
    started = asyncio.Event()
    release = asyncio.Event()

    async def protected_request():
        with diagnostic_scope():
            restrict_diagnostics(SimpleNamespace(exposure_policy="explicit"))
            started.set()
            await release.wait()
            assert not await sync_to_async(diagnostics_allowed)()
        assert diagnostics_allowed()

    async def legacy_request():
        await started.wait()
        try:
            with diagnostic_scope():
                restrict_diagnostics(SimpleNamespace(exposure_policy="legacy"))
                assert await sync_to_async(diagnostics_allowed)()
        finally:
            release.set()

    await asyncio.wait_for(asyncio.gather(protected_request(), legacy_request()), timeout=5)
    assert diagnostics_allowed()
