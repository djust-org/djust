"""
Signed state-snapshot envelope for the v0.6.0 back-navigation restore path.

Security background (CWE-345 → CWE-915 / CWE-639)
-------------------------------------------------
The opt-in state-snapshot feature (``LiveView.enable_state_snapshot = True``)
restores a view's *public* state from a client-supplied payload on the
``live_redirect_mount`` back-navigation path, in lieu of calling ``mount()``.
The restore (``LiveView._restore_snapshot``) ``safe_setattr``s every public
key from that payload onto the live view instance.

Originally the snapshot was embedded in the page and round-tripped through the
client UNSIGNED: the server sent the public state to the client, the client
re-serialized it and echoed it back, and the server trusted it. Because the
payload carried no authenticity proof, a client could FORGE an arbitrary
snapshot (e.g. ``{"is_admin": true, "account_id": 7}``) and inject arbitrary
public state — a mass-assignment / state-injection vulnerability gated only by
``safe_setattr``'s attribute-name regex (which permits any ordinary public
attribute name).

Fix: the server now signs the snapshot with Django's
:class:`~django.core.signing.TimestampSigner` (keyed on ``SECRET_KEY``). The
client stores the OPAQUE signed blob and echoes it back verbatim. On restore,
the server verifies the HMAC signature, the TTL, and the identity binding
(view slug + session) before applying any state. An unsigned, forged, tampered,
expired, or cross-view/cross-session snapshot is rejected and the view falls
back to a normal ``mount()``.

The signed payload binds three things so a valid signature cannot be replayed
out of context:

- ``slug`` — the view path the snapshot was captured for. A snapshot signed
  for view A cannot be replayed onto view B.
- ``sid`` — the Django session key at capture time (empty string for an
  anonymous session). A snapshot captured under session S1 cannot be replayed
  onto session S2. When there is no session key on either side (anonymous),
  ``sid`` is ``""`` on both, so the binding degrades to slug-only — but the
  HMAC signature still closes forgery for anonymous users too.
- ``state`` — the serialized public-state JSON itself.

Both halves (emit + restore) go through this single module so the envelope
shape can never drift between the signer and the verifier.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from django.conf import settings
from django.core import signing

from .log_sanitizer import sanitize_for_log

logger = logging.getLogger(__name__)

# Salt namespaces the signer so a djust state-snapshot signature can never be
# confused with any other ``SECRET_KEY``-derived signature in the project
# (sessions, password reset, messages, other TimestampSigner uses).
SNAPSHOT_SALT = "djust.state_snapshot"

# Default TTL for a signed snapshot (seconds). Operators may override via the
# ``DJUST_STATE_SNAPSHOT_MAX_AGE`` setting. One hour is a generous upper bound
# for a back-navigation round-trip; stale snapshots beyond it are rejected.
DEFAULT_MAX_AGE = 3600


def get_max_age() -> int:
    """Return the configured snapshot TTL in seconds.

    Reads ``DJUST_STATE_SNAPSHOT_MAX_AGE`` from Django settings, falling back
    to :data:`DEFAULT_MAX_AGE`. An invalid/non-int value falls back to the
    default rather than raising — a misconfiguration must not break mount.
    """
    raw = getattr(settings, "DJUST_STATE_SNAPSHOT_MAX_AGE", DEFAULT_MAX_AGE)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "DJUST_STATE_SNAPSHOT_MAX_AGE is not an int (%r); using default %d",
            raw,
            DEFAULT_MAX_AGE,
        )
        return DEFAULT_MAX_AGE
    if value <= 0:
        logger.warning(
            "DJUST_STATE_SNAPSHOT_MAX_AGE must be positive (got %d); using default %d",
            value,
            DEFAULT_MAX_AGE,
        )
        return DEFAULT_MAX_AGE
    return value


def _signer() -> signing.TimestampSigner:
    # TimestampSigner is keyed on SECRET_KEY by default; the salt namespaces it.
    return signing.TimestampSigner(salt=SNAPSHOT_SALT)


def sign_snapshot(state_json: str, view_slug: str, session_key: Optional[str]) -> str:
    """Sign a serialized public-state snapshot, binding it to slug + session.

    Args:
        state_json: The ``json.dumps`` string of the view's public state
            (already serialized by ``_capture_snapshot_state`` + ``json.dumps``).
        view_slug: The dotted view path the snapshot was captured for.
        session_key: The Django session key, or ``None``/empty for anonymous.

    Returns:
        An opaque signed string the client stores verbatim and echoes back.
        The string contains the slug/session binding and a timestamp; it is
        NOT meant to be parsed client-side.
    """
    envelope = json.dumps(
        {
            "slug": view_slug,
            "sid": session_key or "",
            "state": state_json,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _signer().sign(envelope)


def unsign_snapshot(
    signed: str,
    view_slug: str,
    session_key: Optional[str],
    max_age: Optional[int] = None,
) -> Optional[str]:
    """Verify a signed snapshot and return its inner ``state_json`` string.

    Returns ``None`` (and logs at WARNING/INFO) on ANY failure: a bad or
    missing signature, an expired snapshot, a tampered/garbage blob, a
    non-string input, a slug mismatch (cross-view replay), or a session
    mismatch (cross-session replay). A ``None`` return means the caller MUST
    drop the snapshot and fall back to a normal ``mount()``.

    This function is the ONLY place a snapshot becomes trusted. Unsigned input
    (the legacy plain ``state_json`` an attacker might send) fails the
    signature check and is rejected here — there is no bypass path.

    Args:
        signed: The opaque signed blob echoed back by the client.
        view_slug: The view path being mounted (must match the signed slug).
        session_key: The current Django session key (must match signed sid).
        max_age: TTL override in seconds; defaults to :func:`get_max_age`.

    Returns:
        The verified inner ``state_json`` string, or ``None`` if rejected.
    """
    if not isinstance(signed, str) or not signed:
        logger.info("state_snapshot: missing or non-string signed blob; rejecting")
        return None

    ttl = max_age if max_age is not None else get_max_age()

    try:
        envelope_str = _signer().unsign(signed, max_age=ttl)
    except signing.SignatureExpired:
        logger.info(
            "state_snapshot: signed blob expired (max_age=%ds) for %s; rejecting",
            ttl,
            sanitize_for_log(view_slug),
        )
        return None
    except signing.BadSignature:
        # Covers forged/unsigned input and any bit-flip tamper.
        logger.warning(
            "state_snapshot: bad signature for %s; rejecting (forged or tampered)",
            sanitize_for_log(view_slug),
        )
        return None

    try:
        envelope = json.loads(envelope_str)
    except (ValueError, TypeError):
        logger.warning(
            "state_snapshot: signed envelope is not valid JSON for %s; rejecting",
            sanitize_for_log(view_slug),
        )
        return None

    if not isinstance(envelope, dict):
        logger.warning(
            "state_snapshot: signed envelope is not a dict for %s; rejecting",
            sanitize_for_log(view_slug),
        )
        return None

    # Identity binding — reject cross-view replay.
    if envelope.get("slug") != view_slug:
        logger.warning(
            "state_snapshot: slug mismatch (signed for %s, mounting %s); "
            "rejecting cross-view replay",
            sanitize_for_log(str(envelope.get("slug"))),
            sanitize_for_log(view_slug),
        )
        return None

    # Identity binding — reject cross-session replay. Anonymous (no session
    # key) is "" on both sides, so it stays consistent.
    if envelope.get("sid", "") != (session_key or ""):
        logger.warning(
            "state_snapshot: session mismatch for %s; rejecting cross-session replay",
            sanitize_for_log(view_slug),
        )
        return None

    state_json = envelope.get("state")
    if not isinstance(state_json, str):
        logger.warning(
            "state_snapshot: signed envelope 'state' is not a string for %s; rejecting",
            sanitize_for_log(view_slug),
        )
        return None

    return state_json
