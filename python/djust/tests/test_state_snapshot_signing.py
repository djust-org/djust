"""Security regression tests — signed state-snapshot envelope (CWE-345/CWE-915).

Finding #4: the opt-in state-snapshot restore (``LiveView.enable_state_snapshot
= True``) trusted an UNSIGNED, client-supplied snapshot on the
``live_redirect_mount`` back-navigation path. ``_restore_snapshot`` ``safe_setattr``s
every public key from the client's ``state_json``, so a client could FORGE an
arbitrary snapshot and inject arbitrary public state (``is_admin``,
``account_id``, …) — a state-injection / mass-assignment vulnerability.

Fix: the server signs the snapshot with ``TimestampSigner`` (keyed on
``SECRET_KEY``), the client echoes the OPAQUE signed blob verbatim, and the
server verifies signature + TTL + identity (slug + session) before restoring.
Unsigned/forged/tampered/expired/cross-context snapshots are rejected and the
view falls back to a normal ``mount()``.

Coverage:
- (a) forged/unsigned snapshot REJECTED at the real restore path (state stays
      at mount() default) — real ``WebsocketCommunicator`` round-trip.
- (b) a server-signed snapshot round-trips and restores — real
      ``WebsocketCommunicator`` round-trip.
- (c) a tampered signed blob (bit flip) → BadSignature → dropped.
- (d) an expired snapshot (max_age) → dropped.
- (e) cross-view replay rejected (slug binding).
- (f) cross-session replay rejected (session binding).
- (g) anonymous (no session key) signs/verifies consistently.
- (h) source-grep pin: the restore path calls ``unsign_snapshot`` and never
      trusts the raw ``state_json`` (gate-off anchor).
"""

from __future__ import annotations

import inspect
import json

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.security import sign_snapshot, unsign_snapshot


# ---------------------------------------------------------------------------
# Module-level opt-in view, resolvable by dotted path for the WS harness.
# ---------------------------------------------------------------------------


class _SignSnapView(LiveView):
    """Opt-in snapshot view. ``mount`` sets the safe default; a restore that
    survives the signature gate would overwrite ``role`` with the snapshot's
    value (the injection vector)."""

    enable_state_snapshot = True
    template = (
        '<div dj-view="djust.tests.test_state_snapshot_signing._SignSnapView" '
        'dj-id="0">role={{ role }} n={{ n }}</div>'
    )

    def mount(self, request, **kwargs):
        self.role = "user"  # safe default mount() always installs
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"role": self.role, "n": self.n}


_VIEW_SLUG = "djust.tests.test_state_snapshot_signing._SignSnapView"


# ---------------------------------------------------------------------------
# Real WebsocketCommunicator harness (reproduction fidelity — exercises the
# actual handle_live_redirect_mount → handle_mount restore path).
# ---------------------------------------------------------------------------


async def _connect(session_key):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    if session_key is not None:
        communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame
    return communicator


