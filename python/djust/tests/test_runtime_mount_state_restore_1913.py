"""Direct-runtime tests for mount state-restore + signed-snapshot restore/emit
+ run_on_mount_hooks (ADR-022 Iter 3 Phase 3.1, issue #1913).

Phase 3.1 ports the transport-agnostic mount STATE-RESTORE + on-mount hooks the
WS ``handle_mount`` has into :class:`djust.runtime.ViewRuntime.dispatch_mount` so
the runtime mount spine — which is the SSE mount path (Iter 1) — restores +
emits signed snapshots identically. Three ports:

1. **run_on_mount_hooks** (WS websocket.py:2383-2401) — runs registered
   ``on_mount`` hooks after auth, before mount(); a returned redirect → a
   ``navigate`` frame + abort (transport-agnostic: no socket close — that is the
   3.2/3.3a ``finalize_mount_auth`` hook).
2. **Session-saved-state restore** (WS websocket.py:2424-2474) + **signed
   state_snapshot HMAC restore** (WS websocket.py:2491-2587, SECURITY-BOUNDARY) —
   both gated on ``enable_state_snapshot`` (#1552), run in lieu of mount() and set
   the ``_mounted_from_restore`` resume flag.
3. **state_snapshot_signed EMIT** (WS websocket.py:2754-2792) — ship the
   server-signed blob on the mount frame for opt-in views.

The HMAC caps (slug / sid / TTL binding + size/keyset/dict caps + the
``_should_restore_snapshot`` gate) are byte-identical to WS — this is the
security boundary. The **doc-claim-verbatim HMAC-caps TDD** (#1046) asserts the
caps documented in ``python/djust/security/state_snapshot.py``: a snapshot signed
for view A / session S1 / older than MAX_AGE does NOT restore via the runtime
path. Each cap has a **gate-off** sibling (#1468) that weakens it (forge / wrong
slug / wrong session / expired) and proves the legitimate restore path works only
when the cap passes.

These tests drive ``runtime.dispatch_mount`` DIRECTLY against a MockTransport
whose ``build_request`` returns a REAL Django request carrying a REAL DB session,
so the runtime mount path runs end-to-end (resolve view → auth → hooks → restore
→ render → mount frame). The WS ``handle_mount`` is UNTOUCHED — its #1466/#1552
grep-pins in ``test_ws_reconnect_state_1465.py`` + the signing pins in
``test_state_snapshot_signing.py`` still own the WS path.
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
from djust.security import sign_snapshot

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

    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">role={{ role }} n={{ n }}</div>'

    def mount(self, request, **kwargs):
        self.role = "user"  # safe default mount() always installs
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"role": self.role, "n": self.n}


class _DefaultSnapView(LiveView):
    """Does NOT opt in (#1552: must never restore / emit)."""

    template = '<div dj-root dj-id="0">role={{ role }} n={{ n }}</div>'

    def mount(self, request, **kwargs):
        self.role = "user"
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"role": self.role, "n": self.n}


class _RedirectHookView(LiveView):
    """A view whose registered on_mount hook redirects (login-required shape)."""

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


def _signed_snapshot(state: Dict[str, Any], *, slug: str, session_key: Optional[str]):
    """Build the client-echoed snapshot dict the runtime reads from
    ``data['state_snapshot']`` (mirrors 18-navigation.js / 46-state-snapshot.js).
    The inner ``state_json`` is the OPAQUE server-signed blob.
    """
    import json

    inner = json.dumps(state, sort_keys=True, separators=(",", ":"))
    signed = sign_snapshot(inner, slug, session_key)
    return {"view_slug": slug, "state_json": signed}


