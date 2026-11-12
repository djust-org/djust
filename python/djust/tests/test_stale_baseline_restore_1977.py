"""Reconnect / state-restore stale-baseline fix (#1977).

Symptom (reproduced bit-exact by the maintainer in real Chromium): after a
WebSocket reconnect / state-restore between events (laptop sleep, network blip,
server restart), the fresh view instance's Rust diff baseline is primed from a
render that does NOT match the client's pre-disconnect DOM. Diffing the FIRST
post-restore EVENT against that stale baseline lands ``SetText`` patches on the
wrong node (often a bare ``#text`` node)::

    [LiveView] Path traversal failed at index 1, only 0 children (parent: #text).
    [LiveView] Patch failed (SetText): node not found at path=3/13/1/5/7/1/0 ...
    2/220 patches failed → html_recovery

The fix (``python/djust/runtime.py`` ``ViewRuntime.dispatch_mount``): on a
restore mount, set ``view._force_full_html = True`` so the FIRST post-restore
render emits a full ``html_update`` frame — the client morphs wholesale and the
Rust baseline is re-primed to the live DOM, so no stale-baseline diff can reach
the client.

The restore here is the one 2.0 has: declared ``state(persist="client")``
fields put back from the signed client token after ``mount()``. (The legacy
session restore and signed snapshot the fix was first written for were removed
in 2.0.)

These tests drive ``dispatch_mount`` DIRECTLY against a ``MockTransport`` with a
REAL Django request + DB session, then drive ``dispatch_event`` (the runtime
event spine that CONSUMES ``_force_full_html``) for the first post-restore
event.

Non-tautological / gate-off pair (#1200 / #1468): the restore test and the
NO-restore test use the SAME state-changing handler on the SAME view. The only
difference is the restore path setting ``_force_full_html`` — restore →
``html_update`` (full HTML), fresh mount → ``patch`` (normal diff). Gate off the
fix (comment out ``view_instance._force_full_html = True`` in ``dispatch_mount``)
and the restore test's ``html_update`` assertion goes RED while the fresh-mount
``patch`` assertion stays green.
"""

from __future__ import annotations

import contextlib
import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.runtime import ViewRuntime
from djust.decorators import state

pytestmark = pytest.mark.django_db

# The test views live under ``djust.tests.…`` so the mount allowlist must admit
# the ``djust`` module root (mirrors test_runtime_mount_state_restore_1913.py).
_ALLOWLIST = override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"])


# --------------------------------------------------------------------------- #
# Test transport — records send() calls; build_request returns a real request;
# event_context is the SSE-shape no-op (no consumer lock to borrow, #1899).
# --------------------------------------------------------------------------- #
class MockTransport:
    def __init__(self, request: Any, session_id: Optional[str] = None):
        self._request = request
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
        return self._request

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        yield

    @property
    def mount_frame(self) -> Optional[Dict[str, Any]]:
        for msg in self.sent:
            if msg.get("type") == "mount":
                return msg
        return None

    def frames_of(self, *types: str) -> List[Dict[str, Any]]:
        return [m for m in self.sent if m.get("type") in types]


# --------------------------------------------------------------------------- #
# Test view — a real template + a state-changing @event_handler, so the NORMAL
# (no-restore) event path yields a real VDOM patch.
# --------------------------------------------------------------------------- #
def _register(view_cls):
    setattr(sys.modules[__name__], view_cls.__name__, view_cls)
    return f"{__name__}.{view_cls.__name__}"


class _ClientFilterView(LiveView):
    """The explicit twin: ``n`` is a declared field the browser holds signed."""

    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0">n={{ n }}</div>'
    n = state(0, persist="client", client=True)

    def mount(self, request, **kwargs):
        self.n = 5

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        return {"n": self.n}


CLIENT_VIEW_PATH = f"{__name__}._ClientFilterView"
TOKEN = "state_snapshot_signed"
PAGE_URL = "/feed/"


# --------------------------------------------------------------------------- #
# Helpers — real DB session + real request + a runtime wired to dispatch_mount.
# --------------------------------------------------------------------------- #
def _make_db_session():
    from django.contrib.sessions.backends.db import SessionStore

    s = SessionStore()
    s.create()
    return s


