"""Behavior-preservation + convergence tests for the shared pre-mount security
sequence helper (#1853).

#1853 single-sourced the pre-mount auth+tenant SEQUENCE — view-level auth via
``check_view_auth``, then (on auth success) tenant resolve via ``_ensure_tenant``
and the tenant ContextVar bind — into ONE helper,
``djust.auth.core.run_pre_mount_auth``. Every live mount path now routes through
it:

* WebSocket ``LiveViewConsumer.handle_mount`` (``websocket.py``),
* the generic ``ViewRuntime.dispatch_mount`` → ``_check_auth`` (``runtime.py``),
* the legacy SSE ``_sse_mount_view`` (``sse.py``).

The HTTP-GET mount path (``RequestMixin.get``) keeps calling ``check_view_auth``
directly (it has Django's TenantMiddleware + dispatch semantics), so its auth
verdict is provably identical to the helper's (the helper RETURNS exactly what
``check_view_auth`` returns).

These tests prove BEHAVIOR PRESERVATION across every mount path:

* a normal mount still works (authed → allowed);
* a ``login_required`` mount is still DENIED for an anonymous caller, with each
  transport's exact denial envelope (WS navigate+close 4403 / runtime+SSE
  error/navigate frame);
* an object-permission-denied mount is still refused (that control was
  deliberately LEFT OUT of the helper — post-mount / url-change — so this proves
  it survived the refactor);
* a sticky-child mount and a state_snapshot-restore mount still mount;
* HTTP-GET and runtime/SSE agree with the helper on the auth + object-perm
  verdicts.

Plus the GATE-OFF (#1468): neutering ``run_pre_mount_auth`` to always-allow makes
the auth-denied assertion on EVERY transport that routes through it fail —
proving the shared helper is load-bearing on all of them at once.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("channels")

from django.core.exceptions import PermissionDenied  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.auth.core import check_view_auth, run_pre_mount_auth  # noqa: E402


# --------------------------------------------------------------------------- #
# Views + scope helpers
# --------------------------------------------------------------------------- #
class _AnonUser:
    is_authenticated = False
    is_active = False
    is_anonymous = True
    pk = None


class _AuthedUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    pk = 1

    def has_perms(self, perms):  # pragma: no cover - login-only views don't reach
        return True


class _LoginRequiredView(LiveView):
    """A login-gated view; records whether mount() ran."""

    login_required = True
    template = (
        '<div dj-view="djust.tests.test_mount_security_sequence_1853._LoginRequiredView" '
        'dj-id="0">secret</div>'
    )

    def mount(self, request, **kwargs):
        self.mounted = True
        self.count = 0


class _PublicView(LiveView):
    """A public view used for the happy-path / sticky / snapshot cases."""

    login_required = False
    enable_state_snapshot = True
    template = (
        '<div dj-view="djust.tests.test_mount_security_sequence_1853._PublicView" '
        'dj-id="0">pub:{{ count }}</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 7

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _ObjectDeniedView(LiveView):
    """Opts into the object-permission lifecycle and DENIES access."""

    login_required = False
    template = (
        '<div dj-view="djust.tests.test_mount_security_sequence_1853._ObjectDeniedView" '
        'dj-id="0">obj</div>'
    )

    def mount(self, request, **kwargs):
        self.obj_id = 1

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False


class _ObjectAllowedView(LiveView):
    login_required = False
    template = (
        '<div dj-view="djust.tests.test_mount_security_sequence_1853._ObjectAllowedView" '
        'dj-id="0">obj</div>'
    )

    def mount(self, request, **kwargs):
        self.obj_id = 1

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return True


class _StickyChild(LiveView):
    """A sticky child embedded in the parent below."""

    sticky = True
    sticky_id = "seq1853child"
    template = "<div>child:{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 3

    def get_context_data(self, **kwargs):
        return {"n": self.n, "view": self}


class _StickyParentView(LiveView):
    """Parent page embedding the sticky child via ``{% live_render sticky=True %}``.

    Used to prove the WS-only sticky-child mount mechanics still work AFTER the
    pre-mount auth+tenant sequence converged onto run_pre_mount_auth (#1853 left
    all sticky/snapshot/actor/render mechanics untouched)."""

    login_required = False
    template = (
        "{% load live_tags %}"
        "<div dj-root "
        'dj-view="djust.tests.test_mount_security_sequence_1853._StickyParentView" '
        'dj-id="0">'
        "<h1>parent {{ ticks }}</h1>"
        '{% live_render "djust.tests.test_mount_security_sequence_1853._StickyChild" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.ticks = 0

    def get_context_data(self, **kwargs):
        return {"view": self, "ticks": self.ticks}


# Make the dotted paths resolvable for the consumer's importer.
for _name in (
    "_AnonUser",
    "_AuthedUser",
    "_LoginRequiredView",
    "_PublicView",
    "_ObjectDeniedView",
    "_ObjectAllowedView",
    "_StickyChild",
    "_StickyParentView",
):
    setattr(sys.modules[__name__], _name, getattr(sys.modules[__name__], _name))

_MOD = __name__
_LOGIN_PATH = f"{_MOD}._LoginRequiredView"
_PUBLIC_PATH = f"{_MOD}._PublicView"
_OBJ_DENIED_PATH = f"{_MOD}._ObjectDeniedView"
_STICKY_PARENT_PATH = f"{_MOD}._StickyParentView"


def _anon_middleware(app):
    """ASGI wrapper that injects an anonymous scope user + empty session."""

    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _AnonUser()
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


def _authed_middleware(app):
    """ASGI wrapper that injects an authenticated scope user + empty session."""

    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _AuthedUser()
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


async def _drain_handshake(communicator):
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass


@pytest.fixture
def _allow_this_module():
    """Allow this test module's view paths through the mount allowlist.

    Used as an explicit fixture (rather than a class decorator) because
    ``override_settings`` cannot decorate a plain class with async test methods.
    """
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
        yield


# --------------------------------------------------------------------------- #
# Unit-level: the helper's pre-mount auth+tenant sequence semantics.
# --------------------------------------------------------------------------- #
class TestRunPreMountAuthHelper:
    def test_returns_none_when_auth_passes_no_tenant(self):
        """A public (non-tenant) view → helper returns None (mount proceeds)."""
        view = _PublicView()
        request = RequestFactory().get("/")
        request.user = _AuthedUser()
        assert run_pre_mount_auth(view, request) is None

    def test_returns_redirect_url_for_anon_on_login_required(self):
        """Anonymous caller on a login_required view → helper returns the login
        URL (the exact value check_view_auth would return)."""
        view = _LoginRequiredView()
        request = RequestFactory().get("/")
        request.user = _AnonUser()

        helper_verdict = run_pre_mount_auth(view, request)
        direct_verdict = check_view_auth(_LoginRequiredView(), request)
        assert helper_verdict is not None
        assert helper_verdict == direct_verdict, (
            "the helper's auth verdict must equal check_view_auth's verbatim — it "
            "is the single source the HTTP-GET path also relies on for parity"
        )

    def test_raises_permission_denied_for_authed_without_permission(self):
        """An authenticated caller lacking a required permission → helper
        propagates PermissionDenied (each transport maps it to its 403 shape)."""

        class _PermView(LiveView):
            permission_required = "app.view_thing"
            template = "<div dj-id='0'>x</div>"

        class _NoPermUser:
            is_authenticated = True
            is_anonymous = False

            def has_perms(self, perms):
                return False

        request = RequestFactory().get("/")
        request.user = _NoPermUser()
        with pytest.raises(PermissionDenied):
            run_pre_mount_auth(_PermView(), request)

    def test_skips_tenant_resolution_on_auth_redirect(self):
        """On an auth redirect, _ensure_tenant must NOT run (mirrors the
        pre-existing per-transport early return on denial)."""
        ensure_calls = []

        class _TenantLoginView(LiveView):
            login_required = True
            template = "<div dj-id='0'>x</div>"

            def _ensure_tenant(self, request):
                ensure_calls.append(request)

        request = RequestFactory().get("/")
        request.user = _AnonUser()

        verdict = run_pre_mount_auth(_TenantLoginView(), request)
        assert verdict is not None  # redirect
        assert ensure_calls == [], (
            "tenant resolution ran despite an auth denial — the helper must skip "
            "_ensure_tenant when auth fails (sequence invariant)"
        )

    def test_resolves_then_binds_tenant_in_order_on_auth_success(self):
        """On auth success, the helper calls _ensure_tenant THEN binds the
        resolved tenant via set_current_tenant (canonical order)."""
        order = []

        class _FakeTenant:
            id = 42

        class _TenantView(LiveView):
            login_required = False
            template = "<div dj-id='0'>x</div>"

            def _ensure_tenant(self, request):
                order.append("resolve")
                self._tenant = _FakeTenant()

        import djust.auth.core as core

        seen_bind = {}
        real_bind = core._bind_current_tenant

        def _spy_bind(tenant):
            order.append("bind")
            seen_bind["tenant"] = tenant
            return real_bind(tenant)

        core._bind_current_tenant = _spy_bind
        try:
            request = RequestFactory().get("/")
            request.user = _AuthedUser()
            assert run_pre_mount_auth(_TenantView(), request) is None
        finally:
            core._bind_current_tenant = real_bind

        assert order == ["resolve", "bind"], (
            f"pre-mount sequence ran out of order: {order!r} (must resolve tenant "
            f"before binding it)"
        )
        assert isinstance(seen_bind.get("tenant"), _FakeTenant), (
            "the helper bound the wrong value — it must bind the view's resolved "
            "_tenant after _ensure_tenant populates it"
        )


# --------------------------------------------------------------------------- #
# WebSocket mount path (LiveViewConsumer.handle_mount).
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_allow_this_module")
class TestWebSocketMountSequence:
    async def test_authed_normal_mount_succeeds(self):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        app = _authed_middleware(LiveViewConsumer.as_asgi())
        communicator = WebsocketCommunicator(app, "/ws/")
        connected, _ = await communicator.connect()
        assert connected
        await _drain_handshake(communicator)
        await communicator.send_json_to({"type": "mount", "view": _PUBLIC_PATH, "url": "/pub/"})

        resp = await communicator.receive_json_from(timeout=3)
        assert resp.get("type") == "mount", f"expected a mount frame, got {resp!r}"
        assert "pub:7" in (resp.get("html") or ""), f"mount() state missing: {resp!r}"
        await communicator.disconnect()

    async def test_anon_login_required_mount_denied_navigate_then_close(self):
        """The denial envelope must be preserved exactly: navigate frame THEN
        close(4403) (threat model T1)."""
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        app = _anon_middleware(LiveViewConsumer.as_asgi())
        communicator = WebsocketCommunicator(app, "/ws/")
        connected, _ = await communicator.connect()
        assert connected
        await _drain_handshake(communicator)
        await communicator.send_json_to({"type": "mount", "view": _LOGIN_PATH, "url": "/secret/"})

        nav = await communicator.receive_json_from(timeout=2)
        assert nav.get("type") == "navigate", f"expected navigate redirect, got {nav!r}"
        out = await communicator.receive_output(timeout=2)
        assert out["type"] == "websocket.close" and out.get("code") == 4403, (
            f"socket not closed 4403 after auth redirect: {out!r}"
        )
        await communicator.disconnect()

    async def test_object_permission_denied_mount_refused(self):
        """The post-mount object-permission control (NOT folded into the helper)
        still refuses a denied object over WS: error frame + close 4403."""
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        app = _authed_middleware(LiveViewConsumer.as_asgi())
        communicator = WebsocketCommunicator(app, "/ws/")
        connected, _ = await communicator.connect()
        assert connected
        await _drain_handshake(communicator)
        await communicator.send_json_to({"type": "mount", "view": _OBJ_DENIED_PATH, "url": "/obj/"})

        err = await communicator.receive_json_from(timeout=2)
        assert err.get("type") == "error", f"expected object-perm error frame, got {err!r}"
        out = await communicator.receive_output(timeout=2)
        assert out["type"] == "websocket.close" and out.get("code") == 4403, (
            f"socket not closed 4403 after object-perm denial: {out!r}"
        )
        await communicator.disconnect()

    async def test_sticky_child_parent_mount_still_works(self):
        """A parent embedding a sticky child via {% live_render sticky=True %}
        still mounts — the WS-only sticky-child mechanics are untouched by the
        pre-mount auth+tenant convergence (#1853)."""
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        app = _authed_middleware(LiveViewConsumer.as_asgi())
        communicator = WebsocketCommunicator(app, "/ws/")
        connected, _ = await communicator.connect()
        assert connected
        await _drain_handshake(communicator)
        await communicator.send_json_to(
            {"type": "mount", "view": _STICKY_PARENT_PATH, "url": "/dash/"}
        )
        resp = await communicator.receive_json_from(timeout=3)
        assert resp.get("type") == "mount", f"sticky-parent mount failed: {resp!r}"
        html = resp.get("html") or ""
        assert "parent" in html and "child:3" in html, (
            f"sticky child not rendered into parent mount: {html!r}"
        )
        await communicator.disconnect()

    async def test_state_snapshot_restore_mount_still_works(self):
        """A signed state_snapshot restore still mounts (the snapshot-restore
        mechanic is WS-only and untouched by #1853)."""
        from channels.testing import WebsocketCommunicator

        from djust.security import sign_snapshot
        from djust.websocket import LiveViewConsumer

        import json

        app = _authed_middleware(LiveViewConsumer.as_asgi())
        communicator = WebsocketCommunicator(app, "/ws/")
        connected, _ = await communicator.connect()
        assert connected
        await _drain_handshake(communicator)

        signed = sign_snapshot(json.dumps({"count": 99}), _PUBLIC_PATH, None)
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": _PUBLIC_PATH,
                "url": "/pub/",
                "live_redirect_mount": True,
                "state_snapshot": {
                    "view_slug": _PUBLIC_PATH,
                    "state_json": signed,
                },
            }
        )
        resp = await communicator.receive_json_from(timeout=3)
        assert resp.get("type") == "mount", f"snapshot-restore mount failed: {resp!r}"
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# Runtime mount path (ViewRuntime.dispatch_mount → _check_auth).
# --------------------------------------------------------------------------- #
class _MockTransport:
    session_id = "seq-1853"
    scope = None

    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)

    async def send_error(self, message, **kwargs):
        self.sent.append({"type": "error", "error": message, **kwargs})


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_allow_this_module")
class TestRuntimeMountSequence:
    async def test_authed_normal_mount_succeeds(self):
        from djust.runtime import ViewRuntime

        transport = _MockTransport()
        runtime = ViewRuntime(transport)
        await runtime.dispatch_mount(
            {"type": "mount", "view": _PUBLIC_PATH, "params": {}, "url": "/pub/"}
        )
        types = [m.get("type") for m in transport.sent]
        assert "mount" in types, f"runtime did not emit a mount frame: {transport.sent!r}"
        assert runtime.view_instance is not None

    async def test_anon_login_required_mount_denied(self):
        """Runtime denial envelope preserved: navigate frame + view_instance
        cleared (no usable mount left)."""
        from djust.runtime import ViewRuntime

        class _AnonScopeTransport(_MockTransport):
            scope = {"user": _AnonUser(), "session": {}}

        transport = _AnonScopeTransport()
        runtime = ViewRuntime(transport)
        await runtime.dispatch_mount(
            {"type": "mount", "view": _LOGIN_PATH, "params": {}, "url": "/secret/"}
        )
        types = [m.get("type") for m in transport.sent]
        assert "navigate" in types, f"runtime did not redirect anon: {transport.sent!r}"
        assert runtime.view_instance is None, "denied mount left a usable view_instance"

    async def test_check_auth_returns_truthy_on_denial(self):
        """_check_auth (the runtime auth seam) still returns truthy on denial and
        None on success — preserved through the run_pre_mount_auth delegation."""
        from djust.runtime import ViewRuntime

        transport = _MockTransport()
        runtime = ViewRuntime(transport)

        runtime.view_instance = _LoginRequiredView()
        request = RequestFactory().get("/")
        request.user = _AnonUser()
        assert await runtime._check_auth(request) is True

        runtime2 = ViewRuntime(_MockTransport())
        runtime2.view_instance = _PublicView()
        request2 = RequestFactory().get("/")
        request2.user = _AuthedUser()
        assert await runtime2._check_auth(request2) is None


