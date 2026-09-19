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
from djust.tenants.mixin import TenantMixin
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


class TenantRuntimeView(TenantMixin, RuntimeView):
    count = state(0, persist="server")
    hidden = state("SERVER_SENTINEL", persist="server")

    def check_permissions(self, request):
        from djust.tenants.middleware import get_current_tenant

        current = get_current_tenant()
        return current is not None and current.id == request.tenant.id == self.tenant.id


def make_request(session_key=None):
    request = RequestFactory().get("/runtime-explicit/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(session_key)
    if session_key is None:
        request.session.create()
    return request


async def mount(request, view_class=RuntimeView, **extra):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": __name__ + "." + view_class.__name__,
                "url": request.path,
                **extra,
            }
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
    persisted_request = await sync_to_async(make_request)(request.session.session_key)
    adapter = await sync_to_async(server_state_adapter)(runtime.view_instance, persisted_request)
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


@pytest.mark.parametrize("failure", ["denied", "exception", "user", "tenant", "missing"])
async def test_explicit_events_fail_closed_on_fresh_auth_failure(staged, monkeypatch, failure):
    from types import SimpleNamespace
    from djust.tenants.resolvers import TenantInfo

    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    view = runtime.view_instance
    before = await request.session.aload()
    fresh = await sync_to_async(make_request)(request.session.session_key)
    if failure == "denied":
        monkeypatch.setattr(
            RuntimeView, "check_permissions", lambda self, request: False, raising=False
        )
    elif failure == "exception":

        def broken(self, request):
            raise RuntimeError("AUTH_SECRET_SENTINEL")

        monkeypatch.setattr(RuntimeView, "check_permissions", broken, raising=False)
    elif failure == "user":
        fresh.user = SimpleNamespace(is_authenticated=True, pk=123)
    elif failure == "tenant":
        fresh.tenant = TenantInfo("other")

    async def current(view):
        return None if failure == "missing" else fresh

    transport.explicit_event_request = current
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert view.count == 5
    assert runtime.view_instance is None
    assert transport.closed_with == 4403
    assert await request.session.aload() == before
    assert "AUTH_SECRET_SENTINEL" not in json.dumps(transport.sent)


@pytest.mark.parametrize("revocation", ["logout", "inactive", "password"])
async def test_socket_adapter_reloads_auth_instead_of_mount_cache(staged, revocation):
    from django.contrib.auth import get_user_model, login
    from types import SimpleNamespace
    from djust.runtime import WSConsumerTransport

    def authenticated_request():
        request = make_request()
        user = get_user_model().objects.create_user(username="socket-user", password="original")
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.save()
        return request

    request = await sync_to_async(authenticated_request)()
    runtime, transport = await mount(request)
    view = runtime.view_instance
    adapter = WSConsumerTransport(SimpleNamespace())
    transport.explicit_event_request = adapter.explicit_event_request
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert view.count == 6

    def revoke():
        if revocation == "logout":
            SessionStore(request.session.session_key).delete()
        elif revocation == "inactive":
            get_user_model().objects.filter(pk=request.user.pk).update(is_active=False)
        else:
            user = get_user_model().objects.get(pk=request.user.pk)
            user.set_password("changed")
            user.save()

    await sync_to_async(revoke)()
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert view.count == 6
    assert runtime.view_instance is None
    assert transport.closed_with == 4403


async def test_sse_adapter_uses_current_post_request_and_view_route_for_save(staged):
    from types import SimpleNamespace
    from djust.runtime import SSESessionTransport

    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    post = await sync_to_async(make_request)(request.session.session_key)
    post.path = "/djust/sse/event/"
    post.path_info = post.path
    adapter = SSESessionTransport(SimpleNamespace(_event_request=post))
    transport.explicit_event_request = adapter.explicit_event_request
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert runtime.view_instance.count == 6
    assert runtime.view_instance.request.session is post.session
    assert runtime.view_instance.request.path == "/runtime-explicit/"
    assert post.path == "/djust/sse/event/"
    fresh = await sync_to_async(make_request)(request.session.session_key)
    second, _ = await mount(fresh)
    assert second.view_instance.count == 6


@pytest.mark.parametrize("tenant_enabled", [False, True])
async def test_real_websocket_persists_reconnects_and_refuses_deleted_session(
    staged, tenant_enabled
):
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    if tenant_enabled:
        await request.session.aset("tenant_id", "alpha")
        await request.session.asave()
    view_name = "TenantRuntimeView" if tenant_enabled else "RuntimeView"

    async def connect():
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        comm.scope["session"] = SessionStore(request.session.session_key)
        comm.scope["user"] = AnonymousUser()
        connected, _ = await comm.connect()
        assert connected
        await comm.receive_json_from(timeout=5)
        await comm.send_json_to(
            {"type": "mount", "view": __name__ + "." + view_name, "url": request.path}
        )
        frame = await comm.receive_json_from(timeout=5)
        assert frame["type"] == "mount", frame
        assert "SENTINEL" not in json.dumps(frame)
        return comm, frame

    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        DEBUG=False,
        DJUST_CONFIG={"TENANT_RESOLVER": "session", "TENANT_REQUIRED": True}
        if tenant_enabled
        else {},
        DJUST_TENANTS={},
    ):
        comm, frame = await connect()
        try:
            assert ">5<" in frame["html"]
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] in {"patch", "html_update"}, reply
            assert "SENTINEL" not in json.dumps(reply)
        finally:
            await comm.disconnect()

        comm, frame = await connect()
        try:
            assert ">6<" in frame["html"]
            await sync_to_async(SessionStore(request.session.session_key).delete)()
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] == "error", reply
            assert "SENTINEL" not in json.dumps(reply)
            closed = await comm.receive_output(timeout=5)
            assert closed == {"type": "websocket.close", "code": 4403}
        finally:
            await comm.disconnect()


