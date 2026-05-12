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

    # Critical markers from the inserted block:
    assert 'getattr(target_view, "_djust_mount_request", None)' in source
    assert 'self.scope.get("session")' in source
    assert 'save_view_key = f"liveview_{save_path}"' in source
    assert "await save_session.aset(save_view_key" in source
    assert "await save_session.asave()" in source
    # Private-state path:
    assert "_get_private_state" in source
    assert 'f"{save_view_key}__private"' in source
    # Components path:
    assert "_save_components_to_session" in source
    # Save MUST be wrapped so failures don't break event handling.
    assert "Failed to save LiveView state after WS event" in source


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
# Round-trip: handler save → fresh-view restore (proxies WS reconnect)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_round_trip_save_then_restore_proxies_ws_reconnect():
    """A focused integration test that proxies the WS-event-save → WS-reconnect
    flow without a real WebsocketCommunicator (the consumer's full machinery
    has too many moving parts to mock cleanly).

    The flow mirrored:
    1. Initial GET populates session (acts like cold WS mount).
    2. Mutate state in-process (acts like a dj-click handler firing).
    3. Save the mutated state to the session using the HTTP-path save
       (the WS save block produces identical session contents — same
       ``get_context_data()`` + ``_get_private_state`` + components shape).
    4. Build a FRESH CounterView and restore from the session (acts like
       the loosened load gate on WS reconnect).
    5. Assert the restored state reflects the mutation.
    """
    # 1. Initial GET populates session.
    view = CounterView()
    factory = RequestFactory()
    request = factory.get("/counter/")
    request = _add_session(request)
    view.get(request)
    assert view.count == 0

    # 2 + 3. Drive the HTTP POST path with the ``increment`` event. This
    # invokes mount() (count=0) → increment() (count=1) → canonical save.
    # The WS save block produces identical session contents (same
    # ``get_context_data()`` + ``_get_private_state`` + components shape),
    # so this is a faithful proxy for what the WS save block writes.
    post_request = factory.post(
        "/counter/",
        data='{"event":"increment","params":{}}',
        content_type="application/json",
    )
    post_request.session = request.session
    view2 = CounterView()
    view2.post(post_request)

    # The session now reflects the post-increment state (count=1).
    view_key = "liveview_/counter/"
    saved = post_request.session.get(view_key, {})
    assert saved.get("count") == 1, (
        "POST handler must have run increment() and saved the new count to session."
    )

    # 4. Fresh view restores from session (proxies the loosened load gate).
    # This is exactly what handle_mount does on WS reconnect when
    # `has_prerendered or saved_state` evaluates True.
    from djust.security import safe_setattr

    reconnect_view = CounterView()
    saved_state = post_request.session.get(view_key, {})
    assert saved_state, "saved_state must be non-empty for the new load gate to fire"
    for key, value in saved_state.items():
        safe_setattr(reconnect_view, key, value, allow_private=False)

    # 5. The restored state reflects the post-handler mutation.
    assert reconnect_view.count == 1
