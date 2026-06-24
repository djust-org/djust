"""DORMANT mount-hook scaffolding tests (#1915, ADR-022 Iter 3 Phase 3.2).

Phase 3.2 DEFINES the 5 transport mount-hooks the Phase 3.3b WS-mount flip needs,
exactly how Phase 2.3a defined the event hooks (``event_context`` /
``on_event_recorded`` / ``dispatch_actor_event``) DORMANT before the event flip
wired + routed them. ``dispatch_mount`` does NOT call these hooks yet (Phase 3.3a
wires them), and the bespoke WS ``handle_mount`` keeps doing all of this inline
(untouched until 3.3b). So this PR changes NO live behavior.

The 5 hooks (on the ``Transport`` protocol + ``WSConsumerTransport`` real impl +
``SSESessionTransport`` no-op/raw):

1. ``on_view_instantiated(view)`` — WS: ``view._ws_consumer`` /
   ``_push_events_flush_callback`` (websocket.py:2128/2134-2135), ``register_view``
   observability (2161-2167), ``_websocket_host`` / ``_secure`` stash
   (2268-2270). SSE: no-op.
2. ``uses_actors_for_mount(view) -> bool`` — WS:
   ``use_actors and create_session_actor is not None`` (websocket.py:2213); SSE:
   False. ``dispatch_actor_mount(view, data) -> result`` — WS:
   ``create_session_actor`` + ``actor_handle.mount()`` → ``{html, version}``
   (websocket.py:2213-2217 / 2665-2706), verbatim; SSE: raises.
3. ``next_mount_version(html) -> int`` — WS: ``consumer._next_version()`` (NO arm
   — distinct from ``next_client_version`` which arms; Finding C); SSE: raw Rust
   version (placeholder; raises until 3.3a wires it).
4. ``on_mount_render_ready(view, html) -> html`` — WS: sticky preservation +
   ``sticky_hold`` emission BEFORE the mount frame (websocket.py:2080-2082 /
   2836-2903); returns possibly-adjusted html. SSE: returns html unchanged.
5. ``finalize_mount_auth(view, verdict) -> None`` — WS: the auth-frame
   finalization socket ``close(4403)`` (websocket.py:2337-2401), GATED on
   ``not consumer._mounting_in_batch`` for the redirect verdicts (#291 / #1780).
   SSE: no socket close (no-op).

Each hook gets a MockTransport unit test + (for the WS impl) a real-
``WebsocketCommunicator`` test exercising it in ISOLATION against a genuinely
mounted consumer. Plus a DORMANT-verification suite: ``dispatch_mount`` does not
yet call the hooks (structural pin) and ``handle_mount`` still does the work
inline (unchanged).
"""

from __future__ import annotations

import inspect

import pytest

from djust.runtime import (
    SSESessionTransport,
    Transport,
    WSConsumerTransport,
)


# --------------------------------------------------------------------------- #
# Fakes (unit-level — the WS hook impls read only these consumer attrs)
# --------------------------------------------------------------------------- #


class _FakeView:
    """A plain view object the hooks stamp / read."""

    _view_id = "top-1915"
    request = None

    def __init__(self, *, use_actors: bool = False) -> None:
        if use_actors:
            self.use_actors = True
        # _push_events_flush_callback present so on_view_instantiated wires it.
        self._push_events_flush_callback = None
        self._register_calls: list = []

    def get_context_data(self, **kwargs):
        return {"c": 0}

    def _register_child(self, sticky_id, child):
        self._register_calls.append((sticky_id, child))


class _FakeActorHandle:
    """Stand-in for the Rust ``SessionActorHandle``; ``.mount`` returns a known
    render result."""

    def __init__(self) -> None:
        self.session_id = "actor-sess-1915"
        self.mount_calls: list = []
        self.result = {"html": "<div>from-actor</div>", "version": 7}

    async def mount(self, view_path, context_data, view):
        self.mount_calls.append((view_path, dict(context_data)))
        return self.result


