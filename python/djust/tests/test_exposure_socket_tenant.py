"""ADR-038: explicit views work over WebSocket under configured tenancy.

The HTTP request gets ``request.tenant`` from ``TenantMiddleware``. The request
the runtime synthesizes for a WebSocket mount never did. With a tenant resolver
configured, explicit request binding requires resolution to have happened (a
missing ``tenant`` attribute is refused, so tenancy cannot silently disappear),
so every explicit WebSocket mount in a tenant-configured project failed. The
socket request now resolves its tenant the way the middleware does, reading
the handshake's headers for header-based resolvers.
"""

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure_sessions import server_state_adapter
from djust.decorators import state
from djust.websocket import LiveViewConsumer

from .test_exposure_runtime import make_request

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

SEEN = []


class TenantSocketView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self):
        SEEN.append(getattr(self.request, "tenant", "MISSING"))
        self.count += 1


class LegacyTenantSocketView(LiveView):
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self):
        self.count += 1


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    SEEN.clear()


TENANCY = dict(
    DJUST_CONFIG={"TENANT_RESOLVER": "header", "TENANT_REQUIRED": False},
    DJUST_TENANTS=None,
    DEBUG=False,
)


async def _mount(request, view_class, headers):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/", headers=headers)
    socket.scope["session"] = SessionStore(request.session.session_key)
    socket.scope["user"] = AnonymousUser()
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
    )
    return socket, await socket.receive_json_from(timeout=3)


@pytest.mark.parametrize("headers,tenant_id", [([(b"x-tenant-id", b"acme")], "acme"), ([], None)])
async def test_explicit_socket_mount_resolves_the_configured_tenant(headers, tenant_id):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **TENANCY):
        request = await sync_to_async(make_request)()
        socket, frame = await _mount(request, TenantSocketView, headers)
        try:
            assert frame["type"] == "mount", frame
            await socket.send_json_to({"type": "event", "event": "bump", "params": {}})
            reply = await socket.receive_json_from(timeout=3)
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await socket.disconnect()
        tenant = SEEN[0]
        assert (getattr(tenant, "id", None) if tenant is not None else None) == tenant_id

        # The state was saved under the tenant-bound envelope.
        stored = await sync_to_async(make_request)(request.session.session_key)
        stored.tenant = tenant
        view = TenantSocketView()
        adapter = await sync_to_async(server_state_adapter)(view, stored)
        assert await adapter.aload() == {"count": 1}


async def test_legacy_socket_mount_is_unchanged_under_tenancy():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **TENANCY):
        request = await sync_to_async(make_request)()
        socket, frame = await _mount(request, LegacyTenantSocketView, [(b"x-tenant-id", b"acme")])
        try:
            assert frame["type"] == "mount", frame
            await socket.send_json_to({"type": "event", "event": "bump", "params": {}})
            assert (await socket.receive_json_from(timeout=3))["type"] in {"patch", "html_update"}
        finally:
            await socket.disconnect()