async def _receive_mount(communicator, *, tries=6, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == "mount":
            return last
    return last


async def _make_session_key():
    from django.contrib.sessions.backends.db import SessionStore

    def _create():
        s = SessionStore()
        s.create()
        return s.session_key

    return await sync_to_async(_create)()


# ---------------------------------------------------------------------------
# (a) Forged / unsigned snapshot is REJECTED at the real restore path.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_forged_unsigned_snapshot_is_rejected_real_ws_path():
    """An attacker echoes a plain (UNSIGNED) ``state_json`` forging
    ``role=admin``. With the fix, the restore path requires a valid signature,
    so the forged snapshot is dropped and mount() runs → ``role`` stays
    ``"user"``. Pre-fix this test FAILS (the forged role is injected)."""
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        session_key = await _make_session_key()
        communicator = await _connect(session_key)

        forged = {
            "view_slug": _VIEW_SLUG,
            # Legacy unsigned shape — exactly what an attacker would forge.
            "state_json": json.dumps({"role": "admin", "n": 999}),
            "ts": 0,
        }
        await communicator.send_json_to(
            {
                "type": "live_redirect_mount",
                "view": _VIEW_SLUG,
                "url": "/",
                "state_snapshot": forged,
            }
        )
        mount_frame = await _receive_mount(communicator)
        assert mount_frame is not None and mount_frame.get("type") == "mount"

        # The injection MUST NOT have happened: mount() reset role to "user".
        # Read the authoritative rendered HTML from the mount frame (this
        # exercises the real restore path end-to-end).
        html = mount_frame.get("html") or ""
        assert "role=admin" not in html, (
            f"Forged unsigned snapshot was applied — state injection! mount html={html!r}"
        )
        assert "role=user" in html, f"mount() default role expected; html={html!r}"
        assert "n=999" not in html, "Forged n was injected"

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# (b) A server-signed snapshot round-trips and restores.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_server_signed_snapshot_round_trips_and_restores():
    """A snapshot signed by the server (with the same slug + session) is
    accepted and its public state restored — proving the happy path still
    works after the fix."""
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        session_key = await _make_session_key()
        communicator = await _connect(session_key)

        state_json = json.dumps({"role": "user", "n": 42})
        signed = sign_snapshot(state_json, _VIEW_SLUG, session_key)

        await communicator.send_json_to(
            {
                "type": "live_redirect_mount",
                "view": _VIEW_SLUG,
                "url": "/",
                "state_snapshot": {
                    "view_slug": _VIEW_SLUG,
                    "state_json": signed,  # opaque signed blob, echoed verbatim
                    "ts": 0,
                },
            }
        )
        mount_frame = await _receive_mount(communicator)
        assert mount_frame is not None and mount_frame.get("type") == "mount"
        html = mount_frame.get("html") or ""
        assert "n=42" in html, (
            f"Signed snapshot must restore n=42 (not the mount() default 0); html={html!r}"
        )

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# (c)-(g) Unit-level coverage of the signing envelope itself.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tampered_signed_blob_is_rejected():
    """Flipping a byte in the signed blob → BadSignature → None (dropped)."""
    state_json = json.dumps({"role": "user"})
    signed = sign_snapshot(state_json, _VIEW_SLUG, "sess-1")
    # Flip a character in the body (not the trailing signature) — either way
    # the HMAC no longer matches.
    tampered = signed[:5] + ("X" if signed[5] != "X" else "Y") + signed[6:]
    assert unsign_snapshot(tampered, _VIEW_SLUG, "sess-1") is None


@pytest.mark.django_db
def test_expired_snapshot_is_rejected():
    """A snapshot older than max_age → SignatureExpired → None."""
    import time

    state_json = json.dumps({"role": "user"})
    signed = sign_snapshot(state_json, _VIEW_SLUG, "sess-1")
    time.sleep(1.1)
    # max_age=1 second → the 1.1s-old blob is expired.
    assert unsign_snapshot(signed, _VIEW_SLUG, "sess-1", max_age=1) is None
    # Sanity: with a generous TTL it still verifies.
    assert unsign_snapshot(signed, _VIEW_SLUG, "sess-1", max_age=3600) == state_json


@pytest.mark.django_db
def test_cross_view_replay_is_rejected():
    """A snapshot signed for view A cannot be replayed onto view B."""
    state_json = json.dumps({"role": "admin"})
    signed = sign_snapshot(state_json, "app.views.A", "sess-1")
    assert unsign_snapshot(signed, "app.views.B", "sess-1") is None
    # Same view → accepted.
    assert unsign_snapshot(signed, "app.views.A", "sess-1") == state_json


@pytest.mark.django_db
def test_cross_session_replay_is_rejected():
    """A snapshot captured under session S1 cannot be replayed onto S2."""
    state_json = json.dumps({"role": "admin"})
    signed = sign_snapshot(state_json, _VIEW_SLUG, "sess-1")
    assert unsign_snapshot(signed, _VIEW_SLUG, "sess-2") is None
    assert unsign_snapshot(signed, _VIEW_SLUG, "sess-1") == state_json


@pytest.mark.django_db
def test_anonymous_session_signs_and_verifies_consistently():
    """No session key → ``sid`` is "" on both sides; signature still binds."""
    state_json = json.dumps({"n": 1})
    signed = sign_snapshot(state_json, _VIEW_SLUG, None)
    # None and "" are treated identically.
    assert unsign_snapshot(signed, _VIEW_SLUG, None) == state_json
    assert unsign_snapshot(signed, _VIEW_SLUG, "") == state_json
    # An authenticated session must NOT be able to use an anonymous snapshot.
    assert unsign_snapshot(signed, _VIEW_SLUG, "sess-1") is None


@pytest.mark.django_db
def test_unsigned_plain_json_is_rejected():
    """The legacy plain ``state_json`` (no signature) must be rejected — there
    is no bypass for unsigned input."""
    plain = json.dumps({"role": "admin"})
    assert unsign_snapshot(plain, _VIEW_SLUG, "sess-1") is None
    assert unsign_snapshot("", _VIEW_SLUG, "sess-1") is None
    assert unsign_snapshot(None, _VIEW_SLUG, "sess-1") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (h) Source-grep pin — restore path verifies the signature (gate-off anchor).
# ---------------------------------------------------------------------------


def test_restore_path_calls_unsign_and_does_not_trust_raw_state_json():
    """Pin that ``handle_mount`` runs the inbound snapshot through
    ``unsign_snapshot`` before restoring. The gate-off self-test reverts this
    call; test (a) then fails (forged role injected)."""
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_mount)
    assert "unsign_snapshot" in source, (
        "handle_mount restore path must verify the signed snapshot via "
        "unsign_snapshot before applying any state."
    )


def test_emit_path_signs_snapshot():
    """Pin that the mount-frame emit signs the public state."""
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_mount)
    assert "sign_snapshot" in source, (
        "Emit path must sign the public_state snapshot before sending to client."
    )
    assert "state_snapshot_signed" in source, (
        "Emit path must send the signed blob under 'state_snapshot_signed'."
    )