class _FakeConsumer:
    """Minimal ``LiveViewConsumer`` stand-in exposing only what the WS mount
    hooks read. ``_next_version`` is the NO-ARM counter; ``_arm_recovery`` /
    ``_next_version_armed`` are present so we can prove the hook does NOT call
    them (``_recovery_html`` stays None)."""

    def __init__(self, *, mounting_in_batch: bool = False) -> None:
        self.session_id = "sess-1915"
        self.channel_name = "chan.1915"
        self._client_ip = None
        self.scope = {}
        self.use_actors = False
        self.actor_handle = None
        self._mounting_in_batch = mounting_in_batch
        self._sticky_preserved: dict = {}
        self._sticky_auto_reattached: set = set()
        # Version state.
        self._last_sent_version = 0
        self._recovery_html = None
        self._recovery_version = 0
        # Recorded outputs.
        self.send_json_calls: list = []
        self.close_calls: list = []

    # --- version helpers (real shapes from LiveViewConsumer) ---
    def _next_version(self) -> int:
        self._last_sent_version += 1
        return self._last_sent_version

    def _arm_recovery(self, html: str) -> None:
        self._recovery_html = html
        self._recovery_version = self._last_sent_version

    def _next_version_armed(self, html: str) -> int:
        v = self._next_version()
        self._arm_recovery(html)
        return v

    async def _flush_push_events(self) -> None:
        return None

    async def send_json(self, data) -> None:
        self.send_json_calls.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_calls.append(code)


class _FakeSession:
    session_id = "sse-1915"
    _client_ip = None


# --------------------------------------------------------------------------- #
# Hook 1: on_view_instantiated
# --------------------------------------------------------------------------- #


def test_ws_on_view_instantiated_stamps_consumer_backrefs():
    consumer = _FakeConsumer()
    view = _FakeView()
    transport = WSConsumerTransport(consumer)

    transport.on_view_instantiated(view)

    assert view._ws_consumer is consumer, "must stamp view._ws_consumer (websocket.py:2128)"
    assert view._push_events_flush_callback == consumer._flush_push_events, (
        "must wire the push-events flush callback (websocket.py:2134-2135)"
    )
    # host/secure stash present (None/False for a non-browser scope).
    assert hasattr(view, "_websocket_host")
    assert hasattr(view, "_websocket_secure")
    assert view._websocket_host is None and view._websocket_secure is False


def test_sse_on_view_instantiated_is_noop():
    view = _FakeView()
    transport = SSESessionTransport(_FakeSession())
    # SSE must not stamp a consumer back-reference (there is none).
    assert transport.on_view_instantiated(view) is None
    assert not hasattr(view, "_ws_consumer")


# --------------------------------------------------------------------------- #
# Hook 2: uses_actors_for_mount + dispatch_actor_mount
# --------------------------------------------------------------------------- #


def test_ws_uses_actors_for_mount_true_for_actor_view():
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView(use_actors=True)
    # create_session_actor is available in this build (Rust extension loaded).
    from djust.websocket import create_session_actor

    expected = create_session_actor is not None
    assert transport.uses_actors_for_mount(view) is expected
    if expected:
        assert transport.uses_actors_for_mount(view) is True


def test_ws_uses_actors_for_mount_false_for_plain_view():
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    assert transport.uses_actors_for_mount(_FakeView(use_actors=False)) is False


def test_sse_uses_actors_for_mount_always_false():
    transport = SSESessionTransport(_FakeSession())
    assert transport.uses_actors_for_mount(_FakeView(use_actors=True)) is False


@pytest.mark.asyncio
async def test_ws_dispatch_actor_mount_creates_actor_and_returns_result(monkeypatch):
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView(use_actors=True)
    actor = _FakeActorHandle()

    async def _fake_create_session_actor(session_id):
        return actor

    # Patch the factory the WS hook imports from .websocket.
    import djust.websocket as ws_mod

    monkeypatch.setattr(ws_mod, "create_session_actor", _fake_create_session_actor)

    result = await transport.dispatch_actor_mount(
        view, {"view": "myapp.views.ActorView", "params": {}}
    )

    assert consumer.use_actors is True, "the hook must set consumer.use_actors"
    assert consumer.actor_handle is actor, "the created actor must be stashed on the consumer"
    assert actor.mount_calls == [("myapp.views.ActorView", {"c": 0})], (
        "actor_handle.mount must run with (view_path, context_data)"
    )
    assert result == actor.result, "the hook must return the actor mount result {html, version}"