# --------------------------------------------------------------------------- #
# SSE mount path — converged onto the shared runtime (dispatch_mount), #1887.
#
# Post-#1887 (ADR-022 Iter 1) the SSE mount runs the SAME pre-mount auth+tenant
# SEQUENCE (run_pre_mount_auth via dispatch_mount → _check_auth) as the WS +
# runtime paths — the legacy bespoke _sse_mount_view is gone. SSE still mounts
# against the REAL HTTP request (real request.user), preserved via
# SSESessionTransport.build_request reading session._request (stashed here as the
# GET stream does); without it auth would run against a userless request. The
# 'mounted' verdict is now runtime.view_instance is not None.
# --------------------------------------------------------------------------- #
def _sse_request(user):
    from unittest.mock import MagicMock

    request = RequestFactory().get("/")
    request.user = user
    request.session = MagicMock()
    return request


async def _sse_mount(session, request, view_path):
    """Drive the converged SSE mount and return the legacy-equivalent bool."""
    session._request = request
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": view_path, "url": request.path, "params": {}}
    )
    return session.runtime.view_instance is not None


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_allow_this_module")
class TestSSEMountSequence:
    async def test_authed_normal_mount_succeeds(self):
        import uuid

        from djust.sse import SSESession

        session = SSESession(str(uuid.uuid4()))
        session._owner_user_pk = 1
        mounted = await _sse_mount(session, _sse_request(_AuthedUser()), _PUBLIC_PATH)
        assert mounted is True
        # A "mount" message was pushed to the stream queue.
        msgs = []
        while not session.queue.empty():
            msgs.append(session.queue.get_nowait())
        assert any(m and m.get("type") == "mount" for m in msgs), f"no mount msg: {msgs!r}"

    async def test_anon_login_required_mount_denied(self):
        """SSE denial envelope preserved through the runtime: no view_instance +
        a navigate message; no usable session registered."""
        import uuid

        from djust.sse import SSESession

        session = SSESession(str(uuid.uuid4()))
        session._owner_user_pk = None
        mounted = await _sse_mount(session, _sse_request(_AnonUser()), _LOGIN_PATH)
        assert mounted is False, "anon mounted a login_required view over SSE"
        msgs = []
        while not session.queue.empty():
            msgs.append(session.queue.get_nowait())
        assert any(m and m.get("type") == "navigate" for m in msgs), (
            f"expected a navigate redirect over SSE, got {msgs!r}"
        )