async def test_concurrent_sse_turns_keep_their_own_auth_request(staged, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from djust.runtime import SSESessionTransport

    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request)
    session = SimpleNamespace(_event_request=None)
    adapter = SSESessionTransport(session)
    captured = [asyncio.Event(), asyncio.Event()]
    seen = []

    def check(self, current):
        seen.append(current.META["event_marker"])
        return True

    monkeypatch.setattr(RuntimeView, "check_permissions", check, raising=False)

    async def fresh(view):
        current = await adapter.explicit_event_request(view)
        captured[current.META["event_marker"]].set()
        return current

    transport.explicit_event_request = fresh
    await runtime._explicit_event_lock.acquire()
    tasks = []
    try:
        for marker in range(2):
            post = await sync_to_async(make_request)(request.session.session_key)
            post.META["event_marker"] = marker
            session._event_request = post
            tasks.append(
                asyncio.create_task(
                    runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
                )
            )
            await asyncio.wait_for(captured[marker].wait(), timeout=5)
    finally:
        runtime._explicit_event_lock.release()
    await asyncio.gather(*tasks)
    assert seen == [0, 1]
    assert runtime.view_instance.count == 7
    assert session._event_request is None
    assert "_djust_event_request" not in runtime.view_instance.__dict__


async def test_tenant_mixin_http_and_runtime_use_same_authorized_tenant(staged):
    from djust.tenants.middleware import get_current_tenant

    request = await sync_to_async(make_request)()
    await request.session.aset("tenant_id", "alpha")
    await request.session.asave()
    with override_settings(DJUST_CONFIG={"TENANT_RESOLVER": "session", "TENANT_REQUIRED": True}):
        response = await sync_to_async(TenantRuntimeView.as_view())(request)
        assert response.status_code == 200
        assert request.tenant.id == "alpha"
        assert get_current_tenant() is None
        fresh = await sync_to_async(make_request)(request.session.session_key)
        runtime, transport = await mount(fresh, view_class=TenantRuntimeView)
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
        assert runtime.view_instance is not None, transport.sent
        assert runtime.view_instance.count == 6
        assert runtime.view_instance.request.tenant.id == "alpha"
        assert get_current_tenant() is None


async def test_tenant_change_refuses_old_runtime_and_http_remounts(staged):
    from djust.tenants.middleware import get_current_tenant

    request = await sync_to_async(make_request)()
    await request.session.aset("tenant_id", "alpha")
    await request.session.asave()
    with override_settings(DJUST_CONFIG={"TENANT_RESOLVER": "session", "TENANT_REQUIRED": True}):
        runtime, transport = await mount(request, view_class=TenantRuntimeView)
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
        view = runtime.view_instance
        assert view.count == 6
        session = SessionStore(request.session.session_key)
        await session.aset("tenant_id", "beta")
        await session.asave()
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
        assert runtime.view_instance is None
        assert view.count == 6
        assert transport.closed_with == 4403
        assert get_current_tenant() is None

        def post():
            current = RequestFactory().post(
                request.path, {"event": "increment", "params": {}}, content_type="application/json"
            )
            current.user = AnonymousUser()
            current.session = SessionStore(request.session.session_key)
            response = TenantRuntimeView.as_view()(current)
            assert response.status_code == 200, response.content
            assert current.tenant.id == "beta"
            return server_state_adapter(TenantRuntimeView(), current).load()

        # Alpha's 6 is not hydrated: fresh beta mount starts at 5, then increments.
        assert (await sync_to_async(post)())["count"] == 6
        assert (await sync_to_async(post)())["count"] == 7
        assert get_current_tenant() is None


async def test_required_tenant_failure_restores_callers_context(staged):
    from django.http import Http404
    from djust.tenants.middleware import get_current_tenant, tenant_context
    from djust.tenants.resolvers import TenantInfo

    request = await sync_to_async(make_request)()
    outer = TenantInfo("outer")
    with (
        override_settings(
            DJUST_CONFIG={"TENANT_RESOLVER": "session", "TENANT_REQUIRED": True},
            LIVEVIEW_ALLOWED_MODULES=[__name__],
            DEBUG=False,
        ),
        tenant_context(outer),
    ):
        with pytest.raises(Http404):
            await sync_to_async(TenantRuntimeView.as_view())(request)
        assert get_current_tenant() is outer
        transport = MockTransport()
        transport.build_request = lambda: request
        runtime = ViewRuntime(transport)
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".TenantRuntimeView", "url": request.path}
        )
        assert any(frame.get("type") == "error" for frame in transport.sent)
        assert not any(frame.get("type") == "mount" for frame in transport.sent)
        assert get_current_tenant() is outer
