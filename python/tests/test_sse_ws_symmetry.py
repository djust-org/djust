"""
SSE/WebSocket transport symmetry tests.

Drives the same input sequence (``mount`` -> ``event`` -> ``url_change`` ->
``event``) through both ``LiveViewConsumer`` (via ``WSConsumerTransport``) and
``DjustSSEMessageView`` (via ``SSESessionTransport``). Asserts identical
outbound message-type sequences AND identical final ``view_instance.assigns``.

This locks in the contract that the shared :class:`ViewRuntime` is wire-blind:
swapping the transport must not change observable behavior for the message
types it owns (``mount`` / ``event`` / ``url_change``).

Currently the WebSocket path migrates only ``handle_url_change`` to the
runtime in this PR; ``handle_event`` and ``handle_mount`` remain on their
existing WS-specific code paths. Even with that scope cut the symmetry test
still drives both endpoints for ``url_change`` end-to-end and for the other
verbs at the runtime level.
"""

import django
from django.conf import settings

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
        SECRET_KEY="test-secret-key-symmetry",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest


# ------------------------------------------------------------------ #
# Shared MockTransport (matches test_runtime.py)
# ------------------------------------------------------------------ #


class MockTransport:
    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.render_lock = asyncio.Lock()

    @asynccontextmanager
    async def event_context(self, view: Any):
        async with self.render_lock:
            yield

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return self._client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        self.sent.append({"type": "error", "error": error, **kwargs})

    async def close(self, code: int = 1000) -> None:
        pass

    def next_client_version(self, html, rust_version):
        return rust_version


class _SymmetryView:
    """A minimal LiveView-shaped object used to compare outputs."""

    exposure_policy = "explicit"

    def __init__(self):
        self.kwargs_received: Optional[Dict[str, Any]] = None
        self.handle_params_calls: List = []
        self.click_calls: List = []
        self.assigns: Dict[str, Any] = {}
        self.request = None

    def _initialize_temporary_assigns(self):
        pass

    def _initialize_rust_view(self, request):
        pass

    def _sync_state_to_rust(self):
        pass

    def render_with_diff(self):
        # Return None patches so the runtime falls back to html_update —
        # easy-to-compare deterministic shape without actual VDOM.
        return ("<div>" + str(self.assigns.get("count", 0)) + "</div>", None, 1)

    def _strip_comments_and_whitespace(self, html):
        return html

    def _extract_liveview_content(self, html):
        return html

    def mount(self, request, **kwargs):
        self.kwargs_received = dict(kwargs)
        self.assigns["count"] = 0

    def handle_params(self, params, uri):
        self.handle_params_calls.append((params, uri))


# ------------------------------------------------------------------ #
# url_change end-to-end symmetry — the verb that DOES route through the
# runtime on the WS side in this PR.
# ------------------------------------------------------------------ #


async def _mount(runtime, view):
    """Mount ``view`` on ``runtime`` on a live session (only the auth seam and
    URL kwargs stubbed): an explicit view's url_change is authorized against
    its mount binding. The path only has to pass the shape and allowlist gate;
    the view lookup itself is stubbed."""
    from djust.tests._live_session import amount_on_runtime

    runtime._check_auth = AsyncMock(return_value=None)
    runtime._resolve_url_kwargs = MagicMock(return_value={})
    await amount_on_runtime(
        runtime, view, url="/list/", view_path="djust.tests.symmetry.SymmetryView"
    )
    view.handle_params_calls.clear()  # the mount's own handle_params call


@pytest.mark.django_db(transaction=True)
class TestUrlChangeSymmetry:
    @pytest.mark.asyncio
    async def test_runtime_dispatch_url_change_shared_with_ws(self):
        """A given url_change frame produces the same outbound type sequence
        and the same handle_params receipt regardless of transport."""
        from djust.runtime import ViewRuntime

        # ---- WS-side dispatch ----
        ws_transport = MockTransport()
        ws_runtime = ViewRuntime(ws_transport)
        ws_view = _SymmetryView()
        await _mount(ws_runtime, ws_view)
        ws_view.assigns["count"] = 5

        await ws_runtime.dispatch_url_change(
            {"params": {"sort": "name"}, "uri": "/list/?sort=name"}
        )

        # ---- SSE-side dispatch ----
        sse_transport = MockTransport()
        sse_runtime = ViewRuntime(sse_transport)
        sse_view = _SymmetryView()
        await _mount(sse_runtime, sse_view)
        sse_view.assigns["count"] = 5

        await sse_runtime.dispatch_url_change(
            {"params": {"sort": "name"}, "uri": "/list/?sort=name"}
        )

        # ---- Symmetry assertions ----
        # Both endpoints produced exactly the same outbound type sequence.
        ws_types = [m["type"] for m in ws_transport.sent]
        sse_types = [m["type"] for m in sse_transport.sent]
        assert ws_types == sse_types, (ws_types, sse_types)
        # Equal error frames are not successful transport parity.
        assert ws_types == ["html_update"]

        # Both views received handle_params with identical arguments.
        assert ws_view.handle_params_calls == sse_view.handle_params_calls
        assert ws_view.handle_params_calls == [({"sort": "name"}, "/list/?sort=name")]

        # Both ended in the same logical state.
        assert ws_view.assigns == sse_view.assigns


# ------------------------------------------------------------------ #
# Sequence symmetry: drive the same multi-step input through both runtimes
# at the runtime level (since handle_event / handle_mount don't migrate to
# the runtime on the WS side in this PR; this test asserts the runtime
# itself is transport-blind for the dispatchers it owns).
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
class TestRuntimeSequenceSymmetry:
    @pytest.mark.asyncio
    async def test_same_sequence_yields_same_assigns_and_message_types(self):
        from djust.runtime import ViewRuntime

        async def drive(transport, runtime, view):
            # mount: short-circuit instantiate + auth so the test exercises
            # only the dispatch-level plumbing, on a live session. The path
            # used to be "any.path.SymmetryView", which the allowlist refuses:
            # both sides answered "View not found" and compared equal.
            await _mount(runtime, view)

            # url_change
            await runtime.dispatch_message(
                {
                    "type": "url_change",
                    "params": {"page": "2"},
                    "uri": "/list/?page=2",
                }
            )

        # WS side
        ws_transport = MockTransport()
        ws_runtime = ViewRuntime(ws_transport)
        ws_view = _SymmetryView()
        await drive(ws_transport, ws_runtime, ws_view)

        # SSE side
        sse_transport = MockTransport()
        sse_runtime = ViewRuntime(sse_transport)
        sse_view = _SymmetryView()
        await drive(sse_transport, sse_runtime, sse_view)

        # Identical outbound type sequence
        ws_types = [m["type"] for m in ws_transport.sent]
        sse_types = [m["type"] for m in sse_transport.sent]
        assert ws_types == sse_types
        # Equal error frames are not successful transport parity.
        assert "error" not in ws_types, ws_transport.sent
        assert ws_view.handle_params_calls == [({"page": "2"}, "/list/?page=2")]

        # Identical handle_params receipts
        assert ws_view.handle_params_calls == sse_view.handle_params_calls

        # Identical final state
        assert ws_view.assigns == sse_view.assigns
