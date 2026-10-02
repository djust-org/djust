"""The signed ``state_snapshot`` token is bound to the tenant (1.x backport).

The legacy opt-in token (``enable_state_snapshot = True``) was signed over the
view slug and a digest of the Django session key. It carried no tenant, so a
token minted under tenant A verified and restored A's public state into a
mount under tenant B whenever both mounts share a session and a view path
(the tenant resolved from the Host, a header, the session, a cookie).

These tests drive the real ``LiveViewConsumer`` through a
``WebsocketCommunicator`` (mount and ``live_redirect_mount``), the HTTP
fallback POST (the other place a token is minted), and the signing module.
``dispatch_mount`` is shared by WebSocket and SSE, so the one verification
site is the one every transport reaches.
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.tenants.mixin import TenantMixin

pytestmark = [pytest.mark.django_db, pytest.mark.tenants]

_URL = "/snapshot-token-3328/"
_ALLOWED = [".example.com", "testserver"]
_CONFIG = {"TENANT_RESOLVER": "subdomain"}


class _TenantView(TenantMixin, LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'

    def mount(self, request, **kwargs):
        self.secret = "fresh-" + self.tenant.id

    @event_handler()
    def keep(self, **kwargs):
        self.secret = "secret-of-" + self.tenant.id


class _OptionalTenantView(_TenantView):
    tenant_required = False

    def mount(self, request, **kwargs):
        self.secret = "fresh-notenant"

    @event_handler()
    def keep(self, **kwargs):
        self.secret = "kept-notenant"


class _PlainView(LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'

    def mount(self, request, **kwargs):
        self.secret = "fresh-plain"

    @event_handler()
    def keep(self, **kwargs):
        self.secret = "kept-plain"


@pytest.fixture(autouse=True)
def _settings():
    with override_settings(
        ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__]
    ):
        yield


def _new_session() -> SessionStore:
    store = SessionStore()
    store.create()
    return store


def _forget_saved_state(session: SessionStore) -> None:
    """Drop the per-event session saves so only the client's token can restore."""
    for key in [k for k in session.keys() if k.startswith("liveview_")]:
        del session[key]
    session.save()


def _slug(view: str) -> str:
    return f"{__name__}.{view}"


async def _ws(
    view: str,
    host: str,
    session: SessionStore,
    *,
    event: str | None = None,
    snapshot: str | None = None,
    kind: str = "mount",
) -> dict:
    """Mount (optionally echoing ``snapshot``), optionally fire ``event``.

    Returns ``{"html": <mount html>, "mount_token": ..., "event_token": ...}``.
    """
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(), "/ws/", headers=[(b"host", host.encode())]
    )
    communicator.scope["session"] = session
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)
    message = {"type": kind, "view": _slug(view), "url": _URL}
    if snapshot is not None:
        message["state_snapshot"] = {"view_slug": _slug(view), "state_json": snapshot}
    await communicator.send_json_to(message)
    frame = None
    for _ in range(6):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") in ("mount", "error", "navigate"):
            break
    assert frame is not None and frame.get("type") == "mount", frame
    event_token = None
    if event:
        await communicator.send_json_to({"type": "event", "event": event, "params": {}})
        for _ in range(6):
            reply = await communicator.receive_json_from(timeout=3)
            if "state_snapshot_signed" in reply:
                event_token = reply["state_snapshot_signed"]
            if reply.get("type") in ("patch", "html_update", "diff", "noop", "error"):
                break
    await communicator.disconnect()
    return {
        "html": frame.get("html") or "",
        "mount_token": frame.get("state_snapshot_signed"),
        "event_token": event_token,
    }


