"""A state save that can outlive its request re-checks its session first (#3247).

Since #3212 a save made inside a request's executors runs on the dedicated save
pool (``runtime._spawn_save``), so the request ends when its turn does and the
save may still be running afterwards. The save writes through the session
object its turn captured. A logout in ANOTHER request flushes the stored
session but leaves that object untouched, so nothing in the save noticed.

What stops such a write without this check is the backend's own no-recreate
guard: ``db``, ``cached_db`` and ``file`` refuse to update a session row that
no longer exists, and ``cache`` looks the key up before setting it.

Here, right before the write, a pool save looks its session key up once in
the store and is dropped (a debug line, no write) when:

- the key no longer exists (logged out, flushed, rotated away by a login in
  another request, or expired); or
- the stored session names a different authenticated user than the one the
  save's session object holds.

An in-object key rotation stays a legitimate save. ``login()`` in a legacy
handler calls ``cycle_key()`` on the very session object the save writes, so
the object carries the new key and the new user while the store still holds
the pre-login copy that ``cycle_key()`` created (no user, or the same one).
Only a stored user that DIFFERS from the object's is a mismatch; a stored
anonymous session is not. Without that the login itself would never be
saved, because nothing else writes that object again.

The check narrows the window; it cannot close it. A logout that lands between
the lookup and the write still races the write, as it would for any concurrent
Django request. ``db`` refuses that write by itself; ``cache`` checks and then
sets without a lock, so a flush in that gap is overwritten.

Saves off the pool (a WebSocket session's thread, a ``PooledHTTP`` slot) are
not checked: their turn is still waiting for them.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_state = threading.local()


class LateSaveDropped(Exception):
    """The session a pool save would write was logged out or replaced."""


def detached(run: Callable[[], None]) -> Callable[[], None]:
    """Wrap a pool save job so the writes it makes run :func:`check_session`."""

    def job() -> None:
        _state.active = True
        try:
            run()
        finally:
            _state.active = False

    return job


def _auth_user(data: Any) -> Optional[str]:
    from django.contrib.auth import SESSION_KEY

    value = data.get(SESSION_KEY) if hasattr(data, "get") else None
    return None if value is None else str(value)


def check_session(session: Any, expected_key: Optional[str]) -> None:
    """Raise :class:`LateSaveDropped` when a pool save's session is gone.

    ``expected_key`` is the key the save body started with, read before the
    body touched the session: a lazy load of a deleted session resets the
    object's key to ``None``, and ``save()`` would then create a new session.
    ``None`` means the session was never persisted, so there is no stored
    session another request could have logged out: the save may create it.

    One store lookup, only on the save pool.
    """
    if not getattr(_state, "active", False) or not expected_key or session is None:
        return
    backend = type(session)
    if backend.__module__.endswith("signed_cookies"):
        return  # the browser holds the whole session; the server has nothing to flush
    fresh = backend(session_key=expected_key)
    stored = fresh.load()
    if fresh.session_key is None:
        _drop("its session no longer exists")
    stored_user = _auth_user(stored)
    if stored_user is not None and stored_user != _auth_user(session):
        _drop("its session now belongs to another user")


def _drop(reason: str) -> None:
    # Value-free: no session key, no user id.
    logger.debug("Late state save dropped: %s", reason)
    raise LateSaveDropped(reason)
