"""A live server session for tests that drive an explicit view without middleware.

An explicit view binds its state to a server session and re-reads that
session (and its user) on every turn (ADR-038; ``djust._exposure_auth``). A
test that builds its own socket scope or request, without
``SessionMiddleware`` and ``AuthenticationMiddleware``, therefore has to hand
the view what the middleware would have: a saved server session and a user.
Without one the mount is refused ("State binding requires bounded nonempty
identifiers") and every event is denied.

The session is a database session (Django's default engine), so the test
needs database access; an async socket test needs
``django_db(transaction=True)`` so the runtime's worker threads see the row.
A saved ``User`` is logged into the session, as ``django.contrib.auth.login``
would, so the per-turn re-read finds the same user that mounted.

Use one of:

* ``live_session(user=None)``: a new saved session;
* ``with_live_session(target, user=...)``: give a ``WebsocketCommunicator``,
  an ASGI scope dict or an ``HttpRequest`` a saved session and a user, keeping
  a server session it already has.
"""

from __future__ import annotations

from typing import Any

_UNSET: Any = object()


def _server_session_types() -> tuple[type, ...]:
    from djust._exposure_sessions import _SERVER_SESSION_TYPES

    return _SERVER_SESSION_TYPES


def live_session(user: Any = None, *, store: Any = None) -> Any:
    """A saved server session, logged in as ``user`` if it is a saved user.

    ``store`` is the session class: the database backend unless given.
    """
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

    if store is None:
        from django.contrib.sessions.backends.db import SessionStore as store

    session = store()
    if _is_saved_user(user):
        session[SESSION_KEY] = user._meta.pk.value_to_string(user)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    return session


def _is_saved_user(user: Any) -> bool:
    from django.contrib.auth.base_user import AbstractBaseUser

    return isinstance(user, AbstractBaseUser) and user.pk is not None


def _live_session_from_any_context(user: Any) -> Any:
    """``live_session(user)``, also from a coroutine (a socket test's setup).

    The ORM refuses to run on an event loop, so there the session is saved
    from a worker thread; the test then needs ``django_db(transaction=True)``.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return live_session(user)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(live_session, user).result()


def _has_live_session(session: Any) -> bool:
    return type(session) in _server_session_types() and bool(session.session_key)


def with_live_session(target: Any, user: Any = _UNSET) -> Any:
    """Give ``target`` a saved server session and a user; return ``target``.

    ``target`` is a ``WebsocketCommunicator`` (its ``scope``), an ASGI scope
    dict, or a request. A server session it already has is kept. ``user``
    replaces the target's user when given; otherwise an existing user is kept
    and an anonymous one is supplied.
    """
    from django.contrib.auth.models import AnonymousUser

    scope = getattr(target, "scope", None)
    if isinstance(target, dict):
        scope = target
    if scope is not None:
        current_user = scope.get("user")
        chosen = current_user if user is _UNSET else user
        if chosen is None:
            chosen = AnonymousUser()
        if not _has_live_session(scope.get("session")):
            scope["session"] = _live_session_from_any_context(chosen)
        scope["user"] = chosen
        return target
    current_user = getattr(target, "user", None)
    chosen = current_user if user is _UNSET else user
    if chosen is None:
        chosen = AnonymousUser()
    if not _has_live_session(getattr(target, "session", None)):
        target.session = _live_session_from_any_context(chosen)
    target.user = chosen
    return target
