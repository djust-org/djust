"""Explicit runtime persistence using real mount/event dispatch and DB sessions.

Only the staged construction gate is bypassed. Transport records actual frames;
authorization, rendering, request binding and persistence use production code.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust._exposure_sessions import server_state_adapter
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


class RuntimeView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")
    hidden = state("SERVER_SENTINEL", persist="server")

    def mount(self, request, **kwargs):
        self.count = 5
        self.service = object()
        self.public_note = "PUBLIC_SENTINEL"
        self._private_note = "PRIVATE_SENTINEL"

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, render_only="RENDER_SENTINEL", **kwargs)

    @event_handler()
    def increment(self):
        assert self.service is not None
        self.count += 1


def make_request(session_key=None):
    request = RequestFactory().get("/runtime-explicit/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(session_key)
    if session_key is None:
        request.session.create()
    return request


async def mount(request, **extra):
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".RuntimeView", "url": request.path, **extra}
        )
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    assert any(frame.get("type") == "mount" for frame in transport.sent), transport.sent
    return runtime, transport


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    # Preserve the production deadline; DB setup is complete before event save.


async def test_event_persists_without_client_snapshot_opt_in_and_reconnect_restores(staged):
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    assert runtime.view_instance.count == 5
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert runtime.view_instance.count == 6
    adapter = await sync_to_async(server_state_adapter)(runtime.view_instance, request)
    assert await adapter.aload() == {"count": 6, "hidden": "SERVER_SENTINEL"}
    stored = await request.session.aload()
    assert "liveview_/runtime-explicit/" not in stored
    assert "RENDER_SENTINEL" not in json.dumps(stored)
    assert "PRIVATE_SENTINEL" not in json.dumps(stored)
    assert "SENTINEL" not in json.dumps(transport.sent)

    second_request = await sync_to_async(make_request)(request.session.session_key)
    second, second_transport = await mount(second_request)
    assert second.view_instance.count == 6
    await second.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert second.view_instance.count == 7
    assert "SENTINEL" not in json.dumps(second_transport.sent)
    # SessionStore caches reads; prove durability with a fresh request/runtime.
    third_request = await sync_to_async(make_request)(request.session.session_key)
    third, _ = await mount(third_request)
    assert third.view_instance.count == 7


async def test_explicit_mount_never_consumes_or_emits_legacy_signed_snapshots(staged, monkeypatch):
    from djust.security import sign_snapshot

    monkeypatch.setattr(RuntimeView, "enable_state_snapshot", True)
    request = await sync_to_async(make_request)()
    slug = __name__ + ".RuntimeView"
    token = sign_snapshot(
        json.dumps({"count": 90, "public_note": "RESTORE_SENTINEL"}),
        slug,
        request.session.session_key,
    )
    runtime, transport = await mount(
        request, state_snapshot={"view_slug": slug, "state_json": token}
    )
    assert runtime.view_instance.count == 5
    assert "SENTINEL" not in json.dumps(transport.sent)
    assert not any("state_snapshot_signed" in frame for frame in transport.sent)


@pytest.mark.parametrize("mutation", ["schema", "extra", "legacy"])
async def test_reconnect_rejects_invalid_envelopes_before_assignment(staged, mutation):
    request = await sync_to_async(make_request)()
    runtime, _ = await mount(request)
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    adapter = await sync_to_async(server_state_adapter)(runtime.view_instance, request)

    def corrupt():
        session = SessionStore(request.session.session_key)
        payload = session[adapter.key]
        payload["state"]["values"]["count"] = 90
        if mutation == "schema":
            payload["state"]["schema"] = "old"
        elif mutation == "extra":
            payload["state"]["values"]["public_note"] = "RESTORE_SENTINEL"
        else:
            del session[adapter.key]
            session["liveview_/runtime-explicit/"] = {"count": 90}
            session["liveview_/runtime-explicit/__private"] = {"_private_note": "RESTORE_SENTINEL"}
        session.save()

    await sync_to_async(corrupt)()
    fresh = await sync_to_async(make_request)(request.session.session_key)
    second, transport = await mount(fresh)
    assert second.view_instance.count == 5
    assert second.view_instance.public_note == "PUBLIC_SENTINEL"
    assert second.view_instance._private_note == "PRIVATE_SENTINEL"
    assert "SENTINEL" not in json.dumps(transport.sent)


async def test_denied_reconnect_does_not_restore_or_write_state(staged, monkeypatch):
    request = await sync_to_async(make_request)()
    runtime, _ = await mount(request)
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    before = await request.session.aload()
    monkeypatch.setattr(
        RuntimeView, "check_permissions", lambda self, request: False, raising=False
    )
    fresh = await sync_to_async(make_request)(request.session.session_key)
    transport = MockTransport()
    transport.build_request = lambda: fresh
    second = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await second.dispatch_mount(
            {"type": "mount", "view": __name__ + ".RuntimeView", "url": fresh.path}
        )
    # An anonymous denied user is redirected by the production auth sequence.
    assert transport.sent == [{"type": "navigate", "to": "/accounts/login/"}]
    assert not any(frame.get("type") == "mount" for frame in transport.sent)
    assert await fresh.session.aload() == before