def _load_db_session(session_key):
    from django.contrib.sessions.backends.db import SessionStore

    return SessionStore(session_key=session_key)


def _make_request(session):
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    request = RequestFactory().get(PAGE_URL)
    request.user = AnonymousUser()
    request.session = session
    return request


def _make_runtime(request):
    transport = MockTransport(request)
    return ViewRuntime(transport, scope=None), transport


def _fresh_event_requests(transport, session):
    """An explicit turn is authorized against a fresh request (ADR-038)."""

    async def fresh_event_request(view):
        return await sync_to_async(_make_request)(
            await sync_to_async(_load_db_session)(session.session_key)
        )

    transport.explicit_event_request = fresh_event_request


async def _mount(runtime, data):
    with _ALLOWLIST:
        await runtime.dispatch_mount(data)


async def _event(runtime, name, **params):
    with _ALLOWLIST:
        await runtime.dispatch_event({"type": "event", "event": name, "params": params, "ref": 1})


# --------------------------------------------------------------------------- #
# (A) Restore path — the fix: first post-restore render is a full html_update.
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestRestoreForcesFullHtmlFirstRender:
    @pytest.mark.asyncio
    async def test_signed_client_snapshot_restore_first_event_is_full_html(self, monkeypatch):
        """The explicit restore (declared ``persist="client"`` fields, from the
        signed client token) also arms the fix (#1646): the first post-restore
        event is a full ``html_update``."""
        session = await sync_to_async(_make_db_session)()
        first, first_transport = _make_runtime(_make_request(session))
        await _mount(first, {"type": "mount", "view": CLIENT_VIEW_PATH, "url": PAGE_URL})
        token = first_transport.mount_frame[TOKEN]
        assert token, f"the mount must ship the signed token; got {first_transport.sent!r}"
        # The reconnect's mount() leaves the default, so 5 can only be restored.
        monkeypatch.setattr(_ClientFilterView, "mount", lambda self, request, **kwargs: None)

        runtime, transport = _make_runtime(_make_request(session))
        await _mount(
            runtime,
            {
                "type": "mount",
                "view": CLIENT_VIEW_PATH,
                "url": PAGE_URL,
                "state_snapshot": {"view_slug": CLIENT_VIEW_PATH, "state_json": token},
                "has_prerendered": True,
            },
        )
        _fresh_event_requests(transport, session)
        view = runtime.view_instance
        assert view is not None
        assert view.n == 5, "the signed client snapshot must restore the declared field"
        assert view._force_full_html is True, "the #1977 fix must arm on a client-token restore"

        await _event(runtime, "bump")

        assert not transport.frames_of("patch"), (
            f"client-token restore first event must not patch; got {transport.sent!r}"
        )
        assert transport.frames_of("html_update"), (
            f"client-token restore first event must html_update; got {transport.sent!r}"
        )


# --------------------------------------------------------------------------- #
# (B) NO-restore path — scoped: fresh mount + same event yields a normal patch.
#     Proves the fix does NOT force full HTML on every mount (no perf regression)
#     and is the built-in gate-off sibling (#1468): identical handler + view, the
#     ONLY difference is the absent restore.
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestFreshMountUnaffected:
    @pytest.mark.asyncio
    async def test_fresh_mount_first_event_is_normal_patch(self):
        """A fresh mount (no token) never arms the fix; the first event is a
        NORMAL VDOM ``patch`` diffed against the mount baseline — the fix is
        scoped to the restore path only."""
        session = await sync_to_async(_make_db_session)()
        runtime, transport = _make_runtime(_make_request(session))
        await _mount(runtime, {"type": "mount", "view": CLIENT_VIEW_PATH, "url": PAGE_URL})
        _fresh_event_requests(transport, session)
        view = runtime.view_instance
        assert view is not None
        assert view.n == 5, "fresh mount installs the mount() state"
        assert getattr(view, "_force_full_html", False) is False, (
            "fresh mount must NOT arm _force_full_html (no perf regression)"
        )

        await _event(runtime, "bump")

        assert transport.frames_of("patch"), (
            f"fresh-mount first event must emit a normal patch; got {transport.sent!r}"
        )
        assert not transport.frames_of("html_update"), (
            f"fresh-mount first event must NOT force full HTML; got {transport.sent!r}"
        )
