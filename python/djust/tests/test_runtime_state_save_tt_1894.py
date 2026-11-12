"""Direct-runtime tests for the time-travel record (ADR-022 Iter 2 Phase 2.2,
issue #1894), and for what a legacy view's event leaves in the session.

``record_event_start`` / ``record_event_end`` wrap the handler call, and the
freshly-finalized snapshot is pushed via the ``transport.on_event_recorded``
hook (WS sends the DEBUG-gated frame, SSE no-ops).

The per-event session save and the sticky-child save this file also covered
were removed in 2.0 with ``enable_state_snapshot``; what is left of that is one
guard: a legacy view's event writes no view state to the session.

These tests drive ``runtime.dispatch_event`` DIRECTLY against a MockTransport so
the behavior is independent of any transport. ``MockTransport`` is imported by
many other test modules.
"""

from __future__ import annotations

import contextlib
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

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        """No-op event context (#1899): the mock has no consumer lock to borrow —
        the runtime wraps the event handler+render in ``transport.event_context``,
        so the mock must provide one."""
        yield


# ------------------------------------------------------------------ #
# View fixtures
# ------------------------------------------------------------------ #


class _DefaultCounter(LiveView):
    """A legacy top-level view: its events must never persist state (#1552)."""

    exposure_policy = "legacy"
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

    exposure_policy = "legacy"
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
# A legacy event saves nothing (#1552)
# ------------------------------------------------------------------ #


@pytest.mark.django_db
class TestRuntimeLegacyEventSavesNothing:
    @pytest.mark.asyncio
    async def test_default_view_does_not_persist(self):
        """A legacy view's event persists nothing: the session key stays absent
        (#1552). 2.0 has no per-event save for a legacy view at all; this goes
        RED if one is reintroduced."""
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
        assert saved is None, "a legacy view MUST NOT persist per-event state (#1552)"


# ------------------------------------------------------------------ #
# Time-travel record + on_event_recorded hook
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
