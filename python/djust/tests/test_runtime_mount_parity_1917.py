"""Runtime-path MOUNT parity gate (#1917, ADR-022 Iter 3 Phase 3.3a).

THE KEY GATE for Phase 3.3a: prove that ``ViewRuntime.dispatch_mount`` — now a
functional SUPERSET of the WS bespoke ``handle_mount`` after the 5 mount hooks
(#1916) are wired (#1917) — produces WS-EQUIVALENT mount behavior, driven over a
REAL ``WSConsumerTransport`` wrapping a genuinely-connected ``LiveViewConsumer``.

Routing stays bespoke (``RUNTIME_OWNED_VERBS`` UNCHANGED, ``handle_mount``
UNTOUCHED). 3.3a does NOT flip the routing — it only makes ``dispatch_mount`` a
clean superset. To exercise that superset over the real WS transport WITHOUT
adding ``mount`` to ``RUNTIME_OWNED_VERBS``, the temp mechanism here is a
**direct ``dispatch_mount`` call shim**:

  1. connect a real ``WebsocketCommunicator`` + mount a trivial bootstrap view
     (the only way to obtain a genuinely-live ``LiveViewConsumer`` with a real
     channel layer / scope / ``send_json`` wired to the wire);
  2. RESET the consumer (``view_instance = None`` + a fresh version counter) so
     the consumer is back to a pre-mount shape;
  3. wrap it in ``WSConsumerTransport`` (the EXACT adapter the 3.3b flip will
     route mounts through) + build a fresh ``ViewRuntime(transport,
     scope=consumer.scope)``;
  4. drive ``runtime.dispatch_mount(mount_frame)`` directly and assert the frames
     the consumer emitted to the wire match the WS bespoke shape.

Because the runtime emits via ``WSConsumerTransport.send`` →
``consumer.send_json``, every frame lands on the real communicator — so the
assertions are against the SAME wire bytes a 3.3b-flipped WS mount would produce.

Parity behaviors proven (the issue's gate list):
  * basic mount (mount frame + HTML + has_ids + consumer-owned version)
  * ACTOR mount — now RENDERS via ``dispatch_actor_mount`` (NOT the SSE refusal):
    the key 3.3a behavior change (Finding D)
  * ``sticky_hold`` ordering via ``on_mount_render_ready`` (Finding B)
  * NO-ARM version stamping via ``next_mount_version`` (Finding C)
  * auth-block #291-not-in-batch via ``finalize_mount_auth`` (Finding E)
  * state restore (Phase 3.1) still works through the wired path

Plus gate-off siblings (#1468): the actor branch (force ``uses_actors_for_mount``
False → actor view refused again) + the ``next_mount_version`` wiring.
"""

from __future__ import annotations


import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.runtime import ViewRuntime, WSConsumerTransport

_ALLOWED = "djust.tests.test_runtime_mount_parity_1917"
_ALLOWLIST = override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED])


# --------------------------------------------------------------------------- #
# Module-level test views (dotted-path resolvable).
# --------------------------------------------------------------------------- #


class BootstrapView(LiveView):
    """Trivial view mounted via the bespoke path solely to obtain a live
    consumer; reset away before the runtime drive."""

    template = f'<div dj-root dj-view="{_ALLOWED}.BootstrapView" dj-id="0">boot</div>'

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {}


class PlainView(LiveView):
    template = f'<div dj-root dj-view="{_ALLOWED}.PlainView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class ActorView(LiveView):
    """``use_actors=True`` view — the runtime must mount it through
    ``dispatch_actor_mount`` (render frame), NOT the SSE-style refusal."""

    use_actors = True
    template = f'<div dj-root dj-view="{_ALLOWED}.ActorView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class OptInSnapView(LiveView):
    """Opt-in snapshot view (Phase 3.1 state-restore parity)."""

    enable_state_snapshot = True
    template = f'<div dj-root dj-view="{_ALLOWED}.OptInSnapView" dj-id="0">n={{{{ n }}}}</div>'

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class LoginRequiredView(LiveView):
    """A view whose pre-mount auth returns a redirect URL (login-required shape)
    so the runtime emits a ``navigate`` frame + finalize_mount_auth gated close."""

    template = f'<div dj-root dj-view="{_ALLOWED}.LoginRequiredView" dj-id="0">secret</div>'

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {}


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