@pytest.mark.asyncio
async def test_sse_dispatch_actor_mount_raises_not_implemented():
    transport = SSESessionTransport(_FakeSession())
    with pytest.raises(NotImplementedError):
        await transport.dispatch_actor_mount(_FakeView(), {"view": "x"})


# --------------------------------------------------------------------------- #
# Hook 3: next_mount_version (NO-ARM — the Finding C distinction)
# --------------------------------------------------------------------------- #


def test_ws_next_mount_version_uses_no_arm_counter():
    """The mount version comes from the consumer counter and does NOT arm recovery
    (mount establishes the baseline, it does not arm request_html recovery)."""
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)

    assert consumer._recovery_html is None
    v = transport.next_mount_version("<div>full pre-strip html</div>")

    assert v == 1, "first mount version on a fresh connection is 1 (no-arm counter)"
    # THE LOAD-BEARING ASSERTION (#1817 / Finding C): recovery was NOT armed.
    assert consumer._recovery_html is None, (
        "next_mount_version must NOT arm recovery — _recovery_html must stay None "
        "(distinct from next_client_version which calls _next_version_armed)"
    )
    assert consumer._recovery_version == 0, "recovery version must not advance on a mount"
    # And it is monotonic with the connection counter.
    assert consumer._last_sent_version == 1


def test_ws_next_mount_version_distinct_from_next_client_version():
    """Proves next_mount_version (no-arm) differs from next_client_version (arms):
    next_client_version arms recovery; next_mount_version does not."""
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)

    # next_client_version ARMS recovery (sets _recovery_html).
    transport.next_client_version("<div>armed</div>", 0)
    assert consumer._recovery_html == "<div>armed</div>", (
        "sanity: next_client_version arms recovery (#1788 / #1817)"
    )

    # Reset, then next_mount_version must NOT arm.
    consumer._recovery_html = None
    transport.next_mount_version("<div>mount</div>")
    assert consumer._recovery_html is None, "next_mount_version must NOT arm recovery"


def test_sse_next_mount_version_raises_until_wired():
    """SSE still stamps the raw Rust version inline (runtime.py:1554); the SSE
    hook is a dormant placeholder that raises until Phase 3.3a routes it."""
    transport = SSESessionTransport(_FakeSession())
    with pytest.raises(NotImplementedError):
        transport.next_mount_version("<div>x</div>")


# --------------------------------------------------------------------------- #
# Hook 4: on_mount_render_ready (sticky preservation + sticky_hold)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ws_on_mount_render_ready_emits_sticky_hold(monkeypatch):
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    view = _FakeView()

    child = object()
    consumer._sticky_preserved = {"slot-a": child}

    # Make the rendered HTML "contain" the surviving slot id. The hook imports
    # ``_find_sticky_slot_ids`` from ``.websocket`` locally, so patch it there.
    import djust.websocket as ws_mod

    monkeypatch.setattr(ws_mod, "_find_sticky_slot_ids", lambda html: {"slot-a"})

    html_in = '<div dj-sticky-slot="slot-a"></div>'
    html_out = await transport.on_mount_render_ready(view, html_in)

    assert html_out == html_in, "on_mount_render_ready returns html unchanged on WS"
    # Survivor re-registered onto the new parent.
    assert view._register_calls == [("slot-a", child)]
    # sticky_hold frame emitted BEFORE the mount frame, with the survivor list.
    assert consumer.send_json_calls == [{"type": "sticky_hold", "views": ["slot-a"]}]
    # consumer._sticky_preserved updated to the authoritative survivor set.
    assert consumer._sticky_preserved == {"slot-a": child}


@pytest.mark.asyncio
async def test_ws_on_mount_render_ready_noop_when_no_stickys():
    consumer = _FakeConsumer()
    transport = WSConsumerTransport(consumer)
    consumer._sticky_preserved = {}

    html_out = await transport.on_mount_render_ready(_FakeView(), "<div>plain</div>")

    assert html_out == "<div>plain</div>"
    assert consumer.send_json_calls == [], "no sticky_hold frame when nothing was staged"


@pytest.mark.asyncio
async def test_sse_on_mount_render_ready_returns_html_unchanged():
    transport = SSESessionTransport(_FakeSession())
    html = "<div>sse</div>"
    assert await transport.on_mount_render_ready(_FakeView(), html) == html