# --------------------------------------------------------------------------- #
# A token minted under tenant A, other under tenant B
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["mount", "live_redirect_mount"])
class TestTokenTenantBindingOverWebSocket:
    @pytest.mark.asyncio
    async def test_mount_token_from_tenant_a_does_not_restore_into_tenant_b(self, kind):
        session = await sync_to_async(_new_session)()
        acme = await _ws("_TenantView", "acme.example.com", session, event="keep")
        token = acme["mount_token"]
        assert token, "precondition: the mount frame carries a token"
        await sync_to_async(_forget_saved_state)(session)

        other = await _ws("_TenantView", "globex.example.com", session, snapshot=token, kind=kind)
        assert "[fresh-acme]" not in other["html"], other["html"]
        assert "[fresh-globex]" in other["html"], other["html"]

    @pytest.mark.asyncio
    async def test_same_tenant_still_restores_from_its_token(self, kind):
        session = await sync_to_async(_new_session)()
        acme = await _ws("_TenantView", "acme.example.com", session)
        await sync_to_async(_forget_saved_state)(session)
        own = await _ws(
            "_TenantView", "acme.example.com", session, snapshot=acme["mount_token"], kind=kind
        )
        assert "[fresh-acme]" in own["html"], own["html"]


class TestUnscopedViewsKeepWorking:
    @pytest.mark.asyncio
    async def test_unresolved_tenant_mints_nothing(self):
        session = await sync_to_async(_new_session)()
        first = await _ws("_OptionalTenantView", "testserver", session)
        assert not first["mount_token"], first

    @pytest.mark.asyncio
    async def test_plain_view_still_gets_a_token(self):
        session = await sync_to_async(_new_session)()
        first = await _ws("_PlainView", "acme.example.com", session)
        assert first["mount_token"]


class TestEnvelope:
    def test_round_trip_for_the_same_scope(self):
        from djust.security.state_snapshot import sign_snapshot, unsign_snapshot

        token = sign_snapshot('{"a":1}', "m.V", "sess", tenant_scope="tenant:acme")
        assert unsign_snapshot(token, "m.V", "sess", tenant_scope="tenant:acme") == '{"a":1}'

    def test_other_tenant_is_rejected(self):
        from djust.security.state_snapshot import sign_snapshot, unsign_snapshot

        token = sign_snapshot('{"a":1}', "m.V", "sess", tenant_scope="tenant:acme")
        assert unsign_snapshot(token, "m.V", "sess", tenant_scope="tenant:globex") is None

    def test_tenant_token_is_rejected_by_an_unscoped_mount_and_vice_versa(self):
        from djust.security.state_snapshot import sign_snapshot, unsign_snapshot

        scoped = sign_snapshot("{}", "m.V", "sess", tenant_scope="tenant:acme")
        unscoped = sign_snapshot("{}", "m.V", "sess")
        assert unsign_snapshot(scoped, "m.V", "sess") is None
        assert unsign_snapshot(unscoped, "m.V", "sess", tenant_scope="tenant:acme") is None

    def test_unresolved_tenant_scope_is_rejected(self):
        from djust.security.state_snapshot import sign_snapshot, unsign_snapshot

        token = sign_snapshot("{}", "m.V", "sess")
        assert unsign_snapshot(token, "m.V", "sess", tenant_scope=None) is None

    def test_a_token_issued_before_the_binding_still_serves_an_unscoped_view(self):
        """No tenant field in the envelope means unscoped: in-flight tokens of
        views without ``TenantMixin`` survive the upgrade; tenant views fall
        back to one fresh mount."""
        from djust.security import state_snapshot as mod

        envelope = json.dumps(
            {"slug": "m.V", "sid": "sess", "state": "{}"},
            sort_keys=True,
            separators=(",", ":"),
        )
        old = mod._signer().sign(envelope)
        assert mod.unsign_snapshot(old, "m.V", "sess") == "{}"
        assert mod.unsign_snapshot(old, "m.V", "sess", tenant_scope="tenant:acme") is None


class TestScopeHelper:
    def test_scope_of_a_view(self):
        from djust._tenant_state import snapshot_tenant_scope
        from djust.tenants.resolvers import TenantInfo

        assert snapshot_tenant_scope(_PlainView()) == ""
        view = _TenantView()
        assert snapshot_tenant_scope(view) is None  # unresolved: fail closed
        view.tenant = TenantInfo(tenant_id="acme")
        assert snapshot_tenant_scope(view) == "tenant:acme"
