"""Several asyncio event loops in one process (#3128). Opt-in.

On free-threaded CPython (3.14t) with ``LIVEVIEW_CONFIG["worker_threads"]``,
one djust process spreads its sync work over several cores, and the single
asyncio event loop that decodes, dispatches and encodes every WebSocket frame
becomes the limit (about 0.93 of a core at 160–192 snake-arena players).
:func:`serve` runs N uvicorn servers, each on its own event loop in its own
thread, all accepting on ONE listening socket, so the loop work spreads too
while in-process state (rooms, caches, the in-memory channel layer) stays
shared::

    djust serve myproject.asgi:application --loops 4 --ws websockets

or programmatically::

    from djust.multiloop import serve

    serve("myproject.asgi:application", loops=4, host="0.0.0.0", port=8000,
          ws="websockets")

``loops=1`` (the default) is ``uvicorn.run`` unchanged. With more loops:

* **Free-threaded only.** On a GIL build, or when an extension re-enabled the
  GIL, extra loops only contend for the GIL, so :func:`serve` refuses
  (``allow_gil=True`` overrides it, for tests).
* **A loop-safe channel layer.** Channels' in-memory layer and djust's
  ``InMemoryChannelLayer`` keep each channel in an ``asyncio.Queue`` owned by
  one loop, so :func:`serve` refuses them; use
  ``djust.layers.MultiLoopInMemoryChannelLayer``. ``channels_redis``' core
  layer shares one receive lock between loops and is refused too; its
  ``RedisPubSubChannelLayer`` keeps a connection per loop. Other layers get a
  warning.
* **Each connection stays on the loop that accepted it.** Every loop polls the
  shared socket and the kernel hands a new connection to whichever accepts
  first, so a busy loop takes fewer.
* **Lifespan runs once per loop**, as it runs once per worker with
  ``uvicorn --workers``: whatever the app creates at startup is per loop.
* **Signals.** The main thread supervises: SIGINT or SIGTERM asks every server
  to shut down gracefully, a second one forces it. If one server stops (for
  example its lifespan startup failed), the others stop too.
* ``limit_concurrency`` and ``limit_max_requests`` count per loop; reaching
  ``limit_max_requests`` on one loop stops the process.

The rules app code must follow are in the scaling guide ("More than one event
loop"): no asyncio object (lock, queue, event, future, task) may be shared by
sessions, because two sessions can be on different loops; shared state takes a
``threading.Lock``; room-wide background work is one task per room, started
under a ``threading.Lock``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys
import sysconfig
import threading
from typing import Any, Coroutine, List, Optional, TypeVar

logger = logging.getLogger(__name__)

__all__ = [
    "MultiLoopError",
    "free_threaded",
    "is_multi_loop",
    "loop_count",
    "run_on_loop",
    "serve",
]

T = TypeVar("T")

#: Exit code when a server failed to start (uvicorn's ``STARTUP_FAILURE``).
STARTUP_FAILURE = 3

# Set by serve() before it starts the loops; read by the code that must hop to
# another loop (SSE sessions, the db_notify listener). 0 = not launched here.
_loop_count = 0

#: Layers that keep per-channel state bound to one event loop.
_LOOP_UNSAFE_LAYERS = (
    "channels.layers.InMemoryChannelLayer",
    "djust.layers.InMemoryChannelLayer",
    "channels_redis.core.RedisChannelLayer",
)


class MultiLoopError(RuntimeError):
    """Several event loops were asked for where they are unsafe or pointless."""


def loop_count() -> int:
    """How many event loops :func:`serve` runs in this process (0 if it did
    not start the server)."""
    return _loop_count


def is_multi_loop() -> bool:
    """Whether this process serves on more than one event loop."""
    return _loop_count > 1


def free_threaded() -> bool:
    """Whether this is a free-threaded build with the GIL currently off."""
    if not sysconfig.get_config_var("Py_GIL_DISABLED"):
        return False
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    return is_enabled is not None and not is_enabled()


async def run_on_loop(loop: Optional[asyncio.AbstractEventLoop], coro: Coroutine[Any, Any, T]) -> T:
    """Await ``coro`` on ``loop``, the loop that owns the state it touches.

    Used where one piece of state outlives a request and a later request can
    arrive on another loop (an SSE session's event POST, the db_notify
    listener). Runs ``coro`` right here when the process has one loop, when
    ``loop`` is ``None``, the running loop, closed or not running. Otherwise it
    is scheduled on ``loop`` with the caller's context variables and awaited;
    cancelling the caller cancels it there.
    """
    if loop is None or not is_multi_loop():
        return await coro
    try:
        here = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - always called from a coroutine
        here = None
    if loop is here or loop.is_closed() or not loop.is_running():
        return await coro
    # run_coroutine_threadsafe copies the caller's context into the task.
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return await asyncio.wrap_future(future)


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------


def _layer_class_path(backend: Any) -> str:
    cls = type(backend)
    return f"{cls.__module__}.{cls.__qualname__}"


def check_channel_layers() -> List[str]:
    """Create every configured channel layer; return the loop-unsafe ones.

    Creating them here, before the loops start, also avoids two loops racing
    to create the same alias on first use and ending up with two layers.
    Unknown layers are logged as a warning and not returned.
    """
    try:
        from django.conf import settings

        if not settings.configured:
            return []
        aliases = list(getattr(settings, "CHANNEL_LAYERS", None) or {})
    except ImportError:  # pragma: no cover - Django is a dependency
        return []
    if not aliases:
        return []
    from channels.layers import channel_layers

    unsafe = []
    for alias in aliases:
        backend = channel_layers[alias]
        path = _layer_class_path(backend)
        if getattr(backend, "multi_loop_safe", False):
            continue
        if path in _LOOP_UNSAFE_LAYERS:
            unsafe.append(f"CHANNEL_LAYERS[{alias!r}] = {path}")
        elif path == "channels_redis.pubsub.RedisPubSubChannelLayer":
            continue  # keeps one connection per event loop
        else:
            logger.warning(
                "djust serve: channel layer %r (%s) is not known to be safe with several "
                "event loops; a layer whose state is bound to one asyncio loop will fail "
                "or lose messages",
                alias,
                path,
            )
    return unsafe


def _check_pool(loops: int) -> None:
    try:
        from django.conf import settings

        if not settings.configured:
            return
        from .worker_pool import configured_pool_size

        size = configured_pool_size()
    except Exception:  # noqa: BLE001 - advisory only; never block startup
        return
    if size <= 0:
        logger.warning(
            "djust serve: %d event loops but LIVEVIEW_CONFIG['worker_threads'] is off, so "
            "every session's sync work still runs on one shared thread; set worker_threads",
            loops,
        )
    else:
        logger.info("djust serve: %d event loops, worker pool of %d threads", loops, size)


def _refuse_gil(loops: int, allow_gil: bool, when: str) -> None:
    if free_threaded():
        return
    message = (
        f"djust serve --loops {loops}: the GIL is enabled {when}, so several event loops "
        "only take turns holding it and add thread switches. Run on free-threaded "
        "CPython (python3.14t) with no extension re-enabling the GIL, or use one loop."
    )
    if not allow_gil:
        raise MultiLoopError(message)
    logger.warning("%s (continuing: allow_gil=True)", message)


# ---------------------------------------------------------------------------
# The launcher
# ---------------------------------------------------------------------------


class _Runner:
    """Runs one uvicorn server on its own loop, in its own thread."""

    def __init__(self, index: int, server: Any, sock: socket.socket) -> None:
        self.index = index
        self.server = server
        self.sock = sock
        self.error: Optional[BaseException] = None
        self.thread = threading.Thread(target=self._run, name=f"djust-loop-{index}", daemon=True)

    def _run(self) -> None:
        logger.debug(
            "djust serve: loop %d on thread %s (native id %s)",
            self.index,
            threading.current_thread().name,
            threading.get_native_id(),
        )
        try:
            self.server.run(sockets=[self.sock])
        except BaseException as exc:  # noqa: BLE001 - reported by the supervisor
            self.error = exc
            if not isinstance(exc, SystemExit):
                logger.exception("djust serve: event loop %d failed", self.index)
        finally:
            try:
                self.sock.close()
            except OSError:  # pragma: no cover - already closed by uvicorn
                pass


def _signal_all(runners: List[_Runner], force: bool) -> None:
    for runner in runners:
        runner.server.should_exit = True
        if force:
            runner.server.force_exit = True


def serve(app: Any, *, loops: int = 1, allow_gil: bool = False, **uvicorn_kwargs: Any) -> int:
    """Serve ``app`` with uvicorn on ``loops`` event loops in this process.

    ``app`` and ``uvicorn_kwargs`` are what ``uvicorn.run`` takes (``host``,
    ``port``, ``uds``, ``ws``, ``http``, ``loop``, ``lifespan``, ``log_level``,
    ``proxy_headers``, ``timeout_graceful_shutdown`` and so on). ``loops=1``
    calls ``uvicorn.run`` unchanged. ``reload`` and ``workers`` are refused
    with more than one loop.

    Returns the process exit code (0, or 3 when a server failed to start).
    Must be called from the main thread, which handles SIGINT and SIGTERM.

    Raises :class:`MultiLoopError` for several loops on a GIL build (unless
    ``allow_gil``) or with a loop-unsafe channel layer.
    """
    global _loop_count
    try:
        loops = int(loops)
    except (TypeError, ValueError):
        raise ValueError(f"loops must be an integer >= 1, got {loops!r}") from None
    if loops < 1:
        raise ValueError(f"loops must be an integer >= 1, got {loops!r}")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise MultiLoopError("djust serve needs uvicorn: pip install uvicorn") from exc

    if loops == 1:
        uvicorn.run(app, **uvicorn_kwargs)
        return 0

    if uvicorn_kwargs.get("reload"):
        raise MultiLoopError("djust serve --loops N does not support reload; use one loop")
    if (uvicorn_kwargs.get("workers") or 1) != 1:
        raise MultiLoopError(
            "djust serve --loops N runs one process; combine processes with a process "
            "manager instead of workers"
        )
    if threading.current_thread() is not threading.main_thread():
        raise MultiLoopError("djust serve with several loops must run on the main thread")
    _refuse_gil(loops, allow_gil, "on this Python build")

    config = uvicorn.Config(app, **uvicorn_kwargs)
    config.load()  # imports the app (and sets Django up) once, on this thread
    _refuse_gil(loops, allow_gil, "after importing the app")
    unsafe = check_channel_layers()
    if unsafe:
        raise MultiLoopError(
            "djust serve --loops %d: these channel layers keep state bound to one event "
            "loop: %s. Use djust.layers.MultiLoopInMemoryChannelLayer for one process "
            "(or channels_redis.pubsub.RedisPubSubChannelLayer)." % (loops, "; ".join(unsafe))
        )
    _check_pool(loops)

    sock = config.bind_socket()
    sock.listen(config.backlog)
    _loop_count = loops
    # Each server gets its own descriptor for the one listening socket: uvicorn
    # closes the sockets it was given when it shuts down.
    runners = [_Runner(i, uvicorn.Server(config), sock.dup()) for i in range(loops)]

    signals = [signal.SIGINT, signal.SIGTERM]
    received: List[int] = []

    def on_signal(signum: int, frame: Any) -> None:
        received.append(signum)
        _signal_all(runners, force=len(received) > 1)

    previous = {sig: signal.signal(sig, on_signal) for sig in signals}
    logger.info("djust serve: starting %d event loops on one socket", loops)
    try:
        for runner in runners:
            runner.thread.start()
        stopping = False
        while any(r.thread.is_alive() for r in runners):
            for runner in runners:
                runner.thread.join(timeout=0.1)
                if not runner.thread.is_alive() and not stopping:
                    # One server stopped (signal, startup failure, max requests):
                    # stop the rest too.
                    stopping = True
                    _signal_all(runners, force=False)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        sock.close()
        _loop_count = 0

    # A server that never started because a signal stopped it first is not a
    # failure; one that errored, or stopped on its own before starting, is.
    failed = [
        r.index for r in runners if r.error is not None or (not r.server.started and not received)
    ]
    if failed:
        logger.error("djust serve: event loop(s) %s failed to start or crashed", failed)
        return STARTUP_FAILURE
    return 0
