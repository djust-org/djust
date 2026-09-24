"""Value-free signals for the opt-in service worker's caches (ADR-038 D-b, D-n).

The worker (``static/djust/service-worker.js``) keeps three caches on disk:
the state-snapshot cache, the VDOM (mount HTML) cache and the page-shell
cache. The server tells the client and worker three things, none of which
carries a value derived from view state or from the raw identity:

- **Eligibility (D-b).** Explicit-exposure pages are not written to the VDOM
  or shell cache. The HTTP response carries ``X-Djust-SW-Cache: no-store``
  (read by the worker before it writes the shell) and the mount frame carries
  ``"sw_cache": "no-store"`` (read by the client before ``cacheVdom``).
  Legacy pages get neither, so their caching is unchanged.
- **Identity (D-n).** Each mount frame carries ``sw_identity``, an HMAC
  digest of the session key and authenticated user id keyed on
  ``SECRET_KEY``. It is never the raw id or session key. The client clears
  all three caches when it differs from the stored marker, or when it
  disappears (logout).
- **Lifetime (D-n).** A mount frame that carries a signed snapshot also
  carries ``state_snapshot_max_age`` (``DJUST_STATE_SNAPSHOT_MAX_AGE``), so the
  worker can expire entries on read with the server's own lifetime.
"""

from __future__ import annotations

from typing import Any, Optional

from django.utils.crypto import salted_hmac

from .state_snapshot import get_max_age

# Response header the worker checks before writing the shell cache.
SW_CACHE_HEADER = "X-Djust-SW-Cache"
SW_CACHE_NO_STORE = "no-store"

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

    The digest is ``salted_hmac`` over both parts with the project's
    ``SECRET_KEY``, so it reveals neither the session key nor the user id and
    cannot be computed without the secret.
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


def mount_frame_metadata(view: Any, request: Any, snapshot_token: Any) -> dict[str, Any]:
    """Return the service-worker fields for one mount frame.

    ``sw_cache`` is present only for pages that are not eligible, so a legacy
    page's cacheability is unchanged. The identity and max age never carry a
    state value.
    """
    from .._exposure import service_worker_cache_eligible

    meta: dict[str, Any] = {}
    if not service_worker_cache_eligible(view):
        meta["sw_cache"] = SW_CACHE_NO_STORE
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None) if session is not None else None
    marker = identity_marker(session_key, getattr(request, "user", None))
    if marker is not None:
        meta["sw_identity"] = marker
    if isinstance(snapshot_token, str) and snapshot_token:
        meta["state_snapshot_max_age"] = get_max_age()
    return meta
