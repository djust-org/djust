"""Mount helpers for tests that drive djust's runtime or consumer internals.

The session helpers these build on are public: ``djust.testing.live_session``
and ``djust.testing.with_live_session`` (#4191). What stays here mounts a view
on a hand-built ``ViewRuntime`` or ``LiveViewConsumer``, which only the
framework's own tests do.
"""

from __future__ import annotations

from typing import Any

from djust.testing import with_live_session


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
    creates, and runs the real ``_mount_page``. The mount's frames are
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
    await consumer._mount_page(frame)
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
