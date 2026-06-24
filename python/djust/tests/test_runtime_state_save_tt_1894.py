"""Direct-runtime tests for time-travel record + session/sticky state-save
(ADR-022 Iter 2 Phase 2.2, issue #1894).

Phase 2.2 ports the three transport-agnostic per-event PERSISTENCE subsystems
the WS ``_handle_event_inner`` has into :class:`djust.runtime.ViewRuntime` so the
runtime's ``dispatch_event`` spine, when the Phase 2.3 final flip routes WS
events through it, persists identically:

1. **Time-travel record** — ``record_event_start`` / ``record_event_end`` wrap
   the handler call (single-view, component, and sticky-child branches), and
   the freshly-finalized snapshot is pushed via the new
   ``transport.on_event_recorded`` hook (WS sends the DEBUG-gated
   ``time_travel_event`` frame; SSE no-ops).
2. **Session state-save (#1466)** — gated on top-level view identity AND
   ``enable_state_snapshot`` (#1552: default views MUST NOT persist), bounded by
   a 150ms ``asyncio.wait_for`` (#1475).
3. **Sticky-child state-save (ADR-018)** — a separate gate
   (``sticky_child_should_persist``) for ``view_id``-routed child events.

These tests drive ``runtime.dispatch_event`` DIRECTLY against a MockTransport so
the runtime gained the behavior independently of any transport. The WS save
block (websocket.py) is UNTOUCHED — the #1466/#1552 grep-pins in
``test_ws_reconnect_state_1465.py`` still own the WS path; Phase 2.3 retires the
WS copy.

Each subsystem has a **reproduce-first / gate-off** pair (#1468): a positive
test that fails without the runtime handling, and a gate-off test proving the
load-bearing guard (the ``enable_state_snapshot`` gate, the time-travel record,
the ``on_event_recorded`` hook) is real.
"""

from __future__ import annotations

import inspect
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
        SECRET_KEY="test-secret-key-runtime-state-save-tt",
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
# Test transport — records send() calls + on_event_recorded invocations
# ------------------------------------------------------------------ #


class MockTransport:
    """Minimal Transport implementation that records outbound frames AND every
    ``on_event_recorded`` call (so the time-travel hook is observable)."""

    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.closed_with: Optional[int] = None
        # Records (view, snapshot) tuples for every on_event_recorded call.
        self.recorded: List[tuple] = []

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
        # Mirrors the SSE no-op shape (records the call so tests can assert the
        # hook fired) but does NOT emit a frame.
        self.recorded.append((view, snapshot))


# ------------------------------------------------------------------ #
# View fixtures
# ------------------------------------------------------------------ #


class _OptInCounter(LiveView):
    """Top-level view that opts into per-event state persistence."""

    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">count={{ count }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0
        self._secret = "private-seed"  # private attr → __private save key

    @event_handler()
    def increment(self, amount: int = 1, **kwargs):
        self.count += amount

    def get_context_data(self, **kwargs):
        return {"count": self.count, "view": self}


class _DefaultCounter(LiveView):
    """Top-level view that does NOT opt in (#1552: must never persist)."""

    template = '<div dj-root dj-id="0">count={{ count }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, amount: int = 1, **kwargs):
        self.count += amount

    def get_context_data(self, **kwargs):
        return {"count": self.count, "view": self}


class _TimeTravelCounter(LiveView):
    """Top-level view with time-travel enabled (gets a _time_travel_buffer)."""

    time_travel_enabled = True
    template = '<div dj-root dj-id="0">count={{ count }}</div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, amount: int = 1, **kwargs):
        self.count += amount

    def get_context_data(self, **kwargs):
        return {"count": self.count, "view": self}


# ------------------------------------------------------------------ #
# Helpers — a real DB session captured request stand-in
# ------------------------------------------------------------------ #


def _make_runtime(view: LiveView, *, scope: Optional[dict] = None):
    transport = MockTransport()
    runtime = ViewRuntime(transport, scope=scope)
    runtime.view_instance = view
    return runtime, transport


class _StashedRequest:
    """Minimal stand-in for the stashed ``_djust_mount_request`` carrying a real
    Django SessionStore + a path (mirrors what WS handle_mount stashes)."""

    def __init__(self, session, path: str = "/counter/"):
        self.session = session
        self.path = path


def _make_db_session():
    from django.contrib.sessions.backends.db import SessionStore

    s = SessionStore()
    s.create()
    return s


# ------------------------------------------------------------------ #
# Subsystem 2 — session state-save (#1466) + gate (#1552)
# ------------------------------------------------------------------ #


