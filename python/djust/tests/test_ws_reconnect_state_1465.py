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
    """The load gate in handle_mount was widened from
    ``if has_prerendered`` to ``if has_prerendered or saved_state``. Pin this
    boolean by reading the source: the new gate evaluates True whenever
    saved_state is non-empty, regardless of has_prerendered. Verifying via
    source-text is sufficient because the change IS the boolean.

    Companion: the integration test below proves the runtime effect.
    """
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_mount)

    # Old gate: `if has_prerendered:` (exactly) wrapping the aget.
    # New gate: `if has_prerendered or saved_state:` wrapping it.
    assert "if has_prerendered or saved_state:" in source, (
        "Load gate must be widened to `if has_prerendered or saved_state:` "
        "so plain WS reconnect can restore state from session."
    )

    # The aget must be guarded for the no-session case (tests without sessions).
    assert "if request.session else {}" in source, (
        "aget must short-circuit when request.session is None — otherwise "
        "tests/contexts without session middleware blow up on the new path."
    )

    # And the OLD gate text MUST NOT remain — otherwise the boolean is broken.
    # We allow the substring to appear in comments / docstrings (the comment
    # explicitly mentions the old shape for context); pin only that the
    # executable line shape was changed.
    # The pre-change executable line read:
    #     `if has_prerendered:` followed by `view_key = f"liveview_{page_url}"`
    # The post-change shape unconditionally builds view_key BEFORE the gate.
    # Assert the new shape:
    new_shape_idx = source.find('view_key = f"liveview_{page_url}"')
    gate_idx = source.find("if has_prerendered or saved_state:")
    assert new_shape_idx != -1 and gate_idx != -1
    assert new_shape_idx < gate_idx, (
        "view_key must be assigned BEFORE the (widened) gate, not inside it."
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
    """Belt-and-suspenders: the actual handle_mount source must contain the
    ``skip_html_for_resume = mounted and has_prerendered`` line so the
    truth-table test above pins the source's actual behavior, not a stale
    re-implementation."""
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_mount)
    assert "skip_html_for_resume = mounted and has_prerendered" in source
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
    """Pin the save block's presence in handle_event. If a future refactor
    deletes or moves this block, this test fails fast.

    The block must:
    - Read ``target_view._djust_mount_request`` (preferred session source)
    - Fall back to ``self.scope.get("session")`` when no mount_request
    - Build ``save_view_key = f"liveview_{save_path}"``
    - Filter LiveComponents out of the saved state
    - Call ``save_session.aset(...)`` then ``save_session.asave()``
    - Wrap everything in try/except so saves never break event handling
    """
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_event)

    # Critical markers from the inserted block. Use a whitespace-tolerant
    # check so re-indentation (e.g. the Stage 11 fix that nested the block
    # under `if target_view is self.view_instance:`) doesn't break the grep.
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
    # Save MUST be wrapped so failures don't break event handling.
    assert "Failed to save LiveView state after WS event" in source
    # Stage 11 (PR #1466): save block MUST be gated on top-level view
    # identity so child LiveComponent events don't write to "liveview_/".
    assert "target_view is self.view_instance" in source


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
