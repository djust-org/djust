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

A lookup that FAILS (a Redis or memcached blip, a database error) is not a
logout: Django's ``cache`` backend swallows such errors in ``load()`` and
reports the session as missing, so the lookup reads the store directly and
lets the error surface. The save then goes ahead as before, and the error is
logged as a warning with its traceback. Only a definite "session gone" or
"another user" drops a save.

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


class _LookupFailed(Exception):
    """The store could not say whether the session exists."""


def _stored_session(backend: type, key: str) -> Optional[Any]:
    """The stored session data for ``key``; ``None`` only when it is definitely gone.

    Raises on a storage error instead of reporting it as a missing session,
    as ``load()`` does for the ``cache`` and ``file`` backends. One lookup for
    the stock ``db``, ``cache`` and ``cached_db`` backends.
    """
    from django.contrib.sessions.backends import cache as cache_backend
    from django.contrib.sessions.backends import cached_db
    from django.contrib.sessions.backends import db as db_backend

    fresh = backend(session_key=key)
    if issubclass(backend, cached_db.SessionStore):
        try:
            data = fresh._cache.get(fresh.cache_key)
        except Exception:  # noqa: BLE001 — the database is the authority; cached_db does the same
            data = None
        if data is not None:
            return data
        row = fresh._get_session_from_db()  # a database error propagates
        return None if row is None else fresh.decode(row.session_data)
    if issubclass(backend, cache_backend.SessionStore):
        return fresh._cache.get(fresh.cache_key)  # a cache error propagates
    if issubclass(backend, db_backend.SessionStore):
        row = fresh._get_session_from_db()  # a database error propagates
        return None if row is None else fresh.decode(row.session_data)
    # ``file`` and custom backends: ``exists()`` answers presence, and a
    # ``load()`` that then loses the key could not read what exists.
    if not fresh.exists(key):
        return None
    data = fresh.load()
    if fresh.session_key is None:
        raise _LookupFailed("the session exists but could not be read")
    return data


def check_session(session: Any, expected_key: Optional[str]) -> None:
    """Raise :class:`LateSaveDropped` when a pool save's session is gone.

    ``expected_key`` is the key the save body started with, read before the
    body touched the session: a lazy load of a deleted session resets the
    object's key to ``None``, and ``save()`` would then create a new session.
    ``None`` means the session was never persisted before this save, or the
    save's own handler flushed it (``logout()`` on the object the save writes):
    either way there is no stored session another request could have logged
    out, so the check does not apply. (After an in-handler ``logout()`` the
    save creates a new, cookie-less session row holding the logged-out page's
    state, which expires unread; that predates this check.)

    One store lookup, only on the save pool. A lookup error lets the save go
    ahead and is logged as a warning.
    """
    if not getattr(_state, "active", False) or not expected_key or session is None:
        return
    from django.contrib.sessions.backends import signed_cookies

    backend = type(session)
    if issubclass(backend, signed_cookies.SessionStore):
        return  # the browser holds the whole session; the server has nothing to flush
    try:
        stored = _stored_session(backend, expected_key)
    except Exception as exc:  # noqa: BLE001 — an unreadable store is not a logout
        from ._exposure_diagnostics import log_failure

        # A storage error can carry session values: value-free where the
        # turn's diagnostics are restricted, as every other save failure.
        log_failure(
            logger,
            exc,
            "Late state save: the session lookup failed; saving anyway",
            level="warning",
            traceback=True,
        )
        return
    if stored is None:
        _drop("its session no longer exists")
    stored_user = _auth_user(stored)
    if stored_user is not None and stored_user != _auth_user(session):
        _drop("its session now belongs to another user")


def _drop(reason: str) -> None:
    # Value-free: no session key, no user id.
    logger.debug("Late state save dropped: %s", reason)
    raise LateSaveDropped(reason)