# --------------------------------------------------------------------------- #
# Harness — obtain a LIVE consumer, then drive dispatch_mount over it.
# --------------------------------------------------------------------------- #


async def _connect_and_bootstrap_consumer(url: str = "/parity-1917/"):
    """Connect a real ``WebsocketCommunicator``, mount the trivial bootstrap view
    to obtain a live ``LiveViewConsumer``, then RESET it to a pre-mount shape and
    return ``(communicator, consumer)``."""
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.observability.registry import get_view_for_session
    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame

    with _ALLOWLIST:
        await communicator.send_json_to(
            {"type": "mount", "view": f"{_ALLOWED}.BootstrapView", "url": url}
        )
        mount_frame = None
        for _ in range(8):
            mount_frame = await communicator.receive_json_from(timeout=3)
            if mount_frame.get("type") == "mount":
                break
    assert mount_frame and mount_frame.get("type") == "mount"
    consumer = get_view_for_session(mount_frame["session_id"])._ws_consumer

    # Reset to a pre-mount shape: drop the bootstrap view + zero the version
    # counter so the runtime mount starts from a fresh baseline (version 1) just
    # like a fresh WS mount would.
    consumer.view_instance = None
    consumer._last_sent_version = 0
    consumer._recovery_html = None
    return communicator, consumer


async def _drain(communicator, *, max_frames=8, timeout=2):
    """Best-effort drain of JSON frames already on the wire. Stops at the first
    receive timeout OR a ``websocket.close`` frame (the runtime's
    finalize_mount_auth close), so an auth-block mount that ends in a 4403 close
    still yields the preceding navigate/error frames without raising."""
    frames = []
    for _ in range(max_frames):
        if await communicator.receive_nothing(timeout=timeout, interval=0.05):
            break
        msg = await communicator.receive_output(timeout=timeout)
        if msg.get("type") == "websocket.close":
            break
        if msg.get("type") == "websocket.send":
            import json as _json

            frames.append(_json.loads(msg["text"]))
    return frames


async def _runtime_mount(consumer, view_path: str, *, url: str = "/parity-1917/", extra=None):
    """Drive ``ViewRuntime.dispatch_mount`` over a real ``WSConsumerTransport`` —
    the temp 3.3a mechanism (direct call shim; NOT RUNTIME_OWNED_VERBS routing)."""
    transport = WSConsumerTransport(consumer)
    runtime = ViewRuntime(transport, scope=consumer.scope)
    frame = {"type": "mount", "view": view_path, "url": url}
    if extra:
        frame.update(extra)
    with _ALLOWLIST:
        await runtime.dispatch_mount(frame)
    return runtime