# --------------------------------------------------------------------------- #
# (A) Signed state_snapshot HMAC restore — security boundary (#1046 caps TDD).
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestRuntimeSignedSnapshotRestore:
    @pytest.mark.asyncio
    async def test_valid_signed_snapshot_restores_via_runtime(self):
        """A snapshot signed for THIS view + THIS session restores its public
        state in lieu of mount() — the runtime mount path now supports it.

        Reproduce-first / non-tautological (#1200): mount() installs role='user';
        only a surviving restore yields role='admin'.
        """
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        snap = _signed_snapshot(
            {"role": "admin", "n": 7}, slug=VIEW_PATH, session_key=session.session_key
        )
        await _mount(
            runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "state_snapshot": snap}
        )
        view = runtime.view_instance
        assert view is not None, "valid snapshot must produce a mounted view"
        assert view.role == "admin", "valid signed snapshot must restore public state"
        assert view.n == 7
        assert view._mounted_from_restore is True

    @pytest.mark.asyncio
    async def test_forged_unsigned_snapshot_rejected_falls_back_to_mount(self):
        """An UNSIGNED (forged) blob fails the signature cap → falls back to
        mount() → state stays at the safe default. Gate-off for the signature
        cap: the only difference from the passing test is the missing signature.
        """
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        # A forged raw JSON blob the attacker hopes is trusted verbatim.
        import json

        forged = {
            "view_slug": VIEW_PATH,
            "state_json": json.dumps({"role": "admin", "n": 7}),  # NOT signed
        }
        await _mount(
            runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "state_snapshot": forged}
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "forged snapshot must be rejected (signature cap)"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_cross_view_snapshot_rejected_slug_cap(self):
        """A snapshot signed for view A (slug cap) does NOT restore on view B.

        Doc claim (state_snapshot.py): 'A snapshot signed for view A cannot be
        replayed onto view B.' Gate-off: a snapshot validly signed but for a
        DIFFERENT slug — the signature is good, only the slug binding fails.
        """
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        # Validly signed, but bound to a DIFFERENT view slug.
        snap = _signed_snapshot(
            {"role": "admin", "n": 7},
            slug="djust.tests.OtherView",
            session_key=session.session_key,
        )
        # The frame's view_slug must match the mounting view_path to even reach
        # unsign_snapshot (WS websocket.py:2506); set it to VIEW_PATH so the
        # slug-binding cap inside unsign_snapshot is what rejects (not the outer
        # frame-slug guard).
        snap["view_slug"] = VIEW_PATH
        await _mount(
            runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "state_snapshot": snap}
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "cross-view snapshot must be rejected (slug cap)"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_cross_session_snapshot_rejected_sid_cap(self):
        """A snapshot signed under session S1 (sid cap) does NOT restore under S2.

        Doc claim (state_snapshot.py): 'A snapshot captured under session S1
        cannot be replayed onto session S2.' Gate-off: sign with a foreign
        session key; the signature is good, only the sid binding fails.
        """
        session = await sync_to_async(_make_db_session)()
        other_session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        assert session.session_key != other_session.session_key
        snap = _signed_snapshot(
            {"role": "admin", "n": 7},
            slug=VIEW_PATH,
            session_key=other_session.session_key,  # FOREIGN session
        )
        await _mount(
            runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "state_snapshot": snap}
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "cross-session snapshot must be rejected (sid cap)"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_expired_snapshot_rejected_ttl_cap(self):
        """A snapshot older than MAX_AGE (TTL cap) does NOT restore.

        Doc claim (state_snapshot.py): stale snapshots beyond the TTL are
        rejected. Gate-off: a validly-signed, correctly-bound snapshot that is
        only DEFEATED by the age cap (DJUST_STATE_SNAPSHOT_MAX_AGE=0 forces
        every snapshot to read as expired via get_max_age's positive-int floor —
        so instead we shrink the signer's effective age by signing in the past).
        """
        import time
        from unittest.mock import patch

        from django.test import override_settings

        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        snap = _signed_snapshot(
            {"role": "admin", "n": 7}, slug=VIEW_PATH, session_key=session.session_key
        )
        # MAX_AGE=1s; sign-time is "now", but we advance time past the TTL so the
        # TimestampSigner.unsign(max_age=1) raises SignatureExpired.
        with override_settings(DJUST_STATE_SNAPSHOT_MAX_AGE=1):
            real_time = time.time

            def _future():
                return real_time() + 5  # 5s later → past the 1s TTL

            with patch("django.core.signing.time.time", _future):
                await _mount(
                    runtime,
                    {
                        "type": "mount",
                        "view": VIEW_PATH,
                        "url": PAGE_URL,
                        "state_snapshot": snap,
                    },
                )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "expired snapshot must be rejected (TTL cap)"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_tampered_signed_blob_rejected(self):
        """A bit-flipped signed blob → BadSignature → dropped (tamper cap)."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        snap = _signed_snapshot(
            {"role": "admin", "n": 7}, slug=VIEW_PATH, session_key=session.session_key
        )
        # Flip the last char of the opaque signed blob.
        blob = snap["state_json"]
        snap["state_json"] = blob[:-1] + ("a" if blob[-1] != "a" else "b")
        await _mount(
            runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "state_snapshot": snap}
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "tampered snapshot must be rejected"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_default_view_never_restores_even_with_valid_signature(self):
        """A view that does NOT opt in (#1552) never restores — the
        ``enable_state_snapshot`` gate. Gate-off for the opt-in gate: the SAME
        validly-signed snapshot that restores on the opt-in view is ignored here.
        """
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        snap = _signed_snapshot(
            {"role": "admin", "n": 7}, slug=DEFAULT_PATH, session_key=session.session_key
        )
        await _mount(
            runtime,
            {"type": "mount", "view": DEFAULT_PATH, "url": PAGE_URL, "state_snapshot": snap},
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "non-opt-in view must never restore (#1552 gate)"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_oversized_inner_json_rejected_size_cap(self):
        """A verified inner JSON > 64KB is rejected (size cap, WS Fix #6)."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        big = "x" * 70000  # > 65536 once serialized
        snap = _signed_snapshot(
            {"role": "admin", "blob": big}, slug=VIEW_PATH, session_key=session.session_key
        )
        await _mount(
            runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "state_snapshot": snap}
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "oversized snapshot must be rejected (size cap)"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_oversized_keyset_rejected_keyset_cap(self):
        """A verified snapshot with > 256 keys is rejected (keyset cap, WS Fix #7)."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        payload = {f"k{i}": i for i in range(300)}
        payload["role"] = "admin"
        snap = _signed_snapshot(payload, slug=VIEW_PATH, session_key=session.session_key)
        await _mount(
            runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL, "state_snapshot": snap}
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "oversized-keyset snapshot must be rejected (keyset cap)"
        assert view._mounted_from_restore is False

    @pytest.mark.asyncio
    async def test_should_restore_snapshot_gate_blocks_restore(self):
        """``_should_restore_snapshot(request)`` returning False blocks a
        validly-signed snapshot (the view-level veto cap)."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)

        veto_path = _register(
            type(
                "_VetoSnapView",
                (_OptInSnapView,),
                {
                    "_should_restore_snapshot": lambda self, req: False,
                    "template": '<div dj-root dj-id="0">role={{ role }}</div>',
                },
            )
        )
        runtime, transport = _make_runtime(request)
        snap = _signed_snapshot(
            {"role": "admin", "n": 7}, slug=veto_path, session_key=session.session_key
        )
        await _mount(
            runtime, {"type": "mount", "view": veto_path, "url": PAGE_URL, "state_snapshot": snap}
        )
        view = runtime.view_instance
        assert view is not None
        assert view.role == "user", "_should_restore_snapshot=False must block restore"
        assert view._mounted_from_restore is False


# --------------------------------------------------------------------------- #
# (B) state_snapshot_signed EMIT — opt-in only.
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestRuntimeSnapshotEmit:
    @pytest.mark.asyncio
    async def test_opt_in_mount_emits_signed_snapshot(self):
        """An opt-in view's runtime mount frame carries ``state_snapshot_signed``
        — a blob that round-trips through ``unsign_snapshot`` for THIS view +
        session.

        Gate-off: a non-opt-in view (next test) gets NO blob.
        """
        from djust.security import unsign_snapshot

        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL})
        frame = transport.mount_frame
        assert frame is not None
        signed = frame.get("state_snapshot_signed")
        assert isinstance(signed, str) and signed, "opt-in mount must emit a signed snapshot"
        # The emitted blob must verify for THIS view + session (round-trip).
        inner = unsign_snapshot(signed, VIEW_PATH, session.session_key)
        assert inner is not None, "emitted blob must verify under the same slug+sid binding"

    @pytest.mark.asyncio
    async def test_default_view_emits_no_signed_snapshot(self):
        """A non-opt-in view's runtime mount frame carries NO snapshot (#1552)."""
        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(runtime, {"type": "mount", "view": DEFAULT_PATH, "url": PAGE_URL})
        frame = transport.mount_frame
        assert frame is not None
        assert "state_snapshot_signed" not in frame, "non-opt-in view must not ship state"

    @pytest.mark.asyncio
    async def test_emitted_blob_does_not_verify_for_other_session(self):
        """The emit binds the session key: a blob emitted under S1 does not
        verify under S2 (sid binding is live on the emit path too)."""
        from djust.security import unsign_snapshot

        session = await sync_to_async(_make_db_session)()
        other = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)
        await _mount(runtime, {"type": "mount", "view": VIEW_PATH, "url": PAGE_URL})
        signed = transport.mount_frame.get("state_snapshot_signed")
        assert signed
        assert unsign_snapshot(signed, VIEW_PATH, other.session_key) is None


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
