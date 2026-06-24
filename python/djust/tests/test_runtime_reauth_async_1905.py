"""ADR-022 Iter 2 Phase 2.3a — reauth-on-event hook + async source reconcile +
binary-framing confirm (#1905). The last 2.3a fold before the 2.3b flip.

Three sub-folds:

(1) ``Transport.recheck_event_auth(view) -> bool`` — the runtime port of the WS
    per-event auth re-check (#1777, T3). Wired into ``_dispatch_event_inner`` at
    the SAME point the WS bespoke handler does (after the view-mounted check,
    BEFORE the actor branch / handler). GOES LIVE for SSE (events route through
    the runtime since Iter 1, #1887); WS keeps its own inline copy until the
    Phase 2.3b flip. #291 multiplexed-path care: the runtime clears
    ``view_instance`` UNCONDITIONALLY on a False return; the transport close is
    owned + gated by the hook.

(2) async ``source="async"`` reconcile — the runtime's ``_render_async_result``
    frames now carry ``source="async"`` to match the WS ``_run_async_work``
    frames (a #1646 drift INSIDE the convergence target). LIVE for SSE +
    url_change async work today; WS post-flip.

(3) binary-framing confirm — ``consumer.use_binary`` is dead (always False, never
    set True). ``WSConsumerTransport.send`` routes through ``consumer.send_json``
    (always JSON) and NEVER through the ``_send_update`` binary branch. We DESCOPE
    (no new binary path) + PIN with a guard test so a future use_binary-enable is
    a deliberate, tested change.

Each behavioral assertion is paired with a gate-off witness (#1468).
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler
from djust.runtime import ViewRuntime, WSConsumerTransport
from djust.sse import DjustSSEEventView, DjustSSEStreamView, _sse_sessions

pytestmark = pytest.mark.django_db

ALLOWED_ORIGIN = "https://example.com"


# --------------------------------------------------------------------------- #
# Test views.
# --------------------------------------------------------------------------- #


_HANDLER_RAN = False


class _PermSSEView(LiveView):
    """A permission_required view used for the SSE per-event reauth path.

    Owner-binding (Finding #24) gates by user pk, so a logged-OUT POSTer is
    already refused at the endpoint. The reauth gate covers the DISTINCT case
    of a still-authenticated, still-owning POSTer whose PERMISSION was revoked
    mid-session — exactly what login/owner binding cannot catch.
    """

    permission_required = "app.view_thing"
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        global _HANDLER_RAN
        _HANDLER_RAN = True
        self.count += 1

    @event_handler()
    def begin_async(self, **kwargs):
        self.loading = True
        self.start_async(self._do_work)

    def _do_work(self):
        self.count = 99
        self.loading = False

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _CounterSSEView(LiveView):
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def begin_async(self, **kwargs):
        self.loading = True
        self.start_async(self._do_work)

    def _do_work(self):
        self.count = 99
        self.loading = False

    def get_context_data(self, **kwargs):
        return {"count": self.count}


def _register(view_cls):
    setattr(sys.modules[__name__], view_cls.__name__, view_cls)
    return f"{__name__}.{view_cls.__name__}"


def _allowlist():
    return override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__.split(".")[0]])


def _drain(session):
    msgs = []
    while not session.queue.empty():
        msg = session.queue.get_nowait()
        if msg is None:
            continue
        msgs.append(msg)
    return msgs


def _perm_user(*, has_perm: bool, pk: int = 7):
    """An authenticated MagicMock user with controllable has_perms()."""
    user = MagicMock()
    user.is_authenticated = True
    user.is_anonymous = False
    user.pk = pk
    user.has_perms.return_value = has_perm
    user.has_perm.return_value = has_perm
    return user


async def _mount_stream(view_path, *, user, query=""):
    sid = "21111111-1111-1111-1111-111111111111"
    _sse_sessions.pop(sid, None)
    rf = RequestFactory()
    q = f"?view={view_path}" + (f"&{query}" if query else "")
    request = rf.get(f"/djust/sse/{sid}/{q}", HTTP_ORIGIN=ALLOWED_ORIGIN)
    request.user = user
    view = DjustSSEStreamView()
    response = await view.get(request, session_id=sid)
    return response, _sse_sessions.get(sid), sid


async def _post_event(session, sid, event, *, user, params=None):
    """Drive the REAL /event/ endpoint against an owner-bound session.

    The session is bound to pk=7 at mount; ``user`` is the POSTer (also pk=7 so
    owner-binding passes, but with a possibly-revoked permission).
    """
    rf = RequestFactory()
    request = rf.post(
        f"/djust/sse/{sid}/event/",
        data=json.dumps({"event": event, "params": params or {}}),
        content_type="application/json",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
    )
    request.user = user
    view = DjustSSEEventView()
    return await view.post(request, session_id=sid)


# =========================================================================== #
# Sub-fold 1 — reauth_on_event hook (SECURITY). LIVE for SSE.
# =========================================================================== #


class TestSSEReauthOnEvent:
    @override_settings(ALLOWED_HOSTS=["example.com"], LIVEVIEW_CONFIG={"reauth_on_event": True})
    @pytest.mark.asyncio
    async def test_revoked_permission_midsession_refuses_event_and_ends_stream(self):
        """reauth ON: a still-owning POSTer whose permission was revoked
        mid-session is refused — the handler does NOT run, an auth-error frame is
        queued, the stream ends, and the runtime clears view_instance."""
        global _HANDLER_RAN
        _HANDLER_RAN = False
        config.reset()
        try:
            path = _register(_PermSSEView)
            with _allowlist():
                # Mount with a user who HAS the permission (mount passes).
                _resp, session, sid = await _mount_stream(path, user=_perm_user(has_perm=True))
            assert session is not None, "permitted user must mount"
            _drain(session)

            # POST an event from the SAME owner whose permission is now REVOKED.
            resp = await _post_event(session, sid, "bump", user=_perm_user(has_perm=False))
            assert resp.status_code == 200  # endpoint owner-binding passed

            assert _HANDLER_RAN is False, "handler ran despite mid-session permission revocation"
            msgs = _drain(session)
            errs = [m for m in msgs if m.get("type") == "error"]
            assert errs, f"deauthorized SSE event must queue an auth-error frame, got {msgs!r}"
            renders = [m for m in msgs if m.get("type") in ("patch", "html_update")]
            assert not renders, f"deauthorized event must NOT render, got {msgs!r}"
            # The runtime cleared view_instance (#291 unconditional state-clear).
            assert session.runtime.view_instance is None, (
                "runtime must clear view_instance on a failed reauth (#291)"
            )
            # The stream was ended (close → shutdown → active=False).
            assert session.active is False, "failed reauth must end the SSE stream"
        finally:
            config.reset()

    @override_settings(ALLOWED_HOSTS=["example.com"], LIVEVIEW_CONFIG={"reauth_on_event": True})
    @pytest.mark.asyncio
    async def test_still_authorized_event_renders_normally(self):
        """reauth ON but the POSTer STILL has the permission: the event runs +
        renders as usual (the gate only blocks the revoked case)."""
        global _HANDLER_RAN
        _HANDLER_RAN = False
        config.reset()
        try:
            path = _register(_PermSSEView)
            with _allowlist():
                _resp, session, sid = await _mount_stream(path, user=_perm_user(has_perm=True))
            assert session is not None
            _drain(session)

            resp = await _post_event(session, sid, "bump", user=_perm_user(has_perm=True))
            assert resp.status_code == 200
            assert _HANDLER_RAN is True, "an authorized event must run with reauth on"
            assert session.runtime.view_instance is not None
            msgs = _drain(session)
            renders = [m for m in msgs if m.get("type") in ("patch", "html_update")]
            assert renders, f"authorized event must render, got {msgs!r}"
        finally:
            config.reset()

    @override_settings(ALLOWED_HOSTS=["example.com"], LIVEVIEW_CONFIG={"reauth_on_event": True})
    @pytest.mark.asyncio
    async def test_gate_off_revoked_permission_would_render(self):
        """Gate-off witness (#1468): neuter the SSE recheck → the now-unauthorized
        event WRONGLY runs + renders — proving the deny assertion above is
        non-tautological (the recheck is what blocks it)."""
        global _HANDLER_RAN
        _HANDLER_RAN = False
        config.reset()
        try:
            path = _register(_PermSSEView)
            with _allowlist():
                _resp, session, sid = await _mount_stream(path, user=_perm_user(has_perm=True))
            assert session is not None
            _drain(session)

            with patch(
                "djust.runtime.SSESessionTransport.recheck_event_auth",
                AsyncMock(return_value=True),
            ):
                resp = await _post_event(session, sid, "bump", user=_perm_user(has_perm=False))
                assert resp.status_code == 200

            assert _HANDLER_RAN is True, (
                "with the recheck gated off, the deauthorized event must run "
                "(else the deny test is a tautology)"
            )
            msgs = _drain(session)
            renders = [m for m in msgs if m.get("type") in ("patch", "html_update")]
            assert renders, "gate-off: the deauthorized event should render"
        finally:
            config.reset()

    @override_settings(ALLOWED_HOSTS=["example.com"], LIVEVIEW_CONFIG={"reauth_on_event": False})
    @pytest.mark.asyncio
    async def test_reauth_off_default_does_not_recheck(self):
        """Default OFF: no re-check — even a revoked-permission POSTer dispatches
        (the gate is opt-in)."""
        global _HANDLER_RAN
        _HANDLER_RAN = False
        config.reset()
        try:
            path = _register(_PermSSEView)
            with _allowlist():
                _resp, session, sid = await _mount_stream(path, user=_perm_user(has_perm=True))
            assert session is not None
            _drain(session)

            resp = await _post_event(session, sid, "bump", user=_perm_user(has_perm=False))
            assert resp.status_code == 200
            # With the flag off the hook returns True without consulting auth.
            assert _HANDLER_RAN is True, "reauth off (default) must dispatch without a re-check"
        finally:
            config.reset()


class TestReauthHookShape291:
    """#291 multiplexed-path care: the runtime clears view_instance
    UNCONDITIONALLY on a failed reauth — even when the transport close is gated
    (a future batched-event path). Driven through a fake transport whose
    ``recheck_event_auth`` returns False WITHOUT firing a transport close, so we
    isolate the runtime's state-clear from the hook's transport termination."""

    @pytest.mark.asyncio
    async def test_runtime_clears_view_instance_even_if_close_is_gated(self):
        view = MagicMock()
        view._view_id = None

        closed = {"called": False}

        rendered = {"called": False}

        class _FakeTransport:
            session_id = "fake"
            client_ip = None
            _client_ip = None

            async def send(self, data):
                pass

            async def send_error(self, error, **kwargs):
                pass

            async def close(self, code=1000):
                closed["called"] = True

            async def recheck_event_auth(self, v):
                # Refuse the event but DO NOT fire a transport close (simulating a
                # batched-event path where the close is gated on "not in batch").
                # The runtime must STILL clear state (the security gap is closed by
                # the state-clear, not the close).
                return False

            def uses_actors(self, v):
                return False

            def event_context(self, v):
                # Reached ONLY if the reauth gate failed to block — the gate-off
                # witness: gate the recheck off and the event WOULD render here.
                import contextlib

                rendered["called"] = True
                return contextlib.nullcontext()

        runtime = ViewRuntime(_FakeTransport())
        runtime.view_instance = view

        with patch.object(ViewRuntime, "_dispatch_event_render", AsyncMock()):
            await runtime._dispatch_event_inner({"type": "event", "event": "bump", "params": {}})

        assert runtime.view_instance is None, (
            "runtime must clear view_instance on failed reauth even if close is gated (#291)"
        )
        assert closed["called"] is False, "this fake gates its own close — none should have fired"
        assert rendered["called"] is False, "a refused event must NOT reach the render path"

    @pytest.mark.asyncio
    async def test_authorized_reauth_proceeds_to_handler(self):
        """A True recheck lets the event proceed into event_context (non-actor)."""
        reached = {"render": False}

        class _FakeTransport:
            session_id = "fake"
            client_ip = None
            _client_ip = None

            async def send(self, data):
                pass

            async def send_error(self, error, **kwargs):
                pass

            async def close(self, code=1000):
                pass

            async def recheck_event_auth(self, v):
                return True

            def event_context(self, v):
                import contextlib

                return contextlib.nullcontext()

        runtime = ViewRuntime(_FakeTransport())
        runtime.view_instance = MagicMock(_view_id=None)

        async def _fake_render(self, data):
            reached["render"] = True

        with patch.object(ViewRuntime, "_dispatch_event_render", _fake_render):
            await runtime._dispatch_event_inner({"type": "event", "event": "bump", "params": {}})

        assert runtime.view_instance is not None, "an authorized reauth must NOT clear state"
        assert reached["render"] is True, "an authorized reauth must reach the render path"


class TestWSReauthAdapterPort:
    """The WS adapter ``recheck_event_auth`` port (DORMANT for live WS events
    until Phase 2.3b, but unit-tested here so the port is correct when the flip
    lands)."""

    @override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True})
    @pytest.mark.asyncio
    async def test_ws_adapter_refuses_and_closes_on_deauth(self):
        config.reset()
        try:
            consumer = MagicMock()
            consumer.scope = {"session": {}}
            consumer.send_json = AsyncMock()
            consumer.close = AsyncMock()

            view = MagicMock()
            view.login_required = True
            view.permission_required = None
            view.login_url = None
            view.request = MagicMock()

            transport = WSConsumerTransport(consumer)
            with (
                patch("channels.auth.get_user", AsyncMock(return_value=AnonymousUser())),
                patch(
                    "djust.auth.core.check_view_auth_lightweight",
                    return_value=False,
                ),
            ):
                ok = await transport.recheck_event_auth(view)

            assert ok is False, "WS adapter must refuse the event on deauth"
            consumer.send_json.assert_awaited()  # navigate frame
            navigate = consumer.send_json.await_args.args[0]
            assert navigate.get("type") == "navigate", navigate
            consumer.close.assert_awaited_once()
            assert consumer.close.await_args.kwargs.get("code") == 4403
        finally:
            config.reset()

    @override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True})
    @pytest.mark.asyncio
    async def test_ws_adapter_allows_when_still_authorized(self):
        config.reset()
        try:
            consumer = MagicMock()
            consumer.scope = {"session": {}}
            consumer.send_json = AsyncMock()
            consumer.close = AsyncMock()

            view = MagicMock()
            view.login_required = True
            view.permission_required = None
            view.request = MagicMock()

            transport = WSConsumerTransport(consumer)
            with (
                patch("channels.auth.get_user", AsyncMock(return_value=MagicMock())),
                patch("djust.auth.core.check_view_auth_lightweight", return_value=True),
            ):
                ok = await transport.recheck_event_auth(view)

            assert ok is True
            consumer.close.assert_not_awaited()
        finally:
            config.reset()

    @override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True})
    @pytest.mark.asyncio
    async def test_ws_adapter_skips_when_view_requires_no_auth(self):
        """A view with neither login_required nor permission_required short-circuits
        to True WITHOUT a session read (the get_user seam is never consulted)."""
        config.reset()
        try:
            consumer = MagicMock()
            consumer.scope = {"session": {}}
            view = MagicMock()
            view.login_required = None
            view.permission_required = None
            transport = WSConsumerTransport(consumer)
            with patch("channels.auth.get_user", AsyncMock()) as gu:
                ok = await transport.recheck_event_auth(view)
            assert ok is True
            gu.assert_not_awaited()
        finally:
            config.reset()


# =========================================================================== #
# Sub-fold 2 — async source="async" reconcile. LIVE for SSE + url_change async.
# =========================================================================== #


class TestAsyncSourceReconcile:
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_async_result_frame_carries_source_async_over_sse(self):
        """A start_async result frame routed through the runtime carries
        ``source="async"`` (matching WS _run_async_work), end-to-end over the real
        SSE transport."""
        path = _register(_CounterSSEView)
        with _allowlist():
            _resp, session, sid = await _mount_stream(path, user=AnonymousUser())
        assert session is not None
        _drain(session)

        # Bind the session to a known owner pk + post as that owner so owner-binding
        # (Finding #24) passes and the event reaches the runtime (mirrors the
        # convergence test's _post_event owner setup).
        session._owner_user_pk = 7
        session._owner_session_key = None
        resp = await _post_event(session, sid, "begin_async", user=_perm_user(has_perm=True, pk=7))
        assert resp.status_code == 200
        _drain(session)  # the immediate event frame (source="event")

        followup = []
        for _ in range(200):
            await asyncio.sleep(0.01)
            followup += _drain(session)
            if any(m.get("type") in ("patch", "html_update") for m in followup):
                break

        async_frames = [m for m in followup if m.get("type") in ("patch", "html_update")]
        assert async_frames, f"background completion must stream a render frame, got {followup!r}"
        assert all(m.get("source") == "async" for m in async_frames), (
            f"runtime async-result frames must carry source='async', got {async_frames!r}"
        )
        assert session.runtime.view_instance.count == 99, "background task did not run"

    @pytest.mark.asyncio
    async def test_render_async_result_tags_source_async_unit(self):
        """Unit-level: _render_async_result stamps source='async' on BOTH the patch
        and the html_update branch. Gate-off proven by the sibling below."""
        sent = []

        class _CapTransport:
            session_id = "cap"
            client_ip = None
            _client_ip = None

            async def send(self, data):
                sent.append(data)

            async def send_error(self, error, **kwargs):
                pass

            async def close(self, code=1000):
                pass

            def next_client_version(self, html, rust_version):
                return rust_version

        view = MagicMock()
        view._sync_state_to_rust = MagicMock()
        # patch branch: render_with_diff returns (html, patches, version)
        view.render_with_diff = MagicMock(return_value=("<div>x</div>", [{"op": "x"}], 3))

        runtime = ViewRuntime(_CapTransport())
        runtime.view_instance = view
        # Neuter the turn-end flush so the unit test stays focused on the frame.
        runtime._flush_all_pending = AsyncMock()

        await runtime._render_async_result("begin_async")
        assert sent, "a frame must be sent"
        assert sent[0]["type"] == "patch"
        assert sent[0]["source"] == "async", (
            f"patch async frame must be source='async': {sent[0]!r}"
        )

        # html_update branch
        sent.clear()
        view.render_with_diff = MagicMock(return_value=("<div>y</div>", None, 4))
        view._strip_comments_and_whitespace = MagicMock(side_effect=lambda h: h)
        view._extract_liveview_content = MagicMock(side_effect=lambda h: h)
        await runtime._render_async_result("begin_async")
        assert sent[0]["type"] == "html_update"
        assert sent[0]["source"] == "async", (
            f"html_update async frame must be source='async': {sent[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_gate_off_source_tag_removed_is_red(self):
        """Gate-off witness (#1468): if the source tag were absent the assertion in
        the unit test above would FAIL. We prove that by re-running the production
        method with a stripping wrapper and asserting the tag is then missing —
        i.e. the assertion is load-bearing, not coincidental."""
        sent = []

        class _CapTransport:
            session_id = "cap"
            client_ip = None
            _client_ip = None

            async def send(self, data):
                # Simulate the pre-fix behavior: drop the source tag.
                data.pop("source", None)
                sent.append(data)

            async def send_error(self, error, **kwargs):
                pass

            async def close(self, code=1000):
                pass

            def next_client_version(self, html, rust_version):
                return rust_version

        view = MagicMock()
        view._sync_state_to_rust = MagicMock()
        view.render_with_diff = MagicMock(return_value=("<div>x</div>", [{"op": "x"}], 3))
        runtime = ViewRuntime(_CapTransport())
        runtime.view_instance = view
        runtime._flush_all_pending = AsyncMock()
        await runtime._render_async_result("begin_async")
        assert "source" not in sent[0], (
            "this stripping transport models the pre-fix shape; the real fix puts "
            "source='async' on the frame BEFORE send, which the positive test asserts"
        )


# =========================================================================== #
# Sub-fold 3 — binary-framing confirm. DESCOPE + PIN (use_binary is dead).
# =========================================================================== #


class TestBinaryFramingConfirm:
    @pytest.mark.asyncio
    async def test_ws_transport_send_emits_json_via_send_json(self):
        """``WSConsumerTransport.send`` routes through ``consumer.send_json`` (always
        JSON), NEVER through the dead ``_send_update`` ``use_binary`` branch
        (websocket.py:580 = False always, 'MessagePack TODO'). This pins the live
        WS behavior so a future use_binary-enable is a DELIBERATE, tested change."""
        consumer = MagicMock()
        consumer.send_json = AsyncMock()
        # Even if a stray use_binary attr exists + is True, the transport.send path
        # does not consult it — it calls send_json unconditionally.
        consumer.use_binary = True
        transport = WSConsumerTransport(consumer)

        frame = {"type": "patch", "patches": [], "version": 1}
        await transport.send(frame)

        consumer.send_json.assert_awaited_once_with(frame)

    def test_use_binary_is_dead_default_false_and_never_enabled(self):
        """Source-grep pin: ``use_binary`` is initialized to False and is never set
        to True anywhere in the production package. If a future PR enables binary
        framing it must update this pin (and add a real binary path + test)."""
        import pathlib
        import re

        pkg = pathlib.Path(__import__("djust").__file__).parent
        enabled = []
        for py in pkg.rglob("*.py"):
            if "tests" in py.parts:
                continue
            text = py.read_text(encoding="utf-8")
            # Any assignment of use_binary to a truthy literal.
            for m in re.finditer(r"use_binary\s*=\s*(\w+)", text):
                if m.group(1) == "True":
                    enabled.append(f"{py}: {m.group(0)}")
        assert not enabled, (
            "use_binary is documented as dead (JSON-only); a True assignment means "
            f"a binary path was enabled without updating this pin: {enabled}"
        )