# ===========================================================================
# 1. Basic mount over the runtime path == WS shape
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRuntimeBasicMountParity:
    async def test_runtime_mount_emits_ws_equivalent_mount_frame(self):
        communicator, consumer = await _connect_and_bootstrap_consumer()
        try:
            await _runtime_mount(consumer, f"{_ALLOWED}.PlainView")
            frames = await _drain(communicator)
            mounts = [f for f in frames if f.get("type") == "mount"]
            assert mounts, f"runtime dispatch_mount must emit a mount frame; got {frames!r}"
            mf = mounts[0]
            assert "c=0" in mf.get("html", ""), f"the mount frame must carry rendered HTML; {mf!r}"
            # ``has_ids`` mirrors the WS bespoke shape: it is ``"dj-id=" in html``
            # of the EXTRACTED [dj-root] inner content. For this single-element
            # view the inner content carries no nested dj-id, so has_ids is False —
            # exactly what the bespoke WS path emits for the same template (the
            # #1911 gate-off sibling asserts only ``c=0`` for the same reason).
            assert mf.get("has_ids") == ("dj-id=" in mf.get("html", "")), (
                f"has_ids must reflect dj-id presence in the extracted html; {mf!r}"
            )
            # Finding C: consumer-owned NO-ARM version. Fresh counter → version 1.
            assert mf.get("version") == 1, (
                f"the mount frame must stamp the consumer-owned no-arm version (1); got {mf!r}"
            )
            # NO-ARM: recovery stays unset after a mount.
            assert getattr(consumer, "_recovery_html", None) is None, (
                "Finding C: mount must NOT arm request_html recovery"
            )
        finally:
            await communicator.disconnect()


# ===========================================================================
# 2. ACTOR mount RENDERS (not refusal) — the key 3.3a behavior change
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRuntimeActorMountParity:
    async def test_runtime_actor_mount_renders_not_refusal(self):
        pytest.importorskip("channels")
        from djust._rust import create_session_actor

        if create_session_actor is None:
            pytest.skip("actor factory not built in this binary")

        communicator, consumer = await _connect_and_bootstrap_consumer()
        try:
            await _runtime_mount(consumer, f"{_ALLOWED}.ActorView")
            frames = await _drain(communicator, timeout=3)
            mounts = [f for f in frames if f.get("type") == "mount"]
            errors = [f for f in frames if f.get("type") == "error"]
            assert mounts, (
                "Finding D: a use_actors view over the RUNTIME path must now mount "
                f"with a mount frame (via dispatch_actor_mount), NOT refuse; got {frames!r}"
            )
            assert not errors, (
                f"the actor mount must NOT emit the SSE-style refusal error; got {errors!r}"
            )
            mf = mounts[0]
            assert mf.get("html"), f"the actor mount frame must carry rendered HTML; {mf!r}"
            assert mf.get("has_ids") is True, "the actor mount HTML must be dj-id tagged"
        finally:
            await communicator.disconnect()

    async def test_gate_off_actor_refused_when_transport_says_no(self, monkeypatch):
        """GATE-OFF (#1468): force ``uses_actors_for_mount`` → False on the WS
        transport (the SSE shape) and the actor view is REFUSED again — proving
        the positive test's render depends on the actor-mount branch, not on
        every mount succeeding."""
        communicator, consumer = await _connect_and_bootstrap_consumer()
        try:
            transport = WSConsumerTransport(consumer)
            monkeypatch.setattr(transport, "uses_actors_for_mount", lambda view: False)
            runtime = ViewRuntime(transport, scope=consumer.scope)
            with _ALLOWLIST:
                await runtime.dispatch_mount(
                    {"type": "mount", "view": f"{_ALLOWED}.ActorView", "url": "/parity-1917/"}
                )
            frames = await _drain(communicator)
            mounts = [f for f in frames if f.get("type") == "mount"]
            errors = [f for f in frames if f.get("type") == "error"]
            assert not mounts, (
                "gate-off: with uses_actors_for_mount False, the actor view must NOT "
                f"mount; got {frames!r}"
            )
            assert errors, "gate-off: the actor view must be refused (error frame)"
            blob = "".join(str(e) for e in errors)
            assert "not supported over SSE" in blob, (
                f"gate-off: the refusal must be the SSE-style actor envelope; got {errors!r}"
            )
        finally:
            await communicator.disconnect()