# --------------------------------------------------------------------------- #
# Hook 5: finalize_mount_auth (the #291 close-gate)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ws_finalize_mount_auth_permission_denied_closes_unconditionally():
    consumer = _FakeConsumer(mounting_in_batch=False)
    transport = WSConsumerTransport(consumer)
    await transport.finalize_mount_auth(_FakeView(), "permission_denied")
    assert consumer.close_calls == [4403], "permission_denied closes the socket (4403)"


@pytest.mark.asyncio
async def test_ws_finalize_mount_auth_redirect_closes_when_not_in_batch():
    consumer = _FakeConsumer(mounting_in_batch=False)
    transport = WSConsumerTransport(consumer)
    await transport.finalize_mount_auth(_FakeView(), "redirect")
    assert consumer.close_calls == [4403], "redirect closes when not in a mount_batch"


@pytest.mark.asyncio
async def test_ws_finalize_mount_auth_redirect_does_not_close_in_batch():
    """#291 / #1780: a batched login-required view must NOT close the shared
    socket (it reports as navigate[], not a bypass)."""
    consumer = _FakeConsumer(mounting_in_batch=True)
    transport = WSConsumerTransport(consumer)
    assert transport.mounting_in_batch is True

    await transport.finalize_mount_auth(_FakeView(), "redirect")

    assert consumer.close_calls == [], (
        "#291: finalize_mount_auth must NOT close(4403) when _mounting_in_batch=True"
    )


@pytest.mark.asyncio
async def test_ws_finalize_mount_auth_hook_redirect_respects_batch_gate():
    consumer = _FakeConsumer(mounting_in_batch=True)
    transport = WSConsumerTransport(consumer)
    await transport.finalize_mount_auth(_FakeView(), "hook_redirect")
    assert consumer.close_calls == [], "hook_redirect honors the same #291 batch gate"


@pytest.mark.asyncio
async def test_sse_finalize_mount_auth_never_closes():
    session_closes: list = []

    class _SessWithClose(_FakeSession):
        async def close(self, code=1000):
            session_closes.append(code)

    transport = SSESessionTransport(_SessWithClose())
    assert await transport.finalize_mount_auth(_FakeView(), "redirect") is None
    assert session_closes == [], "SSE has no socket to drop on a mount-auth verdict"


def test_sse_mounting_in_batch_always_false():
    transport = SSESessionTransport(_FakeSession())
    assert transport.mounting_in_batch is False


# --------------------------------------------------------------------------- #
# Protocol + adapter surface (all 5 hooks present on Protocol + both adapters)
# --------------------------------------------------------------------------- #

_MOUNT_HOOKS = (
    "on_view_instantiated",
    "uses_actors_for_mount",
    "dispatch_actor_mount",
    "next_mount_version",
    "on_mount_render_ready",
    "finalize_mount_auth",
    "mounting_in_batch",
)


@pytest.mark.parametrize("hook", _MOUNT_HOOKS)
def test_hook_on_protocol_and_both_adapters(hook):
    assert hasattr(Transport, hook), f"Transport protocol must declare {hook}"
    assert hasattr(WSConsumerTransport, hook), f"WSConsumerTransport must implement {hook}"
    assert hasattr(SSESessionTransport, hook), f"SSESessionTransport must implement {hook}"


def test_protocol_defaults_are_behavior_preserving():
    """The Protocol-level defaults must be no-op / refuse so existing callers +
    SSE are unaffected — the dormant-scaffolding contract."""

    class _BareTransport:
        """A duck-typed transport that does NOT override the mount hooks — relies
        on the Protocol defaults via explicit delegation below."""

    # The Protocol defaults: on_view_instantiated returns None; uses_actors_for_mount
    # False; dispatch_actor_mount raises; next_mount_version raises; on_mount_render_ready
    # returns html; finalize_mount_auth returns None; mounting_in_batch False.
    assert Transport.on_view_instantiated(Transport, _FakeView()) is None  # type: ignore[arg-type]
    assert Transport.uses_actors_for_mount(Transport, _FakeView()) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# DORMANT verification: dispatch_mount does NOT call the new hooks yet,
