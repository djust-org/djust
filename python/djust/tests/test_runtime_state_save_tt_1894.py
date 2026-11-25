"""The ``MockTransport`` many runtime tests import (ADR-022 Iter 2 Phase 2.2, #1894).

The time-travel record this file once covered was removed in 2.0 with
time-travel debugging (#4184). Its last test, "a legacy view's event writes no
view state to the session" (#1552), was deleted with the legacy exposure policy
(#4194): an explicit view saves only its declared ``persist="server"`` fields
(``test_exposure_runtime.py``). What stays is the transport double, which
records the frames a ``ViewRuntime`` sends.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import django
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

# ------------------------------------------------------------------ #
# Test transport — records send() calls
# ------------------------------------------------------------------ #


class MockTransport:
    """Minimal Transport implementation that records outbound frames."""

    def __init__(self, session_id: str | None = None):
        self._session_id = session_id or str(uuid.uuid4())
        self._client_ip: str | None = None
        self.sent: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.closed_with: int | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> str | None:
        return self._client_ip

    async def send(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    def next_client_version(self, html: str | None, rust_version: int) -> int:
        return rust_version

    def build_request(self) -> Any | None:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        """No-op event context (#1899): the mock has no consumer lock to borrow —
        the runtime wraps the event handler+render in ``transport.event_context``,
        so the mock must provide one."""
        yield
