"""Explicit embedded events through the real shared runtime and DB sessions."""

import json

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


class EventChild(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "menu"
    count = state(1, persist="server")
    secret = state("SERVER_SENTINEL", persist="server")
    template = "<div>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 1
        self._service = object()
        self._handler_calls = 0

    def check_permissions(self, request):
        return request.child_allowed

    @event_handler()
    def increment(self, mode: str = "ok"):
        self._handler_calls += 1
        assert self.request.marker == "fresh"
        assert self._service is not None
        if mode == "error":
            raise RuntimeError("HANDLER_SECRET_SENTINEL")
        self.count += 1
        if mode == "scope":
            self._explicit_child_mount_inputs = '{"object_id": 99}'
        elif mode == "permission":
            self.request.child_allowed = False
        elif mode == "object":
            self.count = 99

    def get_object(self):
        return self.count

    def has_object_permission(self, request, obj):
        return obj < 10

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class EventParent(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root>{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_events.EventChild" sticky=True object_id=1 %}</div>'
    )


class TransientEventChild(EventChild):
    count = state(1)
    secret = state("TRANSIENT_SENTINEL")


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_events"]


async def mount(session_key=None, *, allowed=True):
    request = await sync_to_async(make_request)(session_key)
    request.marker = "mount"
    request.child_allowed = True
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        current = await sync_to_async(make_request)(request.session.session_key)
        current.marker = "fresh"
        current.child_allowed = allowed
        return current

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".EventParent", "url": request.path}
        )
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    return runtime, transport, request


async def increment(runtime, mode="ok"):
    await runtime.dispatch_event(
        {"type": "event", "event": "increment", "params": {"view_id": "menu", "mode": mode}}
    )


async def test_child_event_persists_with_fresh_request_without_snapshot_optin():
    runtime, transport, request = await mount()
    child = runtime.view_instance._get_child_view("menu")
    assert not child.enable_state_snapshot
    await increment(runtime)
    assert child.count == 2
    assert not transport.errors, transport.errors
    assert any(frame.get("type") == "embedded_update" for frame in transport.sent)
    assert "SERVER_SENTINEL" not in json.dumps(transport.sent)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 2


@pytest.mark.parametrize("changed", [False, True])
async def test_transient_child_event_checks_identity_without_server_grants(monkeypatch, changed):
    monkeypatch.setattr(
        EventParent, "template", EventParent.template.replace("EventChild", "TransientEventChild")
    )
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    assert not hasattr(child, "_explicit_child_mount_binding")
    if changed:
        child._explicit_child_mount_inputs = '{"object_id": 99}'
    await increment(runtime)
    assert child._handler_calls == (0 if changed else 1)
    assert child.count == (1 if changed else 2)
    assert any(frame.get("type") == "embedded_update" for frame in transport.sent) is not changed


@pytest.mark.parametrize("mutation", ["permission", "inputs", "schema", "registration"])
async def test_child_event_rejects_stale_scope_before_handler(mutation, monkeypatch):
    runtime, transport, request = await mount(allowed=mutation != "permission")
    child = runtime.view_instance._get_child_view("menu")
    if mutation == "inputs":
        child._explicit_child_mount_inputs = '{"object_id": 99}'
    elif mutation == "schema":
        child._explicit_child_schema = "stale"
    elif mutation == "registration":
        child._parent_view = EventParent()
    await increment(runtime)
    assert child.count == 1
    assert child._handler_calls == 0
    assert transport.errors or any(frame.get("type") == "error" for frame in transport.sent)
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 1


@pytest.mark.parametrize("mode", ["error", "scope", "permission", "object", "storage"])
async def test_post_handler_failure_does_not_save_or_send_success(mode, monkeypatch, caplog):
    from djust._exposure_children import ChildStateSession

    runtime, transport, request = await mount()
    if mode == "storage":

        async def unavailable(self, values):
            raise OSError("STORAGE_SECRET_SENTINEL")

        monkeypatch.setattr(ChildStateSession, "asave", unavailable)
    await increment(runtime, mode)
    assert runtime.view_instance._get_child_view("menu")._handler_calls == 1
    assert transport.errors
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)
    assert "SECRET_SENTINEL" not in json.dumps(transport.sent)
    assert "SECRET_SENTINEL" not in caplog.text
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 1


async def test_render_failure_never_discloses_private_exception(monkeypatch, settings, caplog):
    settings.DEBUG = True
    runtime, transport, _ = await mount()

    def broken(self, **kwargs):
        raise RuntimeError("RENDER_SECRET_SENTINEL")

    monkeypatch.setattr(EventChild, "get_context_data", broken)
    await increment(runtime)
    assert "RENDER_SECRET_SENTINEL" not in json.dumps(transport.sent)
    assert "RENDER_SECRET_SENTINEL" not in caplog.text
    assert transport.errors
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)


async def test_child_save_timeout_is_cancelled_without_success_update(monkeypatch):
    import asyncio

    from djust._exposure_children import ChildStateSession

    runtime, transport, _ = await mount()
    cancelled = []

    async def stalled(self, values):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(ChildStateSession, "asave", stalled)
    await asyncio.wait_for(increment(runtime), timeout=2)
    assert cancelled == [True]
    assert transport.errors
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)