# and the bespoke WS handle_mount still does the work inline (#1915).
# --------------------------------------------------------------------------- #


def _dispatch_mount_source() -> str:
    from djust.runtime import ViewRuntime

    return inspect.getsource(ViewRuntime.dispatch_mount)


@pytest.mark.parametrize(
    "hook",
    [
        "on_view_instantiated",
        "uses_actors_for_mount",
        "dispatch_actor_mount",
        "next_mount_version",
        "on_mount_render_ready",
        "finalize_mount_auth",
    ],
)
def test_dispatch_mount_does_not_call_hook_yet(hook):
    """DORMANT pin (#1915): Phase 3.2 only DEFINES the hooks; Phase 3.3a wires
    them. ``dispatch_mount`` must not reference any of the 5 mount hooks yet."""
    src = _dispatch_mount_source()
    assert f".{hook}(" not in src, (
        f"dispatch_mount must NOT call transport.{hook}() until Phase 3.3a "
        f"(this PR is DORMANT scaffolding only)"
    )


def test_dispatch_mount_still_stamps_raw_rust_version_inline():
    """Finding C dormant proof: dispatch_mount still stamps the raw Rust version
    from render_with_diff() inline (NOT via next_mount_version) until 3.3a."""
    src = _dispatch_mount_source()
    assert "render_with_diff" in src, "dispatch_mount still renders inline"
    assert '"version": version' in src, (
        "dispatch_mount stamps the raw render_with_diff version on the mount frame "
        "(Finding C: next_mount_version is NOT wired in yet)"
    )
    assert "next_mount_version" not in src, "next_mount_version must not be wired yet"


def test_dispatch_mount_still_refuses_actor_mount():
    """Finding D dormant proof: dispatch_mount still REFUSES use_actors (it does
    not yet route through uses_actors_for_mount / dispatch_actor_mount)."""
    src = _dispatch_mount_source()
    assert 'getattr(view_instance, "use_actors", False)' in src, (
        "dispatch_mount still has the inline use_actors refusal guard"
    )
    assert "is not supported over SSE" in src, "the actor refusal envelope is still inline"


def test_bespoke_handle_mount_still_does_work_inline():
    """The WS bespoke handle_mount keeps doing everything inline (untouched until
    3.3b). Pin the inline sites the hooks will later replace."""
    from djust.websocket import LiveViewConsumer

    src = inspect.getsource(LiveViewConsumer.handle_mount)
    # Hook 1 sites
    assert "_ws_consumer = self" in src
    # Hook 3 site — handle_mount stamps the NO-ARM consumer version inline.
    assert "self._next_version()" in src, (
        "handle_mount still stamps version via self._next_version() inline (Finding C)"
    )
    # Hook 5 site — the #291 batch-gated close is still inline.
    assert "_mounting_in_batch" in src, "the #291 close-gate is still inline in handle_mount"
    assert "close(code=4403)" in src


# --------------------------------------------------------------------------- #
# Real-``WebsocketCommunicator`` tests: exercise the WS hook IMPLS in ISOLATION
# against a genuinely-mounted ``LiveViewConsumer`` (no MockTransport). The
# consumer is established by a real connect+mount; we retrieve it via the
# observability registry (``view._ws_consumer`` is stamped inline by the
# bespoke handle_mount), wrap it in ``WSConsumerTransport``, and call the hooks
# directly — proving the WS impls behave correctly against the real consumer.
# --------------------------------------------------------------------------- #

pytest.importorskip("channels")

from asgiref.sync import sync_to_async  # noqa: E402
from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402

_ALLOWED = "djust.tests.test_transport_mount_hooks_1915"


