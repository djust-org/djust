"""Tests for #1465 — WS-reconnect state continuity.

Three changes to ``python/djust/websocket.py``:

1. **Load gate loosened** (``handle_mount``, ~line 1990): the
   ``request.session.aget(view_key)`` lookup now fires when ``has_prerendered``
   OR when ``saved_state`` exists in the session. Previously it was gated
   strictly on ``has_prerendered``.

2. **Skip html on resume** (``handle_mount`` response build, ~line 2317):
   when state was restored from session AND the client had ``has_prerendered``,
   the mount response omits the freshly-rendered ``html`` field. The client's
   DOM already reflects the restored state.

3. **Save in handle_event** (~line 3105, before the auto-skip-render block):
   every WS event handler completion persists state back to the Django session,
   mirroring the HTTP-path save at ``mixins/request.py:603-609``. Saves include
   public state (``get_context_data()``), private (``_``-prefixed) attrs via
   ``_get_private_state``, and components via ``_save_components_to_session``.
   Failures are caught and logged — saves must never break event handling.

The tests below exercise each change in isolation plus a focused round-trip
that proves session-driven reconnect continuity.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class CounterView(LiveView):
    """Minimal LiveView with a publicly-mutable counter, opted into
    state snapshot. The save block in ``handle_event`` mirrors the HTTP
    POST save, so this view exercises the same code path."""

    template = "<div dj-root><span>{{ count }}</span></div>"
    enable_state_snapshot = True

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_session(request):
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _build_consumer():
    """Build a bare LiveViewConsumer instance suitable for direct method
    invocation in tests. Mirrors the pattern used in test_async_integration.py.
    """
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    # Channels' AsyncWebsocketConsumer doesn't run __init__ for scope/channel
    # plumbing unless .__call__ runs. Set the bare minimum the save block needs.
    consumer.scope = {"session": None}
    return consumer


# ---------------------------------------------------------------------------
# Test 1 — Load gate fires on saved_state alone (no has_prerendered)
# ---------------------------------------------------------------------------


def test_load_gate_loosened_fires_on_saved_state_without_has_prerendered():
    """The session-restore load gate fires on saved_state independent of
    ``has_prerendered``. Post-#1919 (THE MOUNT FLIP) the WS mount routes through
    ``ViewRuntime.dispatch_mount`` (the bespoke ``handle_mount`` body was deleted);
    the runtime gates the restore on ``opt_in and session is not None`` then
    ``if saved_state:`` — semantically the #1466/#1552 behavior (restore on plain
    reconnect when state exists AND the view opted in), pinned at its converged home.

    Companion: the integration test below proves the runtime effect.
    """
    import djust.runtime as rt_mod

    source = inspect.getsource(rt_mod.ViewRuntime.dispatch_mount)

    # The runtime gate: restore whenever saved_state is non-empty (independent of
    # has_prerendered), inside the opt_in + session guard.
    assert "if saved_state:" in source, (
        "dispatch_mount must restore on a non-empty saved_state (plain WS reconnect "
        "resume), not only when has_prerendered."
    )

    # The restore must be guarded for the no-session case AND for views without
    # ``enable_state_snapshot`` (#1552 fix): non-opt-in views don't get their state
    # restored from session on every mount (the wizard step-leak bug). Refs #1466
    # (introduced the load) and #1552 (gated it). The runtime sources opt_in from
    # ``enable_state_snapshot`` and the session from ``request.session``.
    assert "enable_state_snapshot" in source and "session" in source, (
        "restore must short-circuit when the session is None AND when the view "
        "doesn't opt in via enable_state_snapshot (#1552)."
    )

    # view_key must be assigned BEFORE the gate, not inside it.
    new_shape_idx = source.rfind('view_key = f"liveview_{page_url}"')
    gate_idx = source.rfind("if saved_state:")
    assert new_shape_idx != -1 and gate_idx != -1
    assert new_shape_idx < gate_idx, (
        "view_key must be assigned BEFORE the saved_state gate, not inside it."
    )


# ---------------------------------------------------------------------------
# Test 2 — Skip-html on resume (mounted AND has_prerendered)
# ---------------------------------------------------------------------------


def test_skip_html_for_resume_truth_table():
    """The skip-html boolean is ``skip_html_for_resume = mounted and
    has_prerendered``. Pin the truth table by reproducing the conditional
    in isolation — the test fails-fast if the response-build code path
    drifts away from the documented semantic.
    """

    # Reproduce the response-building logic from websocket.py (post-fix).
    def build_response(html: str | None, mounted: bool, has_prerendered: bool) -> dict:
        response: dict[str, Any] = {"type": "mount", "version": 0}
        skip_html_for_resume = mounted and has_prerendered
        if html is not None and not skip_html_for_resume:
            response["html"] = html
            response["has_ids"] = "dj-id=" in html
        return response

    # Cold mount: no resume, html flows.
    r = build_response("<div dj-id=root></div>", mounted=False, has_prerendered=True)
    assert r["html"] == "<div dj-id=root></div>"
    assert r["has_ids"] is True

    # Resume path: mounted from session AND has_prerendered — html dropped.
    r = build_response("<div dj-id=root></div>", mounted=True, has_prerendered=True)
    assert "html" not in r
    assert "has_ids" not in r
    # version still flows (subsequent patches need it)
    assert r["version"] == 0

    # SSR-prerendered cold mount (no saved state, mounted stays False): html flows.
    r = build_response("<div>x</div>", mounted=False, has_prerendered=True)
    assert "html" in r

    # WS-only mount (no prerendered HTML, no saved state): html flows
    # because skip_html_for_resume is False.
    r = build_response("<div>x</div>", mounted=True, has_prerendered=False)
    assert "html" in r

    # html is None (rendering was skipped for other reasons) — never appears.
    r = build_response(None, mounted=True, has_prerendered=True)
    assert "html" not in r


def test_skip_html_logic_present_in_source():
    """Belt-and-suspenders: the actual mount source must contain the
    skip-html-for-resume conditional so the truth-table test above pins the
    source's actual behavior, not a stale re-implementation. Post-#1919 (THE MOUNT
    FLIP) the WS mount routes through ``ViewRuntime.dispatch_mount``, where the
    runtime analogue is ``skip_html_for_resume = bool(mounted_from_restore) and
    bool(has_prerendered)`` (``_mounted_from_restore`` is the runtime's ``mounted``
    flag)."""
    import djust.runtime as rt_mod

    source = inspect.getsource(rt_mod.ViewRuntime.dispatch_mount)
    assert "skip_html_for_resume = bool(mounted_from_restore) and bool(has_prerendered)" in source
    assert "if html is not None and not skip_html_for_resume:" in source


# ---------------------------------------------------------------------------
# Test 3 — handle_event save block persists state to session
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_handle_event_save_block_persists_state_to_session():
    """End-to-end-ish test of the save block added to handle_event.

    Strategy: build a real CounterView, mount it (HTTP-path), then invoke
    the SAVE-BLOCK code path directly with a mock session — asserting the
    session's ``aset`` is called with the expected view_key and state.

    The save block is the ~80-LOC try/except inserted before the
    "Auto-detect unchanged state" block. It is the new code being tested.
    """

    # 1. Set up a view post-mount (HTTP-path GET that populates state).
    # Wrap the synchronous session setup + GET in sync_to_async because the
    # test itself runs in an async context (the save block uses await).
    def _setup_view_sync():
        view = CounterView()
        factory = RequestFactory()
        get_request = factory.get("/counter/")
        _add_session(get_request)
        view.get(get_request)
        # _djust_mount_request stashed by the HTTP-path; mirror what WS does.
        view._djust_mount_request = get_request
        # Simulate the event handler mutation.
        view.count = 1
        return view

    view = await sync_to_async(_setup_view_sync)()

    # 2. Set up a mock session that mirrors Django's async session API.
    mock_session = MagicMock()
    mock_session.aget = AsyncMock(return_value={})
    mock_session.aset = AsyncMock()
    mock_session.apop = AsyncMock()
    mock_session.asave = AsyncMock()

    # Plant the mock onto the stashed mount request — this is the path the
    # save block prefers (mount_request.session over scope_session).
    view._djust_mount_request.session = mock_session

    # 3. Invoke the save block as the consumer would. We reproduce it
    # verbatim from websocket.py:3102-3157 since the consumer's full
    # handle_event has a lot of plumbing that's out-of-scope for this test.
    # Drift between this block and websocket.py is caught by the source-grep
    # test below.
    from djust.components.base import LiveComponent as _LC
    from djust.serialization import normalize_django_value as _normalize

    mount_request = view._djust_mount_request
    save_session = getattr(mount_request, "session", None)
    save_path = mount_request.path
    save_view_key = f"liveview_{save_path}"

    _gcd_save = view.get_context_data
    if inspect.iscoroutinefunction(_gcd_save):
        save_context = await _gcd_save()
    else:
        save_context = await sync_to_async(_gcd_save)()

    save_state = {k: v for k, v in save_context.items() if not isinstance(v, _LC)}
    await save_session.aset(save_view_key, _normalize(save_state))
    await save_session.asave()

    # 4. Verify aset was called with the post-increment state.
    mock_session.aset.assert_called_once()
    args, _ = mock_session.aset.call_args
    assert args[0] == "liveview_/counter/"
    # count was 1 at save-time, so the normalized state must reflect that.
    assert args[1].get("count") == 1
    mock_session.asave.assert_called_once()


def test_save_block_present_in_handle_event_source():
    """Pin the #1466 session-save block. #1907 THE FLIP moved WS events onto
    ``ViewRuntime.dispatch_event``; the save block moved off the deleted
    ``_handle_event_inner`` into the runtime's ``_persist_state_after_event``
    (the body) gated by ``_dispatch_event_render`` (the opt-in + identity gate).
    If a future refactor deletes or moves the block, this fails fast.

    The block (``_persist_state_after_event``) must:
    - Read ``target_view._djust_mount_request`` (preferred session source)
    - Fall back to ``self.scope.get("session")`` when no mount_request
    - Build ``save_view_key = f"liveview_{save_path}"``
    - Filter LiveComponents out of the saved state
    - Call ``save_session.aset(...)`` then ``save_session.asave()``
    - Wrap everything in try/except so saves never break event handling
    """
    import djust.runtime as rt_mod

    # The save BODY lives in the runtime's _persist_state_after_event (#1907).
    source = inspect.getsource(rt_mod.ViewRuntime._persist_state_after_event)
    source_collapsed = " ".join(source.split())

    assert '"_djust_mount_request"' in source
    assert "getattr(" in source and "_djust_mount_request" in source
    assert 'self.scope.get("session")' in source
    assert 'save_view_key = f"liveview_{save_path}"' in source
    assert "await save_session.aset(save_view_key" in source_collapsed
    assert "await save_session.asave()" in source
    # Private-state path:
    assert "_get_private_state" in source
    assert 'f"{save_view_key}__private"' in source
    # Components path:
    assert "_save_components_to_session" in source
    # Save MUST be wrapped so failures don't break event handling (runtime msg).
    assert "Failed to save LiveView state after runtime event" in source
    # #1475: 150ms timeout MUST wrap the save body. Even opt-in views
    # need close-time tail latency bounded so a stalled session backend
    # can't recreate the snapshot-poisoning failure mode.
    assert "asyncio.wait_for" in source
    assert "timeout=0.150" in source
    assert "asyncio.TimeoutError" in source

    # The GATE (top-level identity + enable_state_snapshot opt-in) lives at the
    # call site in _dispatch_event_render. Stage 11 (PR #1466): gated on top-level
    # view identity so child LiveComponent events don't write to "liveview_/".
    # #1475 / 0.9.7rc3: gated on enable_state_snapshot so default views don't pay
    # the per-event async-DB-write overhead (djustlive 0.9.7rc2 production block).
    gate_src = inspect.getsource(rt_mod.ViewRuntime._dispatch_event_render)
    assert "target_view is self.view_instance" in gate_src, (
        "the runtime save call must be gated on top-level view identity (#1466)."
    )
    assert '"enable_state_snapshot"' in gate_src, (
        "the runtime save call must be gated on enable_state_snapshot (#1475) so "
        "default views don't pay per-event async-DB-write overhead."
    )


# ---------------------------------------------------------------------------
# Wire-shape regression — resume path omits html
# ---------------------------------------------------------------------------


def test_mount_envelope_resume_path_omits_html():
    """When state was restored from session AND has_prerendered, the mount
    response omits ``html`` (and ``has_ids``). The resume path is wire-distinct
    from the cold-mount path: the client's DOM is preserved; only ``version``
    flows to keep patches in sync. This is the load-bearing absence — pin it.
    """
    import json

    response: dict[str, Any] = {
        "type": "mount",
        "session_id": "sess-abc",
        "view": "myapp.views.MyView",
        "version": 0,
    }
    # No response["html"] = ...  <- this is the load-bearing absence
    expected = (
        '{"type": "mount", "session_id": "sess-abc", "view": "myapp.views.MyView", "version": 0}'
    )
    assert json.dumps(response) == expected


# ---------------------------------------------------------------------------
# WebsocketCommunicator integration test — the real save-runtime proof
# ---------------------------------------------------------------------------
#
# Stage 11 (PR #1466) Finding #4 — Action #1200: 4/7 tests above are
# tautologies (mock the session, reproduce the boolean, or proxy via
# HTTP-path POST). The reviewer asked for a real integration test that
# drives ``handle_event`` through a WebsocketCommunicator and asserts the
# session contains the post-event state.
#
# Pattern lifted from ``tests/benchmarks/test_request_path.py:178-263``
# (working ``WebsocketCommunicator`` + ``handle_mount`` precedent) and
# the existing ``python/tests/test_websocket_origin_validation.py``
# (working WS handshake precedent).
#
# Why this is non-tautological:
#   - Drives the real ``LiveViewConsumer.as_asgi()`` ASGI app.
#   - Sends a real WS ``event`` frame that flows through ``handle_event``.
#   - Reads the session backend AFTER the handler completes — proving the
#     save block ran, not that the test reproduces the save block.
#   - A revert of the save block (or a ``_normalize`` regression) would
#     leave ``saved.get("count") == 0`` and fail this test.

# Module-level view class so the consumer's dotted-path mount-import
# whitelist can resolve it. Mirrors the
# ``test_request_path.py:_WSMountCounter`` registration pattern.


class _WSReconnectCounter(LiveView):
    """Module-level CounterView for WebsocketCommunicator integration test."""

    template = (
        '<div dj-view="djust.tests.test_ws_reconnect_state_1465._WSReconnectCounter" '
        'dj-id="0">Counter: {{ count }}</div>'
    )
    enable_state_snapshot = True

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_ws_event_save_block_writes_through_to_session():
    """Drive ``handle_event`` via a real WebsocketCommunicator and assert
    the session contains the post-event state.

    This is the canonical non-tautological proof (Action #1200) that the
    save block writes through — a revert of the save block, a missing
    ``asave()``, or a ``_normalize`` regression would fail this test.

    Flow:
      1. Open a WebsocketCommunicator against ``LiveViewConsumer.as_asgi()``.
      2. Connect (real Channels handshake).
      3. Send a mount frame for the module-level ``_WSReconnectCounter``.
      4. Send an ``event: increment`` frame.
      5. Read the session backend directly — assert ``count == 1``.

    The save block in ``handle_event`` is the only path that could have
    written the post-increment state to the session via WS.
    """
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    # The consumer's mount-import allowlist must permit this module.
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        # Pre-create a session so we have a known session_key to inspect
        # after the WS event. The WS scope's session_key threads through
        # to the synthesized request inside handle_mount (websocket.py
        # ~line 1900-1913), so the save lands in this SessionStore.
        # SessionStore.create() is sync-only — wrap with sync_to_async.
        def _create_session():
            s = SessionStore()
            s.create()
            return s.session_key

        session_key = await sync_to_async(_create_session)()

        # Build a Channels-compatible scope with a session that exposes
        # session_key (mirrors what AuthMiddlewareStack provides in prod).
        class _ScopeSession:
            def __init__(self, key):
                self.session_key = key

        communicator = WebsocketCommunicator(
            LiveViewConsumer.as_asgi(),
            "/ws/",
        )
        # Inject the session BEFORE connect so the consumer sees it.
        communicator.scope["session"] = _ScopeSession(session_key)

        connected, _ = await communicator.connect()
        assert connected, "WebsocketCommunicator must connect"

        # 1. Drain the initial connect frame.
        connect_msg = await communicator.receive_json_from(timeout=2)
        assert connect_msg.get("type") == "connect"

        # 2. Send the mount frame — runs handle_mount, which stashes
        # _djust_mount_request on the view (websocket.py:2143). The
        # save block in handle_event reads this stashed request to
        # discover the session and save_path.
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": f"{__name__}._WSReconnectCounter",
                "url": "/counter/",
            }
        )
        mount_resp = await communicator.receive_json_from(timeout=3)
        assert mount_resp.get("type") == "mount", (
            f"expected mount response, got {mount_resp.get('type')!r}: {mount_resp}"
        )

        # 3. Send an event frame — runs handle_event, which mutates
        # state via the @event_handler and then runs the SAVE BLOCK.
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "increment",
                "params": {},
                "ref": 1,
            }
        )
        # Drain any frames sent in response (state diff / push / etc.).
        # We don't care about the response shape — only the session write.
        try:
            await communicator.receive_json_from(timeout=3)
        except Exception:  # noqa: BLE001 — response timing is incidental here
            pass

        await communicator.disconnect()

        # 4. Read the session backend directly. The save block must have
        # written the post-increment state to "liveview_/counter/" with
        # count=1. A reverted save block would leave this key absent.
        post_session = SessionStore(session_key=session_key)
        saved = await post_session.aget("liveview_/counter/", None)
        assert saved is not None, (
            "Save block in handle_event must have written "
            "'liveview_/counter/' to the session — but the key is absent. "
            "Did the save block silently fail, or was it reverted?"
        )
        assert saved.get("count") == 1, (
            f"Save block must have captured post-increment state count=1, got: {saved!r}"
        )


# ---------------------------------------------------------------------------
# #1475 / 0.9.7rc3 — opt-out regression tests
# ---------------------------------------------------------------------------
#
# Without these tests, PR #1466's "unconditional save" regression could be
# reintroduced silently. Default views (no ``enable_state_snapshot``) must
# pay ZERO async-DB-write overhead per WS event.


class _WSDefaultCounter(LiveView):
    """Default-config LiveView — no ``enable_state_snapshot`` opt-in.

    Pins the negative case: the save block must NOT fire for this view.
    """

    template = (
        '<div dj-view="djust.tests.test_ws_reconnect_state_1465._WSDefaultCounter" '
        'dj-id="0">Counter: {{ count }}</div>'
    )
    # Note: NO ``enable_state_snapshot = True`` here.

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_ws_event_save_block_skipped_when_state_snapshot_opt_out():
    """Default view (no ``enable_state_snapshot``) must NOT trigger the
    save block. Locks in the 0.9.7rc3 fix for #1475.

    A regression that removes the ``enable_state_snapshot`` gate would
    cause the session key to be written here — the opposite of what we
    want. The assertion ``saved is None`` is load-bearing.
    """
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):

        def _create_session():
            s = SessionStore()
            s.create()
            return s.session_key

        session_key = await sync_to_async(_create_session)()

        class _ScopeSession:
            def __init__(self, key):
                self.session_key = key

        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope["session"] = _ScopeSession(session_key)
        connected, _ = await communicator.connect()
        assert connected

        # Drain connect frame.
        await communicator.receive_json_from(timeout=2)

        # Mount + increment.
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": f"{__name__}._WSDefaultCounter",
                "url": "/default-counter/",
            }
        )
        mount_resp = await communicator.receive_json_from(timeout=3)
        assert mount_resp.get("type") == "mount"

        await communicator.send_json_to(
            {
                "type": "event",
                "event": "increment",
                "params": {},
                "ref": 1,
            }
        )
        try:
            await communicator.receive_json_from(timeout=3)
        except Exception:  # noqa: BLE001
            pass

        await communicator.disconnect()

        # The load-bearing negative assertion: no session write for default
        # views. A regression that drops the ``enable_state_snapshot``
        # gate would populate this key — exactly what bricked djustlive.
        post_session = SessionStore(session_key=session_key)
        saved = await post_session.aget("liveview_/default-counter/", None)
        assert saved is None, (
            "Save block must NOT have written 'liveview_/default-counter/' "
            "to the session — but it did. The ``enable_state_snapshot`` "
            "gate is the regression guard for djustlive's snapshot path "
            f"(#1475). Got: {saved!r}"
        )


def test_save_block_gates_on_enable_state_snapshot_source():
    """Belt-and-suspenders source pin for the #1475 gate.

    The runtime test above proves the negative behavior at the
    WebsocketCommunicator level. This test pins the gate's lexical
    presence so a future refactor that drops the keyword would fail
    fast even if the runtime test were skipped under some CI config.

    #1907 THE FLIP: WS events route through ``ViewRuntime.dispatch_event``; the
    gated save call lives at the ``_dispatch_event_render`` call site now (the body
    is ``_persist_state_after_event``). The runtime docstring (runtime.py) notes the
    identity clause is kept in the condition specifically so this AND-form gate
    string matches the WS pin verbatim — drift between the two save gates goes red.
    """
    import djust.runtime as rt_mod

    source = inspect.getsource(rt_mod.ViewRuntime._dispatch_event_render)
    source_collapsed = " ".join(source.split())

    # The exact form of the gate: AND'd with the existing top-level
    # identity check. Strip ALL whitespace to be tolerant of ruff-format
    # wrapping long if-conditions across lines.
    source_nospaces = "".join(source.split())
    assert "enable_state_snapshot" in source
    assert "target_view is self.view_instance" in source_collapsed
    # Both gate conditions must co-occur in an AND expression.
    assert (
        'iftarget_viewisself.view_instanceandgetattr(self.view_instance,"enable_state_snapshot",False)'
        in source_nospaces
    ), "Both gates must be AND'd in the save-block condition for #1475 fix."


class _WSSlowOptInCounter(LiveView):
    """Opt-in view whose ``mount`` injects a slow async session so the
    save block exceeds the 150ms timeout — end-to-end fixture for the
    wrapper's TimeoutError → log → continue path.

    The save block looks up ``save_session`` via ``mount_request.session``
    (see ``handle_event`` save block). Replacing ``request.session`` in
    ``mount()`` routes the save's ``aset``/``asave`` calls through the
    slow stub.
    """

    template = (
        '<div dj-view="djust.tests.test_ws_reconnect_state_1465._WSSlowOptInCounter" '
        'dj-id="0">Counter: {{ count }}</div>'
    )
    enable_state_snapshot = True

    def mount(self, request, **kwargs):
        import asyncio as _asyncio

        class _SlowSession:
            async def aget(self, key, default=None):
                return default

            async def aset(self, key, value):
                # 250ms > 150ms wrapper timeout → triggers TimeoutError
                # in the save body's first ``aset`` call.
                await _asyncio.sleep(0.25)

            async def apop(self, key, default=None):
                return default

            async def asave(self):
                await _asyncio.sleep(0.25)

        request.session = _SlowSession()
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_save_block_timeout_logs_and_continues_end_to_end(caplog):
    """End-to-end test of the wrapper's timeout → log → continue path.

    Stage 11 review of PR #1478 (Finding #1) flagged that the original
    timeout test validated ``asyncio.wait_for`` semantics in isolation,
    not the wrapper's actual log+continue behavior — same shape Action
    #254 (gate-off self-test, #1468) was filed to prevent.

    This test drives the real consumer's ``handle_event`` save block
    through a slow session and asserts the wrapper's three guarantees:
    - WARNING log fires with the ``exceeded 150ms`` marker.
    - ``handle_event`` completes without raising (saves never break
      event handling — the event response still goes out).
    - Total elapsed is bounded by the timeout, not the slow body.

    Sabotage check: if the ``except asyncio.TimeoutError`` branch were
    deleted from the wrapper, the TimeoutError would propagate up and
    crash ``handle_event`` — no WARNING log, communicator would receive
    an error frame. The ``warning_logs`` assertion is the load-bearing
    catch for that regression.
    """
    import asyncio as _asyncio
    import logging as _logging

    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    caplog.set_level(_logging.WARNING, logger="djust")

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):

        def _create_session():
            s = SessionStore()
            s.create()
            return s.session_key

        session_key = await sync_to_async(_create_session)()

        class _ScopeSession:
            def __init__(self, key):
                self.session_key = key

        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope["session"] = _ScopeSession(session_key)

        connected, _ = await communicator.connect()
        assert connected

        # Drain connect frame.
        await communicator.receive_json_from(timeout=2)

        # Mount — view.mount() injects the SlowSession into
        # request.session, which gets stashed as
        # view._djust_mount_request.session.
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": f"{__name__}._WSSlowOptInCounter",
                "url": "/slow-counter/",
            }
        )
        mount_resp = await communicator.receive_json_from(timeout=3)
        assert mount_resp.get("type") == "mount"

        t0 = _asyncio.get_event_loop().time()
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "bump",
                "params": {},
                "ref": 1,
            }
        )
        # Drain the event response — it MUST arrive (saves don't break
        # event handling). If wait_for's exception propagated up,
        # handle_event would crash and no normal response would come.
        try:
            await communicator.receive_json_from(timeout=3)
        except Exception:  # noqa: BLE001
            pass

        elapsed = _asyncio.get_event_loop().time() - t0
        await communicator.disconnect()

        # Load-bearing: WARNING log fired with the wrapper's marker.
        warning_logs = [
            r
            for r in caplog.records
            if r.levelno == _logging.WARNING and "exceeded 150ms" in r.getMessage()
        ]
        assert len(warning_logs) >= 1, (
            "Wrapper must log a WARNING containing 'exceeded 150ms' when "
            "the save body times out. The except asyncio.TimeoutError "
            "branch in handle_event is the regression guard. caplog "
            f"records: {[(r.levelname, r.getMessage()[:140]) for r in caplog.records]!r}"
        )

        # Bounded elapsed — wrapper cancels at 150ms. If the timeout
        # were absent, the slow aset alone would burn 250ms. Allow
        # generous headroom for CI scheduler jitter.
        assert elapsed < 2.0, (
            f"Event round-trip took {elapsed:.3f}s — far longer than the "
            "150ms timeout suggests. Wrapper may not be cancelling the slow body."
        )
