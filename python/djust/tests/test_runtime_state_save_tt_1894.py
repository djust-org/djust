"""Direct-runtime test for what a legacy view's event leaves in the session
(ADR-022 Iter 2 Phase 2.2, issue #1894).

The time-travel record this file also covered was removed in 2.0 with
time-travel debugging (#4184). The per-event session save and the sticky-child
save it covered were removed in 2.0 with ``enable_state_snapshot``; what is left
is one guard: a legacy view's event writes no view state to the session.

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
# Test transport — records send() calls
# ------------------------------------------------------------------ #


class MockTransport:
    """Minimal Transport implementation that records outbound frames."""

    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.closed_with: Optional[int] = None

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