class CommPlainView(LiveView):
    template = f'<div dj-root dj-view="{_ALLOWED}.CommPlainView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class CommActorView(LiveView):
    """``use_actors=True`` view — uses_actors_for_mount must report True for the
    consumer that mounted it."""

    use_actors = True
    template = f'<div dj-root dj-view="{_ALLOWED}.CommActorView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect_mount_and_get_consumer(view_path: str, url: str = "/mh-1915/"):
    """Connect a real ``WebsocketCommunicator``, mount ``view_path``, and return
    ``(communicator, consumer, mount_frame)`` — ``consumer`` is the LIVE
    ``LiveViewConsumer`` retrieved from the observability registry via
    ``view._ws_consumer`` (stamped by the bespoke handle_mount)."""
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

    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = None
    for _ in range(8):
        mount_frame = await communicator.receive_json_from(timeout=3)
        if mount_frame.get("type") == "mount":
            break
    assert mount_frame and mount_frame.get("type") == "mount", (
        f"expected a mount frame, got {mount_frame!r}"
    )

    session_id = mount_frame.get("session_id")
    view = get_view_for_session(session_id)
    assert view is not None, "the mounted view must be in the observability registry"
    consumer = getattr(view, "_ws_consumer", None)
    assert consumer is not None, "the live consumer must be reachable via view._ws_consumer"
    return communicator, consumer, mount_frame


@override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED])
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_real_ws_uses_actors_for_mount_true_for_actor_view():
    communicator, consumer, _ = await _connect_mount_and_get_consumer(f"{_ALLOWED}.CommActorView")
    try:
        from djust.websocket import create_session_actor

        transport = WSConsumerTransport(consumer)
        # The actor view mounted in actor mode, so the live consumer has
        # use_actors=True (+ an actor_handle when the Rust factory is present).
        if create_session_actor is not None:
            assert transport.uses_actors_for_mount(consumer.view_instance) is True, (
                "uses_actors_for_mount must be True for a use_actors view's live consumer"
            )
        # A plain view object on the SAME consumer would not change the result
        # (the gate keys on view.use_actors): a non-actor view → False.
        assert transport.uses_actors_for_mount(_FakeView(use_actors=False)) is False
    finally:
        await communicator.disconnect()


@override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED])
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_real_ws_next_mount_version_does_not_arm_recovery():
    """#1817 / Finding C, against a REAL consumer: next_mount_version returns the
    consumer's monotonic counter and does NOT arm request_html recovery —
    ``_recovery_html`` stays None."""
    communicator, consumer, _ = await _connect_mount_and_get_consumer(f"{_ALLOWED}.CommPlainView")
    try:
        transport = WSConsumerTransport(consumer)
        before = getattr(consumer, "_recovery_html", None)
        last_before = getattr(consumer, "_last_sent_version", 0)

        v = transport.next_mount_version("<div>full pre-strip html</div>")

        assert v == last_before + 1, "next_mount_version advances the consumer counter by 1"
        # THE LOAD-BEARING ASSERTION: recovery was NOT armed by the mount-version call.
        assert getattr(consumer, "_recovery_html", None) == before, (
            "next_mount_version must NOT arm recovery on a real consumer "
            "(_recovery_html must be unchanged — distinct from next_client_version)"
        )
    finally:
        await communicator.disconnect()


@override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED])
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_real_ws_finalize_mount_auth_does_not_close_in_batch():
    """#291 / #1780, against a REAL consumer: with _mounting_in_batch=True the
    redirect-verdict finalization must NOT close the shared socket."""
    communicator, consumer, _ = await _connect_mount_and_get_consumer(f"{_ALLOWED}.CommPlainView")
    try:
        transport = WSConsumerTransport(consumer)
        consumer._mounting_in_batch = True
        assert transport.mounting_in_batch is True

        closed = {"flag": False}
        orig_close = consumer.close

        async def _spy_close(code=1000):
            closed["flag"] = True
            return await orig_close(code=code)

        consumer.close = _spy_close  # type: ignore[method-assign]
        await transport.finalize_mount_auth(consumer.view_instance, "redirect")

        assert closed["flag"] is False, (
            "#291: finalize_mount_auth must NOT close the shared socket when "
            "_mounting_in_batch=True"
        )

        # And the gate-off direction: not in a batch → it DOES close.
        consumer.close = orig_close  # type: ignore[method-assign]
        consumer._mounting_in_batch = False
        closed["flag"] = False
        consumer.close = _spy_close  # type: ignore[method-assign]
        await transport.finalize_mount_auth(consumer.view_instance, "redirect")
        assert closed["flag"] is True, (
            "with _mounting_in_batch=False the redirect verdict DOES close(4403)"
        )
    finally:
        consumer.close = orig_close  # type: ignore[method-assign]
        await communicator.disconnect()