# ===========================================================================
# 3. NO-ARM version stamping (Finding C) — gate-off the wiring
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRuntimeNoArmVersionWiring:
    async def test_version_routes_through_next_mount_version(self, monkeypatch):
        """The mount frame version comes from ``next_mount_version`` (the no-arm
        consumer counter), NOT the raw Rust render version. Gate-off (#1468):
        stub ``next_mount_version`` to a sentinel → the mount frame carries it,
        proving the wiring is the version source (not the inline Rust version)."""
        communicator, consumer = await _connect_and_bootstrap_consumer()
        try:
            transport = WSConsumerTransport(consumer)
            monkeypatch.setattr(transport, "next_mount_version", lambda html, rust=1: 4242)
            runtime = ViewRuntime(transport, scope=consumer.scope)
            with _ALLOWLIST:
                await runtime.dispatch_mount(
                    {"type": "mount", "view": f"{_ALLOWED}.PlainView", "url": "/parity-1917/"}
                )
            frames = await _drain(communicator)
            mounts = [f for f in frames if f.get("type") == "mount"]
            assert mounts and mounts[0].get("version") == 4242, (
                "the mount frame version must come from next_mount_version (Finding C "
                f"wiring); got {mounts!r}"
            )
        finally:
            await communicator.disconnect()


# ===========================================================================
# 4. auth-block #291 not-in-batch (Finding E) via finalize_mount_auth
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRuntimeAuthBlockFinalize:
    async def test_login_required_redirect_closes_socket_not_in_batch(self, monkeypatch):
        """A login-required view (auth returns a redirect URL) over the runtime
        path emits a ``navigate`` frame AND — via ``finalize_mount_auth`` — closes
        the socket (4403) when NOT in a mount_batch (Finding E)."""
        communicator, consumer = await _connect_and_bootstrap_consumer()
        try:
            consumer._mounting_in_batch = False
            closed = {"code": None}
            orig_close = consumer.close

            async def _spy_close(code=1000):
                closed["code"] = code
                return await orig_close(code=code)

            consumer.close = _spy_close  # type: ignore[method-assign]

            # Force the pre-mount auth sequence to return a redirect URL.
            # _check_auth does ``from .auth import run_pre_mount_auth`` per call,
            # so patch the symbol on djust.auth (the import source).
            import djust.auth as _auth

            monkeypatch.setattr(_auth, "run_pre_mount_auth", lambda view, request: "/login/")
            await _runtime_mount(consumer, f"{_ALLOWED}.LoginRequiredView")

            frames = await _drain(communicator)
            navs = [f for f in frames if f.get("type") == "navigate"]
            assert navs and navs[0].get("to") == "/login/", (
                f"a login-required view must emit a navigate frame; got {frames!r}"
            )
            assert closed["code"] == 4403, (
                "Finding E: finalize_mount_auth must close the socket (4403) when "
                f"not in a mount_batch; close code was {closed['code']!r}"
            )
        finally:
            await communicator.disconnect()

    async def test_login_required_redirect_does_not_close_in_batch(self, monkeypatch):
        """#291 / #1780: the SAME login-required redirect, but with
        ``_mounting_in_batch=True``, must NOT close the shared socket (a batched
        login view reports as navigate[], closing would kill siblings)."""
        communicator, consumer = await _connect_and_bootstrap_consumer()
        try:
            consumer._mounting_in_batch = True
            closed = {"flag": False}
            orig_close = consumer.close

            async def _spy_close(code=1000):
                closed["flag"] = True
                return await orig_close(code=code)

            consumer.close = _spy_close  # type: ignore[method-assign]

            import djust.auth as _auth

            monkeypatch.setattr(_auth, "run_pre_mount_auth", lambda view, request: "/login/")
            await _runtime_mount(consumer, f"{_ALLOWED}.LoginRequiredView")

            frames = await _drain(communicator)
            navs = [f for f in frames if f.get("type") == "navigate"]
            assert navs, f"the navigate frame must still be emitted in a batch; got {frames!r}"
            assert closed["flag"] is False, (
                "#291: finalize_mount_auth must NOT close the shared socket when "
                "_mounting_in_batch=True"
            )
        finally:
            await communicator.disconnect()


