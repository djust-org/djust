"""Direct-runtime tests for mount state-restore + run_on_mount_hooks
(ADR-022 Iter 3 Phase 3.1, issue #1913).

Phase 3.1 ported the transport-agnostic mount STATE-RESTORE + on-mount hooks
into :class:`djust.runtime.ViewRuntime.dispatch_mount`, the mount spine every
transport uses. What is left of it here:

1. **run_on_mount_hooks** — runs registered ``on_mount`` hooks after auth,
   before mount(); a returned redirect → a ``navigate`` frame + abort.
2. **Session-saved-state restore** — gated on ``enable_state_snapshot``
   (#1552), runs in lieu of mount() and sets the ``_mounted_from_restore``
   resume flag.

The signed state-snapshot restore and emit this file also covered were removed
in 2.0 with the legacy signed snapshot; the explicit client snapshot is covered
in ``test_exposure_runtime.py`` and ``test_snapshot_persistence_after_removal.py``.

These tests drive ``runtime.dispatch_mount`` DIRECTLY against a MockTransport
whose ``build_request`` returns a REAL Django request carrying a REAL DB session,
so the runtime mount path runs end-to-end (resolve view → auth → hooks → restore
→ render → mount frame).
"""

from __future__ import annotations

import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.runtime import ViewRuntime

pytestmark = pytest.mark.django_db

# The test views live under ``djust.tests.…`` so the mount allowlist must admit
# the ``djust`` module root (the demo_project test settings allowlist only the
# demo apps). Mirrors the SSE convergence test's ``_allowlist`` pattern.
_ALLOWLIST = override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"])


# --------------------------------------------------------------------------- #
# Test transport — records send() calls; build_request returns a REAL request.
# --------------------------------------------------------------------------- #


class MockTransport:
    """Minimal Transport whose ``build_request`` hands the runtime a real Django
    request (with a real DB session) so the converged mount path runs against a
    genuine session — the SSE shape, which mounts off the live HTTP request."""

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

    @property
    def mount_frame(self) -> Optional[Dict[str, Any]]:
        for msg in self.sent:
            if msg.get("type") == "mount":
                return msg
        return None

    @property
    def navigate_frames(self) -> List[Dict[str, Any]]:
        return [m for m in self.sent if m.get("type") == "navigate"]


# --------------------------------------------------------------------------- #
# Test views — registered as module attrs for dotted-path resolution.
# --------------------------------------------------------------------------- #


class _OptInSnapView(LiveView):
    """Opt-in snapshot view. ``mount`` installs a safe default; a snapshot that
    survives the HMAC gate would overwrite ``role`` (the injection vector)."""

    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">role={{ role }} n={{ n }}</div>'

    def mount(self, request, **kwargs):
        self.role = "user"  # safe default mount() always installs
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"role": self.role, "n": self.n}


class _DefaultSnapView(LiveView):
    """Does NOT opt in (#1552: must never restore / emit)."""

    exposure_policy = "legacy"
    template = '<div dj-root dj-id="0">role={{ role }} n={{ n }}</div>'

    def mount(self, request, **kwargs):
        self.role = "user"
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"role": self.role, "n": self.n}


class _RedirectHookView(LiveView):
    """A view whose registered on_mount hook redirects (login-required shape)."""

    exposure_policy = "legacy"
    template = '<div dj-root dj-id="0">never rendered</div>'

    def mount(self, request, **kwargs):
        self.mounted_ran = True

    def get_context_data(self, **kwargs):
        return {}


def _register(view_cls):
    setattr(sys.modules[__name__], view_cls.__name__, view_cls)
    return f"{__name__}.{view_cls.__name__}"


VIEW_PATH = f"{__name__}._OptInSnapView"
DEFAULT_PATH = f"{__name__}._DefaultSnapView"
REDIRECT_PATH = f"{__name__}._RedirectHookView"
PAGE_URL = "/snap/"


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
    """Drive ``dispatch_mount`` under the test allowlist (override_settings is a
    context manager here — it cannot decorate a plain pytest class)."""
    with _ALLOWLIST:
        await runtime.dispatch_mount(data)


