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


async def amount_on_runtime(
    runtime: Any,
    view: Any,
    *,
    user: Any = None,
    url: str = "/",
    view_path: str | None = None,
) -> Any:
    """Mount ``view`` on a hand-built ``ViewRuntime`` the way a transport does.

    An explicit view's turns need a runtime that owns it and a mount binding
    to a live server session (``_djust_mount_request``): a view installed with
    ``runtime.view_instance = view`` is refused ("Explicit turn has no mounted
    owner", or the event authorization denial). This runs the REAL
    ``dispatch_mount`` for ``view`` (only the view lookup is stubbed, so the
    test keeps its own instance), on a request that carries a saved session
    logged in as ``user`` (anonymous when ``None``).

    The transport is given what a live transport supplies: ``build_request``
    (the mount request, the SSE shape; it replaces the transport's own) and
    ``explicit_event_request`` (the per-event re-read of that session, as the
    socket does) unless it has one. The mount's frames are cleared, so the test sees only what its
    own turns send. Needs ``django_db(transaction=True)``. Returns the mount
    request.
    """
    from unittest.mock import MagicMock

    from asgiref.sync import sync_to_async
    from django.test import RequestFactory

    from djust._exposure_auth import fresh_socket_request

    request = RequestFactory().get(url)
    with_live_session(request, user)
    transport = runtime.transport
    transport.build_request = lambda: request
    if getattr(transport, "explicit_event_request", None) is None:

        async def explicit_event_request(owner: Any) -> Any:
            return await sync_to_async(fresh_socket_request)(owner)

        transport.explicit_event_request = explicit_event_request
    if view_path is None:
        cls = type(view)
        view_path = f"{cls.__module__}.{cls.__name__}"
    runtime._instantiate_view = MagicMock(return_value=view)
    await runtime.dispatch_mount({"type": "mount", "view": view_path, "url": url})
    sent = getattr(transport, "sent", None)
    if runtime.view_instance is not view:
        raise AssertionError(f"the runtime did not mount the view: {sent!r}")
    for frames in (sent, getattr(transport, "errors", None)):
        if isinstance(frames, list):
            frames.clear()
    return request


async def amount_on_consumer(
    consumer: Any,
    view_path: str,
    *,
    user: Any = None,
    url: str = "/",
    params: dict | None = None,
    stop_tick: bool = True,
    view: Any = None,
) -> Any:
    """Mount ``view_path`` on a hand-built ``LiveViewConsumer``, as a socket does.

    Tests that drive a consumer's internals (``server_push``, ``db_notify``,
    the tick, background work) without a socket used to install a view with
    ``consumer.view_instance = view``. An explicit view's server-originated
    turns are authorized by the runtime that mounted it, against the live
    session it was bound to at mount ("no runtime owns this view" otherwise).
    This gives the consumer a socket scope with a saved session logged in as
    ``user`` (anonymous when ``None``), the per-connection state ``connect()``
    creates, and runs the real ``handle_mount``. The mount's frames are
    cleared from ``consumer.sent`` when the test captures sends there, and the
    tick loop a ``tick_interval`` view starts is stopped (``stop_tick``).
    ``view``: mount this instance instead of a new one (only the view lookup
    is stubbed; ``view_path`` must still pass the allowlist). Needs
    ``django_db(transaction=True)``. Returns the mounted view.
    """
    from django.contrib.auth.models import AnonymousUser

    from djust.rate_limit import ConnectionRateLimiter

    scope = dict(getattr(consumer, "scope", None) or {})
    scope.setdefault("type", "websocket")
    scope.setdefault("path", "/ws/")
    scope.setdefault("query_string", b"")
    scope.setdefault("headers", [])
    scope["session"] = None
    scope["user"] = None
    with_live_session(scope, user if user is not None else AnonymousUser())
    consumer.scope = scope
    if not getattr(consumer, "session_id", None):
        import uuid

        consumer.session_id = str(uuid.uuid4())
    if getattr(consumer, "_rate_limiter", None) is None:
        consumer._rate_limiter = ConnectionRateLimiter(rate=1000, burst=1000)
    if not getattr(consumer, "channel_name", None):
        consumer.channel_name = "test.channel"
    if not hasattr(consumer, "channel_layer"):
        from channels.layers import get_channel_layer

        consumer.channel_layer = get_channel_layer()
    frame = {"type": "mount", "view": view_path, "url": url}
    if params:
        frame["params"] = params
    if view is not None:
        from unittest.mock import MagicMock

        consumer._get_runtime()._instantiate_view = MagicMock(return_value=view)
    await consumer.handle_mount(frame)
    sent = getattr(consumer, "sent", None)
    if consumer.view_instance is None:
        raise AssertionError(f"the consumer did not mount {view_path}: {sent!r}")
    task = getattr(consumer, "_tick_task", None)
    if task is not None and stop_tick:
        # A view with ``tick_interval`` starts its tick loop at mount. These
        # tests drive ``_tick_once`` themselves: a wall-clock loop beside
        # them is a race (#2124).
        import asyncio
        import contextlib

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        consumer._tick_task = None
    if isinstance(sent, list):
        sent.clear()
    return consumer.view_instance