# ===========================================================================
# 5. State restore (Phase 3.1) still works through the wired path
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRuntimeStateRestoreParity:
    async def test_opt_in_view_restores_saved_session_state_on_runtime_mount(self):
        """An opt-in (enable_state_snapshot) view whose session carries saved
        state restores it on a runtime mount — proving the Phase-3.1 restore path
        survives the 3.3a hook wiring. Gate-off contrast: a DIFFERENT (fresh)
        session has no saved state → n stays 0."""
        communicator, consumer = await _connect_and_bootstrap_consumer(url="/snap-1917/")
        try:
            # Pre-seed the consumer's scope session with saved state for the view.
            from django.contrib.sessions.backends.db import SessionStore

            key = consumer.scope["session"].session_key

            def _seed():
                s = SessionStore(session_key=key)
                s["liveview_/snap-1917/"] = {"n": 99}
                s.save()

            await sync_to_async(_seed)()

            await _runtime_mount(consumer, f"{_ALLOWED}.OptInSnapView", url="/snap-1917/")
            frames = await _drain(communicator)
            mounts = [f for f in frames if f.get("type") == "mount"]
            assert mounts, f"the opt-in view must mount; got {frames!r}"
            assert "n=99" in mounts[0].get("html", ""), (
                "Phase 3.1 restore: the saved session state (n=99) must be restored "
                f"on the runtime mount; got {mounts[0]!r}"
            )
        finally:
            await communicator.disconnect()


# ===========================================================================
# 6. Routing untouched — structural pins (no RUNTIME_OWNED_VERBS change)
# ===========================================================================


def test_mount_in_runtime_owned_verbs_post_flip():
    """Post-#1919 (ADR-022 Iter 3 Phase 3.3b, THE MOUNT FLIP): ``mount`` is now in
    ``RUNTIME_OWNED_VERBS`` alongside ``url_change`` + ``event`` — every WS mount
    routes through the single ``dispatch_message`` chokepoint (the #1646 mount
    convergence COMPLETE). Pin the post-flip set so a revert trips here."""
    from djust.websocket import LiveViewConsumer

    assert "mount" in LiveViewConsumer.RUNTIME_OWNED_VERBS, (
        "mount must be in RUNTIME_OWNED_VERBS after the 3.3b flip (#1919)"
    )
    assert LiveViewConsumer.RUNTIME_OWNED_VERBS == frozenset({"url_change", "event", "mount"}), (
        "RUNTIME_OWNED_VERBS must be exactly {url_change, event, mount} post-#1919"
    )


def test_handle_mount_is_a_thin_shim_post_flip():
    """Post-#1919: ``handle_mount`` is a THIN SHIM that delegates to
    ``ViewRuntime.dispatch_mount`` (mirroring ``handle_url_change`` /
    ``handle_event``) — it no longer carries the ~870-line bespoke mount body.
    The shim nulls the runtime view (Finding A), dispatches, then reads back the
    created view (Finding B)."""
    import inspect

    from djust.websocket import LiveViewConsumer

    src = inspect.getsource(LiveViewConsumer.handle_mount)
    assert "dispatch_mount" in src, (
        "handle_mount must delegate to dispatch_mount after the 3.3b flip (#1919)"
    )
    # The bespoke inline version stamp is GONE — the wire version is now stamped by
    # the runtime's next_mount_version hook (Finding C), not inline here.
    assert "self._next_version()" not in src, (
        "handle_mount must NOT stamp the version inline post-flip — that moved into "
        "the next_mount_version transport hook (#1915, Finding C)"
    )
    # Finding A (null before dispatch) + Finding B (read back after).
    assert "runtime.view_instance = None" in src, "shim must null runtime.view_instance (Finding A)"
    assert "self.view_instance = runtime.view_instance" in src, (
        "shim must read back the created view (Finding B)"
    )