# --------------------------------------------------------------------------- #
# (A) Signed state_snapshot HMAC restore — security boundary (#1046 caps TDD).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# (B) state_snapshot_signed EMIT — opt-in only.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# (C) Session-saved-state restore — plain reconnect resume.
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestRuntimeSessionRestore:
    @pytest.mark.asyncio
    async def test_opt_in_session_saved_state_restores_on_mount(self):
        """An opt-in view whose state was saved to the session (the #1466
        per-event save) restores on a runtime reconnect-mount in lieu of mount().

        Reproduce-first (#1200): mount() installs role='user'/n=0; only a
        surviving session-restore yields role='admin'/n=42.
        """
        session = await sync_to_async(_make_db_session)()
        view_key = f"liveview_{PAGE_URL}"
        await session.aset(view_key, {"role": "admin", "n": 42})
        await sync_to_async(session.save)()

        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL})
        view = runtime.view_instance
        assert view is not None
        assert view.role == "admin", "session-saved state must restore on opt-in reconnect"
        assert view.n == 42
        assert view._mounted_from_restore is True

    @pytest.mark.asyncio
    async def test_default_view_ignores_session_saved_state(self):
        """Gate-off (#1552): a non-opt-in view with the SAME saved state in the
        session does NOT restore — a fresh mount() runs (the #1466 clobbered-
        baseline regression fix). role stays at mount()'s default.
        """
        session = await sync_to_async(_make_db_session)()
        view_key = f"liveview_{PAGE_URL}"
        await session.aset(view_key, {"role": "admin", "n": 42})
        await sync_to_async(session.save)()

        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(runtime, {"type": "mount", "view": DEFAULT_PATH, "url": PAGE_URL})
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "non-opt-in view must ignore session state (#1552)"
        assert view.n == 0
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_opt_in_no_saved_state_runs_fresh_mount(self):
        """An opt-in view with NO saved state runs a normal mount() (no-op
        restore — the SSE 'no snapshot present' default)."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL})
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user"
        assert view._mounted_from_restore is False


# --------------------------------------------------------------------------- #
# (D) run_on_mount_hooks — redirect → navigate frame via the runtime.
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestRuntimeOnMountHooks:
    @pytest.mark.asyncio
    async def test_on_mount_redirect_emits_navigate_and_aborts(self):
        """A registered on_mount hook that returns a redirect URL emits a
        ``navigate`` frame and aborts the mount (no mount frame, view cleared).
        """
        from unittest.mock import patch

        _register(_RedirectHookView)
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)

        # run_on_mount_hooks is called as ``djust.hooks.run_on_mount_hooks`` from
        # the runtime; patch it to return a redirect URL (the login-required
        # shape) without registering a global hook.
        with patch("djust.hooks.run_on_mount_hooks", return_value="/login/"):
            await _mount(runtime, {"type": "mount", "view": REDIRECT_PATH, "url": PAGE_URL})

        assert runtime.view_instance is None, "redirecting hook must clear the view"
        navs = transport.navigate_frames
        assert navs, "redirect hook must emit a navigate frame"
        assert navs[0]["to"] == "/login/"
        assert transport.mount_frame is None, "no mount frame after a hook redirect"

    @pytest.mark.asyncio
    async def test_no_redirect_hook_proceeds_to_mount(self):
        """Gate-off for the hook redirect: a hook returning None proceeds to a
        normal mount (the hook ran, mount() ran, a mount frame was emitted)."""
        from unittest.mock import patch

        _register(_RedirectHookView)
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)

        with patch("djust.hooks.run_on_mount_hooks", return_value=None):
            await _mount(runtime, {"type": "mount", "view": REDIRECT_PATH, "url": PAGE_URL})

        assert runtime.view_instance is not None, "no-redirect hook must proceed to mount"
        assert runtime.view_instance.mounted_ran is True
        assert transport.navigate_frames == []
        assert transport.mount_frame is not None
