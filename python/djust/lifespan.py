"""Answer the ASGI ``lifespan`` protocol for a djust application.

Django's ASGI handler refuses a ``lifespan`` scope and channels'
``ProtocolTypeRouter`` raises for a scope type it has no entry for, so a
project's ``asgi.py`` has no supported place to start services that are bound
to the server's event loop (an async database engine, a LISTEN connection, a
background pump) and to stop them on shutdown. Wrap the application::

    # asgi.py
    from djust.lifespan import with_lifespan

    application = with_lifespan(
        ProtocolTypeRouter({...}),
        startup=[backend.start],
        shutdown=[backend.stop],
    )

or register hooks from anywhere that runs before the server starts (an
``AppConfig.ready()``, the settings-adjacent module that builds a service)::

    from djust.lifespan import on_shutdown, on_startup

    @on_startup
    async def start_backend():
        await backend.start()

The wrapper answers ``lifespan`` itself and passes every other scope to the
wrapped application. Hooks run in order on startup (explicit ones, then
registered ones) and in reverse on shutdown. An ``async def`` hook runs on the
server's event loop; a sync hook runs through ``sync_to_async``, so it may use
the ORM. A startup hook that raises reports ``lifespan.startup.failed`` (the
server does not start); every shutdown hook runs even when one raises, and a
failure is reported as ``lifespan.shutdown.failed``.

Run the server with lifespan on (uvicorn's default ``--lifespan auto`` does,
once the application answers it).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Union

from ._exposure_diagnostics import exception_details_allowed_for, log_failure_for
from ._view_hooks import call_hook

logger = logging.getLogger(__name__)

Hook = Callable[[], Union[None, Awaitable[None]]]
ASGIApp = Callable[..., Awaitable[None]]

_startup_hooks: List[Hook] = []
_shutdown_hooks: List[Hook] = []


def on_startup(fn: Hook) -> Hook:
    """Register ``fn`` to run when the server starts (usable as a decorator)."""
    _startup_hooks.append(fn)
    return fn


def on_shutdown(fn: Hook) -> Hook:
    """Register ``fn`` to run when the server stops (usable as a decorator)."""
    _shutdown_hooks.append(fn)
    return fn


def _reset_registry_for_tests() -> None:
    _startup_hooks.clear()
    _shutdown_hooks.clear()


def _hook_name(fn: Any) -> str:
    return getattr(fn, "__qualname__", None) or type(fn).__name__


def _failure_message(hook: Any, exc: BaseException) -> str:
    """What the server prints for a failed hook: the hook and the error, with
    its text only where diagnostics are allowed (ADR-038)."""
    if exception_details_allowed_for(()):
        return "%s: %s: %s" % (_hook_name(hook), type(exc).__name__, exc)
    return "%s: %s" % (_hook_name(hook), type(exc).__name__)


class LifespanApp:
    """An ASGI application that answers ``lifespan`` and delegates the rest.

    Built by :func:`with_lifespan`. Registered hooks are read when the server
    starts, so hooks registered after the application is built still run.
    """

    def __init__(
        self,
        app: ASGIApp,
        startup: Iterable[Hook] = (),
        shutdown: Iterable[Hook] = (),
    ) -> None:
        self.app = app
        self.startup = list(startup)
        self.shutdown = list(shutdown)

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "lifespan":
            await self.app(scope, receive, send)
            return
        await self._lifespan(receive, send)

    async def _lifespan(self, receive: Callable, send: Callable) -> None:
        while True:
            message = await receive()
            kind = message.get("type")
            if kind == "lifespan.startup":
                error = await self._run_startup()
                if error is not None:
                    await send({"type": "lifespan.startup.failed", "message": error})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif kind == "lifespan.shutdown":
                error = await self._run_shutdown()
                if error is not None:
                    await send({"type": "lifespan.shutdown.failed", "message": error})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                return

    async def _run_startup(self) -> Optional[str]:
        for hook in [*self.startup, *_startup_hooks]:
            try:
                await call_hook(hook)
            except Exception as exc:  # noqa: BLE001 - reported to the server, which stops
                log_failure_for(
                    logger,
                    (),
                    exc,
                    "lifespan startup hook %s failed",
                    _hook_name(hook),
                    traceback=True,
                )
                return _failure_message(hook, exc)
        return None

    async def _run_shutdown(self) -> Optional[str]:
        failed: Optional[str] = None
        for hook in reversed([*self.shutdown, *_shutdown_hooks]):
            try:
                await call_hook(hook)
            except Exception as exc:  # noqa: BLE001 - the remaining hooks still run
                log_failure_for(
                    logger,
                    (),
                    exc,
                    "lifespan shutdown hook %s failed",
                    _hook_name(hook),
                    traceback=True,
                )
                if failed is None:
                    failed = _failure_message(hook, exc)
        return failed


def with_lifespan(
    app: ASGIApp,
    *,
    startup: Iterable[Hook] = (),
    shutdown: Iterable[Hook] = (),
) -> LifespanApp:
    """Wrap ``app`` so it answers ASGI ``lifespan`` and runs the hooks."""
    return LifespanApp(app, startup=startup, shutdown=shutdown)


__all__ = ["LifespanApp", "on_shutdown", "on_startup", "with_lifespan"]