@pytest.mark.django_db
class TestRuntimeSessionStateSave:
    @pytest.mark.asyncio
    async def test_opt_in_event_writes_state_to_session(self):
        """An ``enable_state_snapshot=True`` view's event, dispatched through the
        runtime, writes the post-event state to the Django session under
        ``liveview_{path}``.

        Reproduce-first / non-tautological (#1200): a revert of the save call, a
        missing ``asave()``, or a broken gate would leave the session key absent.
        """
        from asgiref.sync import sync_to_async

        session = await sync_to_async(_make_db_session)()
        view = _OptInCounter()
        view.mount(None)
        view._djust_mount_request = _StashedRequest(session, path="/counter/")

        runtime, _transport = _make_runtime(view)

        await runtime.dispatch_event(
            {"type": "event", "event": "increment", "params": {"amount": 5}, "ref": 1}
        )

        assert view.count == 5
        saved = await session.aget("liveview_/counter/")
        assert saved is not None, "opt-in view's state must be persisted to the session"
        assert saved.get("count") == 5

    @pytest.mark.asyncio
    async def test_default_view_does_not_persist(self):
        """GATE-OFF target (#1468/#1552): a DEFAULT view (no opt-in) does NOT
        persist. The session key stays absent.

        This is the test that goes RED if the ``enable_state_snapshot`` gate is
        removed from the runtime save block (the #1552 regression).
        """
        from asgiref.sync import sync_to_async

        session = await sync_to_async(_make_db_session)()
        view = _DefaultCounter()
        view.mount(None)
        view._djust_mount_request = _StashedRequest(session, path="/counter/")

        runtime, _transport = _make_runtime(view)

        await runtime.dispatch_event(
            {"type": "event", "event": "increment", "params": {"amount": 5}, "ref": 1}
        )

        assert view.count == 5  # handler still ran
        saved = await session.aget("liveview_/counter/")
        assert saved is None, (
            "default (non-opt-in) view MUST NOT persist per-event state (#1552); "
            "the enable_state_snapshot gate is load-bearing."
        )

    @pytest.mark.asyncio
    async def test_private_attrs_saved_under_private_key(self):
        """Opt-in save also persists private attrs under ``{key}__private``
        (mirrors HTTP/WS save order)."""
        from asgiref.sync import sync_to_async

        session = await sync_to_async(_make_db_session)()
        view = _OptInCounter()
        view.mount(None)
        # Mirror the post-mount snapshot the WS/HTTP mount paths run so the
        # ``_secret`` private attr is tracked in ``_user_private_keys`` and thus
        # captured by ``_get_private_state``.
        view._snapshot_user_private_attrs()
        view._djust_mount_request = _StashedRequest(session, path="/counter/")

        runtime, _transport = _make_runtime(view)
        await runtime.dispatch_event(
            {"type": "event", "event": "increment", "params": {}, "ref": 1}
        )

        priv = await session.aget("liveview_/counter/__private")
        assert priv is not None
        assert priv.get("_secret") == "private-seed"


# ------------------------------------------------------------------ #
# Subsystem 1 — time-travel record + on_event_recorded hook
# ------------------------------------------------------------------ #


@pytest.mark.django_db
class TestRuntimeTimeTravelRecord:
    @pytest.mark.asyncio
    async def test_record_fires_on_runtime_event(self):
        """A time-travel-enabled view's event appends ONE snapshot to its buffer
        with the correct state_before/state_after.

        Reproduce-first (#1200): snapshot count grows by exactly 1 — asserting
        the count delta, not just non-emptiness, so a no-op record can't pass.
        """
        view = _TimeTravelCounter()
        view.mount(None)
        buffer = view._time_travel_buffer
        assert buffer is not None
        before_len = len(buffer)

        runtime, _transport = _make_runtime(view)
        await runtime.dispatch_event(
            {"type": "event", "event": "increment", "params": {"amount": 2}, "ref": 9}
        )

        assert len(buffer) == before_len + 1, "exactly one snapshot recorded"
        snap = buffer.jump(len(buffer) - 1)  # last entry (TimeTravelBuffer API)
        assert snap is not None
        assert snap.event_name == "increment"
        assert snap.state_before.get("count") == 0
        assert snap.state_after.get("count") == 2

    @pytest.mark.asyncio
    async def test_gate_off_record_neutered_means_no_snapshot(self):
        """GATE-OFF (#1468): a view with time-travel DISABLED records nothing —
        proving ``record_event_start``'s opt-in gate is what drives the record,
        not an unconditional append. If a future edit dropped the gate (recorded
        unconditionally) a non-opt-in view would gain a buffer / snapshots and
        this would go red.
        """
        view = _DefaultCounter()  # time_travel_enabled is False
        view.mount(None)
        # No buffer is created for a non-opt-in view.
        assert getattr(view, "_time_travel_buffer", None) is None

        runtime, _transport = _make_runtime(view)
        await runtime.dispatch_event(
            {"type": "event", "event": "increment", "params": {}, "ref": 1}
        )

        # Still no buffer — the record path is a true no-op for non-opt-in views.
        assert getattr(view, "_time_travel_buffer", None) is None

    @pytest.mark.asyncio
    async def test_on_event_recorded_hook_called_with_snapshot(self):
        """The runtime calls ``transport.on_event_recorded(view, snapshot)`` after
        the record finalizes (SSE/Mock no-op; WS would send a frame)."""
        view = _TimeTravelCounter()
        view.mount(None)
        runtime, transport = _make_runtime(view)

        await runtime.dispatch_event(
            {"type": "event", "event": "increment", "params": {}, "ref": 1}
        )

        assert len(transport.recorded) == 1, "on_event_recorded must fire once per event"
        rec_view, rec_snap = transport.recorded[0]
        assert rec_view is view
        assert rec_snap is not None
        assert rec_snap.event_name == "increment"

    @pytest.mark.asyncio
    async def test_hook_not_called_when_record_disabled(self):
        """When time-travel is OFF the snapshot is None, so the runtime skips the
        ``on_event_recorded`` hook (no None-snapshot pushes)."""
        view = _DefaultCounter()
        view.mount(None)
        runtime, transport = _make_runtime(view)

        await runtime.dispatch_event(
            {"type": "event", "event": "increment", "params": {}, "ref": 1}
        )

        assert transport.recorded == [], "hook must be skipped for a None snapshot"


