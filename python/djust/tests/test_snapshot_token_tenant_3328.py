"""#3328: the signed ``state_snapshot`` token is bound to the tenant.

The legacy opt-in token (``enable_state_snapshot = True``) was signed over the
view slug and a digest of the Django session key. It is now also bound to the
tenant of the view that minted it: a mount under another tenant (same session,
same view path) ignores it and mounts fresh.

These tests drive the real ``LiveViewConsumer`` through a
``WebsocketCommunicator`` (mount and ``live_redirect_mount``), the HTTP
fallback POST (the other place a token is minted), SSE, and the signing module.
``dispatch_mount`` is shared by WebSocket and SSE, so the one verification
site is the one every transport reaches.
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

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
# The reproduction: a token minted under tenant A, other under tenant B
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["mount", "live_redirect_mount"])
class TestTokenTenantBindingOverWebSocket:
    @pytest.mark.asyncio
    async def test_event_token_from_tenant_a_does_not_restore_into_tenant_b(self, kind):
        session = await sync_to_async(_new_session)()
        acme = await _ws("_TenantView", "acme.example.com", session, event="keep")
        token = acme["event_token"]
        assert token, "precondition: the event reply carries a refreshed token"
        await sync_to_async(_forget_saved_state)(session)

        # Control: tenant A echoing its own token restores its state.
        own = await _ws("_TenantView", "acme.example.com", session, snapshot=token, kind=kind)
        assert "[secret-of-acme]" in own["html"], own["html"]
        await sync_to_async(_forget_saved_state)(session)

        other = await _ws("_TenantView", "globex.example.com", session, snapshot=token, kind=kind)
        assert "secret-of-acme" not in other["html"], other["html"]
        assert "[fresh-globex]" in other["html"], other["html"]

    @pytest.mark.asyncio
    async def test_mount_token_from_tenant_a_does_not_restore_into_tenant_b(self, kind):
        session = await sync_to_async(_new_session)()
        # The mount frame itself ships a token (the view's state at mount).
        acme = await _ws("_TenantView", "acme.example.com", session)
        token = acme["mount_token"]
        assert token, "precondition: the mount frame carries a token"
        await sync_to_async(_forget_saved_state)(session)

        other = await _ws("_TenantView", "globex.example.com", session, snapshot=token, kind=kind)
        assert "[fresh-acme]" not in other["html"], other["html"]
        assert "[fresh-globex]" in other["html"], other["html"]

    @pytest.mark.asyncio
    async def test_token_from_the_http_fallback_is_bound_too(self, kind):
        session = await sync_to_async(_new_session)()
        request = RequestFactory().post(
            _URL,
            data=json.dumps({"event": "keep", "params": {}}),
            content_type="application/json",
            HTTP_HOST="acme.example.com",
        )
        request.session = session
        response = await sync_to_async(_TenantView().post)(request)
        token = json.loads(response.content).get("state_snapshot_signed")
        assert token, "precondition: the HTTP fallback reply carries a token"
        await sync_to_async(_forget_saved_state)(session)

        own = await _ws("_TenantView", "acme.example.com", session, snapshot=token, kind=kind)
        assert "[secret-of-acme]" in own["html"], own["html"]
        await sync_to_async(_forget_saved_state)(session)
        other = await _ws("_TenantView", "globex.example.com", session, snapshot=token, kind=kind)
        assert "secret-of-acme" not in other["html"], other["html"]
        assert "[fresh-globex]" in other["html"], other["html"]


class TestUnscopedViewsKeepWorking:
    @pytest.mark.asyncio
    async def test_view_without_the_hook_still_restores_from_its_token(self):
        session = await sync_to_async(_new_session)()
        first = await _ws("_PlainView", "acme.example.com", session, event="keep")
        await sync_to_async(_forget_saved_state)(session)
        again = await _ws("_PlainView", "acme.example.com", session, snapshot=first["event_token"])
        assert "[kept-plain]" in again["html"], again["html"]

    @pytest.mark.asyncio
    async def test_unresolved_tenant_mints_and_restores_nothing(self):
        session = await sync_to_async(_new_session)()
        first = await _ws("_OptionalTenantView", "testserver", session, event="keep")
        assert not first["mount_token"] and not first["event_token"], first
        # A token minted for a resolved tenant is not accepted by a mount that
        # resolves none (fail closed, never the unscoped fallback).
        acme = await _ws("_TenantView", "acme.example.com", session, event="keep")
        await sync_to_async(_forget_saved_state)(session)
        other = await _ws(
            "_OptionalTenantView", "testserver", session, snapshot=acme["event_token"]
        )
        assert "[fresh-notenant]" in other["html"], other["html"]


# --------------------------------------------------------------------------- #
# The signing module
# --------------------------------------------------------------------------- #
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
            {"slug": "m.V", "sid": mod._session_digest("sess"), "state": "{}"},
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
        view.tenant = TenantInfo(tenant_id="a:b")
        assert snapshot_tenant_scope(view) == "tenant:a%3Ab"


# --------------------------------------------------------------------------- #
# The secondary note: a mixin listed before TenantMixin must chain
# --------------------------------------------------------------------------- #
class TestRenderOnlyKeysChainThroughTheMro:
    def test_model_form_mixin_before_tenant_mixin_keeps_the_tenant_key_render_only(self):
        from djust.forms import ModelFormMixin
        from djust.mixins.context import legacy_render_only_keys

        class _Edit(ModelFormMixin, TenantMixin, LiveView):
            context_object_name = "project"

        view = _Edit()
        view.object = None
        keys = legacy_render_only_keys(view)
        assert "tenant" in keys, keys
        assert {"object", "project"} <= keys, keys

    def test_tenant_mixin_before_model_form_mixin_still_works(self):
        from djust.forms import ModelFormMixin
        from djust.mixins.context import legacy_render_only_keys

        class _Edit(TenantMixin, ModelFormMixin, LiveView):
            context_object_name = "project"

        view = _Edit()
        view.object = None
        keys = legacy_render_only_keys(view)
        assert {"tenant", "object", "project"} <= keys, keys

    def test_every_render_only_hook_chains(self):
        """A hook that does not call ``super()`` hides the ones after it."""
        import ast
        import pathlib

        import djust

        pkg = pathlib.Path(djust.__file__).resolve().parent
        offenders = []
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(pkg).as_posix()
            if "/tests/" in rel or rel.startswith("tests/"):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == (
                    "_djust_render_only_context_keys"
                ):
                    if "super" not in ast.dump(node):
                        offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, offenders


# --------------------------------------------------------------------------- #
# SSE: the same single verification site
# --------------------------------------------------------------------------- #
def _sse_request(host: str, session: SessionStore):
    from django.contrib.auth.models import AnonymousUser

    request = RequestFactory().get(_URL, HTTP_HOST=host)
    request.user = AnonymousUser()
    request.session = session
    return request


async def _sse_mount(host: str, session: SessionStore, snapshot: str | None = None) -> dict:
    import uuid

    from djust.sse import SSESession

    sse = SSESession(str(uuid.uuid4()))
    sse._request = await sync_to_async(_sse_request)(host, session)
    data = {"type": "mount", "view": _slug("_TenantView"), "url": _URL}
    if snapshot is not None:
        data["state_snapshot"] = {"view_slug": _slug("_TenantView"), "state_json": snapshot}
    await sse.runtime.dispatch_mount(data)
    frames = []
    while not sse.queue.empty():
        frame = sse.queue.get_nowait()
        if frame is not None:
            frames.append(frame)
    mount = next(f for f in frames if f.get("type") == "mount")
    return mount


class TestTokenTenantBindingOverSse:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_token_from_tenant_a_does_not_restore_into_tenant_b(self):
        session = await sync_to_async(_new_session)()
        acme = await _sse_mount("acme.example.com", session)
        token = acme.get("state_snapshot_signed")
        assert token, "precondition: the SSE mount frame carries a token"

        own = await _sse_mount("acme.example.com", session, snapshot=token)
        assert "[fresh-acme]" in own["html"], own["html"]  # restored == same state

        other = await _sse_mount("globex.example.com", session, snapshot=token)
        assert "[fresh-acme]" not in other["html"], other["html"]
        assert "[fresh-globex]" in other["html"], other["html"]


# --------------------------------------------------------------------------- #
# ADR-038 explicit envelopes are bound to the tenant already: pin it
# --------------------------------------------------------------------------- #
class TestExplicitSnapshotIsTenantBound:
    def _request(self, tenant_id, session):
        from django.contrib.auth.models import AnonymousUser

        from djust.tenants.resolvers import TenantInfo

        request = RequestFactory().get(_URL)
        request.user = AnonymousUser()
        request.session = session
        request.tenant = TenantInfo(tenant_id=tenant_id)
        return request

    def test_explicit_client_token_from_tenant_a_is_refused_for_tenant_b(self):
        from djust._exposure_snapshots import snapshot_codec
        from djust.decorators import state

        class Explicit(LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root>{{ label }}</div>"
            label = state("initial", persist="client", client=True)

        session = _new_session()
        view = Explicit()
        view.label = "secret-of-acme"
        token = snapshot_codec(view, self._request("acme", session)).capture(view)

        assert snapshot_codec(Explicit(), self._request("acme", session)).restore(token) == {
            "label": "secret-of-acme"
        }
        assert snapshot_codec(Explicit(), self._request("globex", session)).restore(token) is None
