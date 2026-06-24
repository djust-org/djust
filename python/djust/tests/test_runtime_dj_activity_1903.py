"""dj_activity gating + lock-free deferred re-dispatcher in ViewRuntime
(ADR-022 Iter 2 Phase 2.3a, issue #1903).

Phase 2.3a ports the transport-agnostic ``{% dj_activity %}`` deferral the WS
``_handle_event_inner`` has (websocket.py:3254-3273 gate + 4290-4294 flush →
``_dispatch_single_event`` websocket.py:1456) into the runtime's
``dispatch_event`` spine:

* **Gate** (``_dispatch_event_render``): an event targeting a HIDDEN (non-eager)
  activity region → queued via ``view._queue_deferred_activity_event`` + a no-op
  frame (no render).
* **Flush + lock-free re-dispatcher**: after a render that may flip visibility,
  drain ``view._deferred_activity_events`` via
  ``runtime._flush_deferred_activity_events`` → the ActivityMixin flush →
  ``runtime._dispatch_single_event`` (the lock-free single-event re-dispatch).
  The re-dispatcher runs INSIDE ``event_context`` (which on WS holds the borrowed
  consumer ``_render_lock``) and MUST NOT re-acquire any lock / re-enter
  ``event_context`` — non-reentrant ``asyncio.Lock`` would deadlock.

Since Iter 1 (#1887) SSE events route through ``dispatch_event``, so this goes
LIVE for SSE — SSE events now respect dj_activity deferral (a parity
improvement). WS events are UNAFFECTED (they stay on the bespoke
``_handle_event_inner`` gate until Phase 2.3b).

Two layers, both with reproduce-first + gate-off (#1468):

1. **Direct-runtime** tests (``runtime.dispatch_event`` against a MockTransport
   with a real-rendering activity LiveView) — the runtime gained the behavior
   independently of any transport.
2. **Real-SSE end-to-end** tests (GET-stream + /event/ POST through the real
   Django SSE views) — proving the behavior is LIVE on the SSE transport.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import sys
import uuid
from typing import Any, Dict, List, Optional

import django
import pytest
from django.conf import settings

# Configure Django before importing anything that needs settings.
if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-runtime-dj-activity-1903",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"builtins": ["djust.templatetags.live_tags"]},
            }
        ],
    )
    django.setup()

from djust import LiveView  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402


# ------------------------------------------------------------------ #
# Test transport — records every outbound frame.
# ------------------------------------------------------------------ #


class MockTransport:
    """Minimal Transport that records outbound frames and exposes the event
    context the runtime borrows. ``event_context`` is a no-op (no consumer lock
    to borrow), matching SSE — which is the live transport this PR affects."""

    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.closed_with: Optional[int] = None
        # True while inside event_context — used to PROVE the re-dispatcher runs
        # inside the (single) borrowed context, never re-entering it.
        self._in_event_context = False
        self.context_reentry_attempts = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return self._client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self) -> Optional[Any]:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    async def on_event_recorded(self, view: Any, snapshot: Any) -> None:
        return None

    def uses_actors(self, view: Any) -> bool:
        return False

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        if self._in_event_context:
            # A re-entry would mean the re-dispatcher re-wrapped the turn — the
            # exact deadlock-class bug this PR avoids. Record it instead of
            # yielding so a regressing impl is caught (a real asyncio.Lock would
            # deadlock; the mock makes the violation observable + non-hanging).
            self.context_reentry_attempts += 1
            yield
            return
        self._in_event_context = True
        try:
            yield
        finally:
            self._in_event_context = False


def _make_runtime(view: LiveView, *, scope: Optional[dict] = None):
    transport = MockTransport()
    runtime = ViewRuntime(transport, scope=scope)
    runtime.view_instance = view
    return runtime, transport


# ------------------------------------------------------------------ #
# Real-rendering activity LiveViews.
# ------------------------------------------------------------------ #


# The inline Rust template engine doesn't support the ``{% dj_activity %}`` tag,
# so — per the #1896 parity-net pattern (test_ws_event_flip_parity_1896.py:196) —
# the region is registered directly in mount via ``set_activity_visible`` (the
# gate reads the SAME ``_djust_activities`` map the tag would populate). The
# ``count``/``opens`` assigns change on every handler so the render path (not the
# auto-skip path) runs — the auto-skip early-return bypasses the flush.


class _ActivityView(LiveView):
    """Counter with a ``panel`` activity registered HIDDEN at mount."""

    template = '<div dj-root dj-id="0">count={{ count }} opens={{ opens }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0
        self.opens = 0
        self.set_activity_visible("panel", False)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    @event_handler()
    def show_panel(self, **kwargs):
        # Flip the panel visible AND change a public assign so the render path
        # runs (the auto-skip early-return bypasses the deferred-activity flush);
        # the post-render flush then drains the queued bump events.
        self.set_activity_visible("panel", True)
        self.opens += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count, "opens": self.opens}


class _NoActivityView(LiveView):
    """A plain counter with NO activity region — the gate must be a total no-op
    (zero-cost-when-unused)."""

    template = '<div dj-root dj-id="0">count={{ count }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count}


# ------------------------------------------------------------------ #
# Direct-runtime tests.
# ------------------------------------------------------------------ #


class TestRuntimeActivityGate:
    @pytest.mark.asyncio
    async def test_hidden_activity_event_is_queued_and_noop(self):
        """An event targeting a HIDDEN activity → queued + no-op (NO render)."""
        view = _ActivityView()
        view.mount(None)
        runtime, transport = _make_runtime(view)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"_activity": "panel"},
                "ref": 1,
            }
        )

        # Handler did NOT run (event was deferred, not dispatched).
        assert view.count == 0, "hidden-activity event must NOT run its handler immediately"
        # Queued for replay.
        assert "panel" in view._deferred_activity_events
        assert len(view._deferred_activity_events["panel"]) == 1
        # Exactly one frame: a no-op (no patch / html_update).
        types = [m.get("type") for m in transport.sent]
        assert types == ["noop"], f"expected a single noop, got {transport.sent!r}"
        assert transport.sent[0].get("ref") == 1
        assert transport.sent[0].get("event_name") == "bump"

    @pytest.mark.asyncio
    async def test_gate_off_hidden_activity_event_renders_immediately(self):
        """GATE-OFF (#1468): with the gate disabled, the hidden-activity event
        WRONGLY runs + renders immediately — proving the gate is load-bearing."""
        view = _ActivityView()
        view.mount(None)
        runtime, transport = _make_runtime(view)

        # Neuter the gate by making the view report the panel as always-visible
        # (the gate's hidden-check then never fires).
        view.is_activity_visible = lambda name: True  # type: ignore[assignment]

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"_activity": "panel"},
                "ref": 1,
            }
        )

        # With the gate off the handler runs + a render frame is emitted.
        assert view.count == 1, (
            "gate-off must let the hidden-activity event run (else gate test is a tautology)"
        )
        types = [m.get("type") for m in transport.sent]
        assert any(t in ("patch", "html_update") for t in types), (
            f"gate-off must produce a render frame, got {transport.sent!r}"
        )
        assert "panel" not in view._deferred_activity_events

    @pytest.mark.asyncio
    async def test_flip_visible_drains_queue_same_round_trip(self):
        """Queue while hidden, then a show_panel event flips visible → the queued
        event drains in the SAME round-trip (2nd render frame)."""
        view = _ActivityView()
        view.mount(None)
        runtime, transport = _make_runtime(view)

        # 1) Hidden bump → queued + noop.
        await runtime.dispatch_event(
            {"type": "event", "event": "bump", "params": {"_activity": "panel"}, "ref": 1}
        )
        assert view.count == 0
        assert len(view._deferred_activity_events["panel"]) == 1
        transport.sent.clear()

        # 2) show_panel flips visible → its own render frame, THEN the drained
        #    bump runs (count→1) + its render frame, all in this round-trip.
        await runtime.dispatch_event(
            {"type": "event", "event": "show_panel", "params": {}, "ref": 2}
        )

        assert view.count == 1, "the queued bump must have drained when the panel flipped visible"
        assert "panel" not in view._deferred_activity_events, "queue popped clean after drain"
        render_frames = [m for m in transport.sent if m.get("type") in ("patch", "html_update")]
        assert len(render_frames) >= 2, (
            f"expected the show_panel render AND the drained bump render, got {transport.sent!r}"
        )

    @pytest.mark.asyncio
    async def test_view_without_activity_region_renders_normally(self):
        """A view with NO {% dj_activity %} region → the gate is a no-op; the
        event renders normally even with a stray _activity param."""
        view = _NoActivityView()
        view.mount(None)
        runtime, transport = _make_runtime(view)

        # Even a stray _activity marker is harmless: is_activity_visible returns
        # True for an unknown name, so the gate falls through to a normal render.
        await runtime.dispatch_event(
            {"type": "event", "event": "bump", "params": {"_activity": "ghost"}, "ref": 1}
        )

        assert view.count == 1, "no-activity view must run + render normally"
        types = [m.get("type") for m in transport.sent]
        assert any(t in ("patch", "html_update") for t in types), (
            f"no-activity view must produce a render frame, got {transport.sent!r}"
        )

    @pytest.mark.asyncio
    async def test_redispatcher_runs_inside_borrowed_context_no_reentry(self):
        """The lock-free re-dispatcher MUST run inside the borrowed event_context
        and NEVER re-enter it (a real consumer's non-reentrant _render_lock would
        deadlock on re-entry). The MockTransport records any re-entry attempt."""
        view = _ActivityView()
        view.mount(None)
        runtime, transport = _make_runtime(view)

        await runtime.dispatch_event(
            {"type": "event", "event": "bump", "params": {"_activity": "panel"}, "ref": 1}
        )
        transport.sent.clear()
        await runtime.dispatch_event(
            {"type": "event", "event": "show_panel", "params": {}, "ref": 2}
        )

        assert view.count == 1, (
            "the drain must have run (else there's nothing to prove about re-entry)"
        )
        assert transport.context_reentry_attempts == 0, (
            "the re-dispatcher must NOT re-enter event_context — it already runs inside "
            "the borrowed context (a real _render_lock would deadlock on re-entry)"
        )

    @pytest.mark.asyncio
    async def test_redispatcher_revalidates_denied_queued_event(self):
        """A denied queued event never dispatches — the re-dispatcher re-runs the
        full auth pipeline, so an unknown/denied handler in the queue is dropped
        (matches the WS flush per-event behavior)."""
        view = _ActivityView()
        view.mount(None)
        # Queue a non-existent handler directly (simulating a client-supplied
        # event that passed the queue but has no handler).
        view._queue_deferred_activity_event("panel", "no_such_handler", {})
        view.set_activity_visible("panel", True)
        runtime, transport = _make_runtime(view)

        # Drain directly (the path the post-render flush takes).
        await runtime._flush_deferred_activity_events()

        # The denied event produced an error frame (validation), NOT a render —
        # and the queue is popped clean.
        assert "panel" not in view._deferred_activity_events
        render_frames = [m for m in transport.sent if m.get("type") in ("patch", "html_update")]
        assert not render_frames, "a denied queued event must NOT render"


# ------------------------------------------------------------------ #
# Source-grep structural pins (drift detectors, #1646/#1859).
# ------------------------------------------------------------------ #


class TestRuntimeActivityStructuralPins:
    def test_gate_lives_in_dispatch_event_render(self):
        """The gate is in ``_dispatch_event_render`` and reuses the transport-
        agnostic ActivityMixin view methods (NOT a re-implementation)."""
        src = inspect.getsource(ViewRuntime._dispatch_event_render)
        assert 'params.pop("_activity"' in src
        assert "is_activity_visible" in src
        assert "_is_activity_eager" in src
        assert "_queue_deferred_activity_event" in src

    def test_redispatcher_is_lock_free(self):
        """The re-dispatcher must NOT acquire a lock or re-enter event_context —
        it runs inside the borrowed context (websocket.py:1467 contract).

        Checks the EXECUTABLE body only (docstring + comments stripped), since the
        contract is explained in prose there."""
        import ast
        import textwrap

        src = inspect.getsource(ViewRuntime._dispatch_single_event)
        tree = ast.parse(textwrap.dedent(src))
        func = tree.body[0]
        # Drop the leading docstring expression so prose mentions don't false-match.
        body = func.body[1:] if (func.body and isinstance(func.body[0], ast.Expr)) else func.body
        code = "\n".join(ast.unparse(node) for node in body)
        assert "_render_lock" not in code, "re-dispatcher body must NOT touch a render lock"
        assert ".acquire(" not in code, "re-dispatcher body must NOT acquire a lock"
        assert "event_context" not in code, "re-dispatcher body must NOT re-enter event_context"

    def test_flush_passes_runtime_as_dispatcher(self):
        """Option (a): the runtime passes ITSELF to the ActivityMixin flush as the
        consumer-like ``_dispatch_single_event`` provider (no mixin change)."""
        src = inspect.getsource(ViewRuntime._flush_deferred_activity_events)
        assert "_flush_deferred_activity_events(self)" in src


# ------------------------------------------------------------------ #
# Real-SSE end-to-end tests — proving the behavior is LIVE on SSE.
# ------------------------------------------------------------------ #

ALLOWED_ORIGIN = "https://example.com"


class _ActivitySSEView(LiveView):
    """SSE-mounted activity view: a ``panel`` registered HIDDEN at mount (the
    inline Rust engine doesn't support the tag — same #1896 parity-net pattern as
    the direct-runtime views above)."""

    template = '<div dj-root dj-id="0">count={{ count }} opens={{ opens }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0
        self.opens = 0
        self.set_activity_visible("panel", False)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    @event_handler()
    def show_panel(self, **kwargs):
        self.set_activity_visible("panel", True)
        self.opens += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count, "opens": self.opens}


class _NoActivitySSEView(LiveView):
    template = '<div dj-root dj-id="0">count={{ count }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count}


def _register(view_cls):
    setattr(sys.modules[__name__], view_cls.__name__, view_cls)
    return f"{__name__}.{view_cls.__name__}"


def _allowlist():
    from django.test import override_settings

    return override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__.split(".")[0]])


def _drain(session):
    msgs = []
    while not session.queue.empty():
        msg = session.queue.get_nowait()
        if msg is None:
            continue
        msgs.append(msg)
    return msgs


async def _mount_stream(view_path, *, query=""):
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from djust.sse import DjustSSEStreamView, _sse_sessions

    sid = "33333333-3333-3333-3333-333333333333"
    _sse_sessions.pop(sid, None)
    rf = RequestFactory()
    q = f"?view={view_path}" + (f"&{query}" if query else "")
    request = rf.get(f"/djust/sse/{sid}/{q}", HTTP_ORIGIN=ALLOWED_ORIGIN)
    request.user = AnonymousUser()
    view = DjustSSEStreamView()
    response = await view.get(request, session_id=sid)
    return response, _sse_sessions.get(sid), sid


async def _post_event(session, sid, event, params=None):
    from unittest.mock import MagicMock

    from django.test import RequestFactory

    from djust.sse import DjustSSEEventView

    session._owner_user_pk = 7
    session._owner_session_key = None
    rf = RequestFactory()
    request = rf.post(
        f"/djust/sse/{sid}/event/",
        data=json.dumps({"event": event, "params": params or {}}),
        content_type="application/json",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
    )
    request.user = MagicMock(is_authenticated=True, pk=7)
    view = DjustSSEEventView()
    return await view.post(request, session_id=sid)


@pytest.mark.django_db
class TestSSEActivityViaRuntime:
    @pytest.mark.asyncio
    async def test_hidden_activity_event_deferred_over_sse(self):
        """Over the REAL SSE transport: an event on a HIDDEN activity → queued +
        noop (no render). This is the LIVE parity improvement for SSE."""
        from django.test import override_settings

        with override_settings(ALLOWED_HOSTS=["example.com"]):
            path = _register(_ActivitySSEView)
            with _allowlist():
                _resp, session, sid = await _mount_stream(path)
            assert session is not None
            _drain(session)  # clear mount frame

            resp = await _post_event(session, sid, "bump", {"_activity": "panel"})
            assert resp.status_code == 200

            view = session.runtime.view_instance
            assert view.count == 0, "SSE hidden-activity event must be deferred, not run"
            assert "panel" in view._deferred_activity_events
            msgs = _drain(session)
            render_frames = [m for m in msgs if m.get("type") in ("patch", "html_update")]
            assert not render_frames, f"deferred SSE event must NOT render, got {msgs!r}"
            assert any(m.get("type") == "noop" for m in msgs), f"expected a noop, got {msgs!r}"

    @pytest.mark.asyncio
    async def test_flip_visible_drains_over_sse_same_round_trip(self):
        """Over SSE: queue while hidden, then flip visible → the queued event
        drains in the same /event/ round-trip."""
        from django.test import override_settings

        with override_settings(ALLOWED_HOSTS=["example.com"]):
            path = _register(_ActivitySSEView)
            with _allowlist():
                _resp, session, sid = await _mount_stream(path)
            _drain(session)

            await _post_event(session, sid, "bump", {"_activity": "panel"})
            view = session.runtime.view_instance
            assert view.count == 0
            assert len(view._deferred_activity_events["panel"]) == 1
            _drain(session)

            resp = await _post_event(session, sid, "show_panel", {})
            assert resp.status_code == 200
            assert view.count == 1, "the queued bump must drain when the SSE panel flips visible"
            assert "panel" not in view._deferred_activity_events
            msgs = _drain(session)
            render_frames = [m for m in msgs if m.get("type") in ("patch", "html_update")]
            assert len(render_frames) >= 2, (
                f"expected show_panel + drained-bump render frames, got {msgs!r}"
            )

    @pytest.mark.asyncio
    async def test_no_activity_sse_view_unaffected(self):
        """An SSE view with NO {% dj_activity %} region renders normally — the
        gate adds zero behavior when unused."""
        from django.test import override_settings

        with override_settings(ALLOWED_HOSTS=["example.com"]):
            path = _register(_NoActivitySSEView)
            with _allowlist():
                _resp, session, sid = await _mount_stream(path)
            _drain(session)

            resp = await _post_event(session, sid, "bump", {})
            assert resp.status_code == 200
            view = session.runtime.view_instance
            assert view.count == 1
            msgs = _drain(session)
            assert any(m.get("type") in ("patch", "html_update") for m in msgs), (
                f"no-activity SSE view must render normally, got {msgs!r}"
            )