# --------------------------------------------------------------------------- #
# Cross-path verdict parity (HTTP-GET / runtime / SSE all agree with the helper).
# --------------------------------------------------------------------------- #
class TestCrossPathVerdictParity:
    def test_http_get_auth_verdict_matches_helper(self):
        """The HTTP-GET path uses check_view_auth directly; the helper wraps it,
        so for a non-tenant view their auth verdicts are identical."""
        request = RequestFactory().get("/")
        request.user = _AnonUser()
        http_verdict = check_view_auth(_LoginRequiredView(), request)  # HTTP-GET seam
        helper_verdict = run_pre_mount_auth(_LoginRequiredView(), request)
        assert http_verdict == helper_verdict and http_verdict is not None

        request2 = RequestFactory().get("/")
        request2.user = _AuthedUser()
        assert (
            check_view_auth(_PublicView(), request2)
            == run_pre_mount_auth(_PublicView(), request2)
            is None
        )

    def test_object_perm_verdict_identical_across_paths(self):
        """enforce_object_permission is the single shared object-perm chokepoint
        (NOT in the helper); every path raises PermissionDenied for a denied
        object and passes for an allowed one."""
        from djust.auth.core import enforce_object_permission

        request = RequestFactory().get("/")

        denied = _ObjectDeniedView()
        denied.obj_id = 1
        with pytest.raises(PermissionDenied):
            enforce_object_permission(denied, request)

        allowed = _ObjectAllowedView()
        allowed.obj_id = 1
        assert enforce_object_permission(allowed, request) is None