# ------------------------------------------------------------------ #
# Runtime-side #1466 source-grep pin (mirrors the WS pin)
# ------------------------------------------------------------------ #


def test_runtime_save_block_present_and_gated():
    """Runtime-side mirror of the WS #1466/#1552 pin
    (test_ws_reconnect_state_1465.py:298-331). Pins the ported save block in
    ``ViewRuntime`` so the gate / key-shape / 150ms-bound cannot silently drop.

    The WS pin asserts these exact strings in
    ``LiveViewConsumer._handle_event_inner``; this asserts them in the runtime's
    save path (``_dispatch_event_inner`` gate + ``_persist_state_after_event``
    body). Drift between the two save gates goes red on whichever pin's source
    lost the string.
    """
    import djust.runtime as rt_mod

    gate_src = inspect.getsource(rt_mod.ViewRuntime._dispatch_event_inner)
    body_src = inspect.getsource(rt_mod.ViewRuntime._persist_state_after_event)
    gate_collapsed = " ".join(gate_src.split())
    body_collapsed = " ".join(body_src.split())

    # --- Gate (in _dispatch_event_inner): identity AND enable_state_snapshot ---
    # Mirrors websocket.py:3700-3702 / WS pin line 313 + 320.
    assert "target_view is self.view_instance" in gate_collapsed, (
        "Runtime save gate must keep the top-level-identity clause (#1466) — "
        "the same string the WS pin asserts, so drift between the two is caught."
    )
    assert '"enable_state_snapshot"' in gate_collapsed, (
        "Runtime save gate must read enable_state_snapshot (#1552) — default "
        "views MUST NOT persist per-event state."
    )

    # --- Body (in _persist_state_after_event): key shape + writes ---
    # Mirrors WS pin lines 301-303.
    assert 'save_view_key = f"liveview_{save_path}"' in body_collapsed
    assert "await save_session.aset(save_view_key" in body_collapsed
    assert "await save_session.asave()" in body_collapsed
    # Private + components paths (WS pin lines 305-308).
    assert "_get_private_state" in body_collapsed
    assert 'f"{save_view_key}__private"' in body_collapsed
    assert "_save_components_to_session" in body_collapsed
    # Failure handling + 150ms bound (WS pin lines 310 + 329-331).
    assert "Failed to save LiveView state after runtime event" in body_collapsed
    assert "asyncio.wait_for" in body_collapsed
    assert "timeout=0.150" in body_collapsed
    assert "asyncio.TimeoutError" in body_collapsed


def test_runtime_save_reads_scope_or_mount_request():
    """The runtime save discovers the session the same way WS does: prefer the
    stashed ``_djust_mount_request`` session, fall back to the ASGI scope
    session (websocket.py:3710-3720)."""
    import djust.runtime as rt_mod

    body = " ".join(inspect.getsource(rt_mod.ViewRuntime._persist_state_after_event).split())
    assert "_djust_mount_request" in body
    assert 'self.scope.get("session")' in body


# ------------------------------------------------------------------ #
# Subsystem 3 — sticky-child state-save (ADR-018 Branch B)
# ------------------------------------------------------------------ #


