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

These tests drive ``dispatch_mount`` (the converged WS + SSE + runtime mount
seam — WS ``handle_mount`` is a thin shim to it, websocket.py:2209) DIRECTLY
against a ``MockTransport`` with a REAL Django request + DB session, then drive
``dispatch_event`` (the runtime event spine that CONSUMES ``_force_full_html``)
for the first post-restore event. The harness mirrors
``test_runtime_mount_state_restore_1913.py`` (mount + restore) and
``test_transport_behavioral_parity.py`` (event ``event_context``).

Reproduction fidelity / render-timing (#1977 brief): the resume case uses
``has_prerendered=True`` so the mount frame's HTML is SKIPPED
(``skip_html_for_resume``) — proving the flag is consumed by the FIRST EVENT
render, NOT prematurely by the mount-time render whose HTML the resume drops.

Non-tautological / gate-off pair (#1200 / #1468): the restore test and the
NO-restore test use the SAME state-changing handler on the SAME view. The only
difference is the restore path setting ``_force_full_html`` — restore →
``html_update`` (full HTML), fresh mount → ``patch`` (normal diff). Gate off the
fix (comment out ``view_instance._force_full_html = True`` in ``dispatch_mount``)
and the restore tests' ``html_update`` assertions go RED while the fresh-mount
``patch`` assertion stays green.
"""

from __future__ import annotations

import contextlib
import json
import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.runtime import ViewRuntime
from djust.security import sign_snapshot

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
# Test view — opt-in snapshot view with a real template + a state-changing
# @event_handler so the NORMAL (no-restore) event path yields a real VDOM patch.
# --------------------------------------------------------------------------- #
class _FilterView(LiveView):
    """Opt-in snapshot view. ``mount`` installs the DEFAULT (unfiltered) state;
    a surviving restore overwrites it with the saved (filtered) state — the
    reconnect scenario where the fresh-mount baseline diverges from the client's
    live DOM."""

    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">author={{ author }} n={{ n }}</div>'

    def mount(self, request, **kwargs):
        self.author = "all"  # DEFAULT (unfiltered) — a fresh mount's baseline
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        # A single-value change → a SetText patch on the NORMAL path.
        self.n += 1

    def get_context_data(self, **kwargs):
        return {"author": self.author, "n": self.n}


def _register(view_cls):
    setattr(sys.modules[__name__], view_cls.__name__, view_cls)
    return f"{__name__}.{view_cls.__name__}"


VIEW_PATH = f"{__name__}._FilterView"
PAGE_URL = "/feed/"


# --------------------------------------------------------------------------- #
# Helpers — real DB session + real request + a runtime wired to dispatch_mount.
# --------------------------------------------------------------------------- #
def _make_db_session():
    from django.contrib.sessions.backends.db import SessionStore

    s = SessionStore()
    s.create()
    return s


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


async def _mount(runtime, data):
    with _ALLOWLIST:
        await runtime.dispatch_mount(data)


async def _event(runtime, name, **params):
    with _ALLOWLIST:
        await runtime.dispatch_event({"type": "event", "event": name, "params": params, "ref": 1})


def _signed_snapshot(state: Dict[str, Any], *, slug: str, session_key: Optional[str]):
    inner = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return {"view_slug": slug, "state_json": sign_snapshot(inner, slug, session_key)}


# --------------------------------------------------------------------------- #
# (A) Restore path — the fix: first post-restore render is a full html_update.
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestRestoreForcesFullHtmlFirstRender:
    @pytest.mark.asyncio
    async def test_session_restore_first_event_is_full_html_not_patch(self):
        """Session-saved-state reconnect (the #1466 per-event save) → the fix sets
        ``_force_full_html`` on the restore mount; the FIRST post-restore event
        emits a full ``html_update`` (no stale-baseline diff), not a ``patch``.

        ``has_prerendered=True`` so the mount frame drops its HTML
        (``skip_html_for_resume``) — proving the flag survives to the EVENT render
        rather than being consumed by the (skipped) mount-time render.
        """
        session = await sync_to_async(_make_db_session)()
        view_key = f"liveview_{PAGE_URL}"
        # Client's live DOM before disconnect = the FILTERED state.
        await session.aset(view_key, {"author": "alice", "n": 42})
        await sync_to_async(session.save)()

        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(
            runtime,
            {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "has_prerendered": True},
        )
        view = runtime.view_instance
        assert view is not None
        # Restore ran (in lieu of mount) and the fix armed the baseline re-sync.
        assert view._mounted_from_restore is True, "session-saved state must restore"
        assert view.author == "alice", "restore must overwrite the mount default"
        assert view._force_full_html is True, (
            "the #1977 fix must set _force_full_html on a restore mount (gate-off target)"
        )
        # Resume → mount HTML skipped, so the flag can only be consumed by the event.
        mount_frame = transport.mount_frame
        assert mount_frame is not None and "html" not in mount_frame, (
            "resume mount frame must skip HTML (skip_html_for_resume)"
        )

        await _event(runtime, "bump")

        patches = transport.frames_of("patch")
        updates = transport.frames_of("html_update")
        assert not patches, (
            f"first post-restore event must NOT diff against the stale baseline "
            f"(no patch frame); got {transport.sent!r}"
        )
        assert updates, (
            f"first post-restore event must emit a full html_update; got {transport.sent!r}"
        )
        assert view._force_full_html is False, "flag must be consumed after exactly one render"

    @pytest.mark.asyncio
    async def test_signed_snapshot_restore_first_event_is_full_html(self):
        """Signed-snapshot HMAC restore (the other #1646 restore mechanism) also
        arms the fix — the first post-restore event is a full ``html_update``."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        snap = _signed_snapshot(
            {"author": "bob", "n": 7}, slug=VIEW_PATH, session_key=session.session_key
        )
        await _mount(
            runtime,
            {
                "type": "mount",
                "view": VIEW_PATH,
                "url": PAGE_URL,
                "state_snapshot": snap,
                "has_prerendered": True,
            },
        )
        view = runtime.view_instance
        assert view is not None
        assert view._mounted_from_restore is True, "signed snapshot must restore"
        assert view.author == "bob"
        assert view._force_full_html is True, "the #1977 fix must arm on signed-snapshot restore"

        await _event(runtime, "bump")

        assert not transport.frames_of("patch"), (
            f"signed-snapshot restore first event must not patch; got {transport.sent!r}"
        )
        assert transport.frames_of("html_update"), (
            f"signed-snapshot restore first event must html_update; got {transport.sent!r}"
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
        """A fresh mount (no saved state, no snapshot) never arms the fix; the
        first event is a NORMAL VDOM ``patch`` diffed against the mount baseline —
        the fix is scoped to the restore path only."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL})
        view = runtime.view_instance
        assert view is not None
        assert view._mounted_from_restore is False, "fresh mount must not restore"
        assert view.author == "all", "fresh mount installs the default state"
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
