"""Value-free signals for the opt-in service worker's caches (#2948).

The worker (``static/djust/service-worker.js``) keeps three caches on disk:
the state-snapshot cache, the VDOM (mount HTML) cache and the page-shell
cache. Each mount frame tells the client two things, neither of which carries
a value derived from view state or from the raw identity:

- **Identity.** ``sw_identity`` is an HMAC digest of the session key and
  authenticated user id keyed on ``SECRET_KEY``. It is never the raw id or
  session key. The client clears all three caches when it differs from the
  stored marker, or when it disappears (logout), so one user's cached state is
  never offered to the next user of the same browser profile.
- **Lifetime.** A mount frame that carries a signed snapshot also carries
  ``state_snapshot_max_age`` (``DJUST_STATE_SNAPSHOT_MAX_AGE``), so the worker
  expires state entries on read with the server's own lifetime.

Backported from the 1.3 line (ADR-038 D-n).
"""

from __future__ import annotations

from typing import Any, Optional

from django.utils.crypto import salted_hmac

from .state_snapshot import get_max_age

# Namespaces the digest so it can never collide with another SECRET_KEY use.
SW_IDENTITY_SALT = "djust.sw_identity"

# 128 bits of the SHA-256 HMAC: enough to detect a change, and nothing more
# is needed; the marker is compared, never verified.
_MARKER_HEX_LENGTH = 32


def identity_marker(session_key: Optional[str], user: Any) -> Optional[str]:
    """Return a value-free marker of the session/user binding, or ``None``.

    The marker changes when the session key changes (Django cycles it on
    login and flushes it on logout) or when the authenticated user changes.
    It is ``None`` when there is neither a session key nor an authenticated
    user, which the client treats as "logged out".
    """
    session_part = session_key if isinstance(session_key, str) else ""
    user_part = ""
    try:
        if user is not None and getattr(user, "is_authenticated", False) is True:
            pk = getattr(user, "pk", None)
            if pk is not None:
                user_part = str(pk)
    except Exception:  # noqa: BLE001 — an unreadable user is treated as absent
        user_part = ""
    if not session_part and not user_part:
        return None
    # The separator cannot appear in a session key, so the parts never alias.
    digest: str = salted_hmac(
        SW_IDENTITY_SALT,
        session_part + "\x00" + user_part,
        algorithm="sha256",
    ).hexdigest()
    return digest[:_MARKER_HEX_LENGTH]


def mount_frame_metadata(request: Any, snapshot_token: Any) -> dict[str, Any]:
    """Return the service-worker fields for one mount frame."""
    meta: dict[str, Any] = {}
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None) if session is not None else None
    marker = identity_marker(session_key, getattr(request, "user", None))
    if marker is not None:
        meta["sw_identity"] = marker
    if isinstance(snapshot_token, str) and snapshot_token:
        meta["state_snapshot_max_age"] = get_max_age()
    return meta