class _StickyOptInChild(LiveView):
    """A sticky child that opts into per-event persistence (Decision 5)."""

    sticky = True
    sticky_id = "child"
    enable_state_snapshot = True
    template = "<div>child={{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, amount: int = 1, **kwargs):
        self.count += amount

    def get_context_data(self, **kwargs):
        return {"count": self.count, "view": self}


class _StickyOptInParent(LiveView):
    """A parent that opts in (Decision 5 requires BOTH child + parent)."""

    enable_state_snapshot = True
    template = '<div dj-root dj-id="0"><h1>parent={{ parent_count }}</h1></div>'

    def mount(self, request, **kwargs):
        self.parent_count = 0

    def get_context_data(self, **kwargs):
        return {"parent_count": self.parent_count, "view": self}


@pytest.mark.django_db
class TestRuntimeStickyChildSave:
    @pytest.mark.asyncio
    async def test_view_id_child_event_persists_child_state(self):
        """A ``view_id``-routed sticky-child event persists the CHILD under its
        stable sticky key (both opted in). Reproduce-first: a revert of the
        sticky save would leave the sticky index/child key absent."""
        from asgiref.sync import sync_to_async

        session = await sync_to_async(_make_db_session)()
        parent = _StickyOptInParent()
        parent.mount(None)
        parent._djust_mount_request = _StashedRequest(session, path="/page/")
        child = _StickyOptInChild()
        child.mount(None)
        parent._register_child("child-1", child)

        runtime, transport = _make_runtime(parent)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"view_id": "child-1", "amount": 4},
                "ref": 3,
            }
        )

        assert child.count == 4
        # The sticky index ledger must list the persisted child (the
        # write_sticky_index_and_prune call ran). The exact index key shape is
        # owned by mixins/sticky.py; assert SOME sticky-related key landed.
        await session.aload() if hasattr(session, "aload") else None
        keys = await sync_to_async(lambda: list(session.keys()))()
        sticky_keys = [k for k in keys if "sticky" in k or "/page/" in k]
        assert sticky_keys, (
            f"sticky-child save must write at least one sticky/child key; got keys={keys}"
        )

    @pytest.mark.asyncio
    async def test_gate_off_parent_not_opted_in_skips_save(self):
        """GATE-OFF (#1468): when the PARENT does not opt in, the both-opt-in
        predicate (``sticky_child_should_persist``) is False, so NO sticky save
        runs (Decision 5). The child's state mutates but nothing persists."""
        from asgiref.sync import sync_to_async

        session = await sync_to_async(_make_db_session)()

        class _NonOptInParent(LiveView):
            template = '<div dj-root dj-id="0">p={{ parent_count }}</div>'

            def mount(self, request, **kwargs):
                self.parent_count = 0

            def get_context_data(self, **kwargs):
                return {"parent_count": self.parent_count, "view": self}

        parent = _NonOptInParent()
        parent.mount(None)
        parent._djust_mount_request = _StashedRequest(session, path="/page/")
        child = _StickyOptInChild()  # child opts in, parent does not → mismatch
        child.mount(None)
        parent._register_child("child-1", child)

        runtime, _transport = _make_runtime(parent)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"view_id": "child-1", "amount": 4},
            }
        )

        assert child.count == 4  # handler still ran
        keys = await sync_to_async(lambda: list(session.keys()))()
        # No sticky/child key written — the both-opt-in gate skipped the save.
        sticky_keys = [k for k in keys if "sticky" in k]
        assert not sticky_keys, (
            f"sticky save must be skipped when parent is not opted in; got {keys}"
        )


def test_runtime_sticky_save_present_and_gated():
    """Pin the sticky-child save (ADR-018 Branch B) in the runtime's sticky-child
    dispatch + its helper. Mirrors the WS sticky markers (websocket.py:3821-3888):
    the both-opt-in predicate, the helper calls, the 150ms bound, and the
    opt-in-mismatch warning."""
    import djust.runtime as rt_mod

    dispatch_src = " ".join(
        inspect.getsource(rt_mod.ViewRuntime._dispatch_sticky_child_event).split()
    )
    helper_src = " ".join(
        inspect.getsource(rt_mod.ViewRuntime._persist_sticky_child_after_event).split()
    )

    # Gate + warning in the dispatch method.
    assert "sticky_child_should_persist" in dispatch_src
    assert "warn_sticky_child_optin_skip" in dispatch_src
    # Helper body: the two ADR-018 sticky helpers + asave + 150ms bound.
    assert "save_sticky_child_state" in helper_src
    assert "write_sticky_index_and_prune" in helper_src
    assert "await save_session.asave()" in helper_src
    assert "asyncio.wait_for" in helper_src
    assert "timeout=0.150" in helper_src
