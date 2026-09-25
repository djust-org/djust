"""Pinned session worker pool for the WebSocket path (#3074).

By default djust runs a WebSocket session's sync work — mount, event handlers,
hooks, renders — through asgiref's ``sync_to_async`` with
``thread_sensitive=True``. Outside a per-request context that means ONE
thread shared by every session in the process, so at most one core does
LiveView work however many sessions are connected.

``LIVEVIEW_CONFIG["worker_threads"]`` opts in to a pool of K threads instead:

* ``None`` / ``False`` / ``0`` (the default) — stock behaviour, unchanged.
* ``True`` or ``"auto"`` — one thread per available CPU, capped at 32.
* an ``int`` ``N >= 1`` — exactly N threads.

Each WebSocket session is assigned to one pool thread when it connects (the
least-loaded one) and keeps it for its lifetime, so thread-locals, the
thread's Django DB connection and the Rust renderer's thread-local cells stay
consistent for the session. Different sessions can then run their sync code
at the same time on different threads. The session's render lock still
serialises its own work, so per-session ordering is unchanged.

The mechanism is asgiref's own: ``SyncToAsync.thread_sensitive_context``
selects the executor that thread-sensitive calls run on (Django's ASGI
handler uses it to give each HTTP request its own thread). The consumer sets
it to the session's pool slot for the life of the connection, so EVERY
thread-sensitive call the session makes lands there — djust's, Channels'
``database_sync_to_async``, and the app's own ``sync_to_async``. HTTP and SSE
requests never pass through here and keep Django's behaviour, unless the HTTP
app is wrapped in :class:`PooledHTTP` (#3114).

``PooledHTTP`` bounds the HTTP side the same way. Django's ASGI handler gives
EVERY request a new thread (``ThreadSensitiveContext``), so an overload burst
of N in-flight requests is N threads. On free-threaded CPython each thread
gets its own allocator heap, and that memory stays resident after the thread
exits: 256 concurrent page GETs took a snake-arena process from 81 MB to
1.26 GB of RSS. Wrapped, requests share a small pool of threads instead.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

#: Upper bound for ``worker_threads = True`` / ``"auto"``. More threads than
#: this buys nothing measurable and costs memory per thread (#3074 measured
#: about 2.2–2.7 MB per session with a pinned pool on 3.14t).
AUTO_MAX_THREADS = 32


class _Slot:
    """One pool thread: the key asgiref maps to its single-thread executor.

    asgiref keeps ``context -> executor`` in a WeakKeyDictionary, so the slot
    objects live in the module-level pool for the life of the process.
    """

    __slots__ = ("index", "sessions", "db_check_due", "checks_on_run", "__weakref__")

    def __init__(self, index: int) -> None:
        self.index = index
        self.sessions = 0
        # Set on the event loop when a WebSocket frame arrives for a session
        # on this thread; the thread runs Django's close_old_connections()
        # before its next task (#3095). See mark_db_check_due().
        self.db_check_due = False
        # Whether this slot's executor is a _SlotExecutor (so it honours
        # db_check_due). False if asgiref already had an executor for it.
        self.checks_on_run = False

    def __repr__(self) -> str:
        return f"<djust worker {self.index} sessions={self.sessions}>"


class _HTTPSlot:
    """One thread of the HTTP pool (:class:`PooledHTTP`, #3114).

    Deliberately not a :class:`_Slot`: :func:`offload_enabled` is the
    WebSocket consumer's switch and must stay off on the HTTP path.
    ``sessions`` counts the requests currently bound to the slot.
    """

    __slots__ = ("index", "sessions", "__weakref__")

    def __init__(self, index: int) -> None:
        self.index = index
        self.sessions = 0

    def __repr__(self) -> str:
        return f"<djust http worker {self.index} requests={self.sessions}>"


_lock = threading.Lock()
_pool: List[_Slot] = []
# HTTP pools by size: every ``PooledHTTP`` of one size shares a pool, and
# instances of different sizes never rebuild each other's.
_http_pools: Dict[int, List[_HTTPSlot]] = {}


def _channels_checks_per_message() -> bool:
    """Channels 4.2+ runs ``aclose_old_connections()`` before every message."""
    try:
        import channels.consumer
    except ImportError:  # pragma: no cover - channels is a dependency
        return False
    return hasattr(channels.consumer, "aclose_old_connections")


_CHANNELS_CHECKS_PER_MESSAGE = _channels_checks_per_message()


def _run_checked(slot: _Slot, fn: Any, args: Any, kwargs: Any) -> Any:
    """Run one task on a slot's thread, first doing a DB check that is due."""
    if slot.db_check_due:
        # Clear before checking: a frame that marks the slot again while the
        # check runs gets its own check before the next task.
        slot.db_check_due = False
        from django.db import close_old_connections

        try:
            close_old_connections()
        except Exception:  # noqa: BLE001 - never fail another session's task
            # Value-free: a database error's message can carry query values.
            # The task's own database access reports a real failure.
            logger.warning("djust worker pool: close_old_connections() failed")
    return fn(*args, **kwargs)


class _SlotExecutor(ThreadPoolExecutor):
    """A pool thread's single-thread executor that honours ``db_check_due``."""

    def __init__(self, slot: _Slot, prefix: str = "djust-worker") -> None:
        super().__init__(max_workers=1, thread_name_prefix=f"{prefix}-{slot.index}")
        self._slot = slot

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return super().submit(_run_checked, self._slot, fn, args, kwargs)


def _available_cpus() -> int:
    count = getattr(os, "process_cpu_count", None)  # Python 3.13+
    if count is not None:
        n = count()
    elif hasattr(os, "sched_getaffinity"):
        n = len(os.sched_getaffinity(0))
    else:
        n = os.cpu_count()
    return max(1, n or 1)


def resolve_pool_size(value: Any) -> int:
    """Map a ``worker_threads`` setting to a thread count; 0 means stock.

    Raises ``ValueError`` for a value that is not one of the documented
    spellings, so a typo is reported by the system check (``djust.C021``)
    instead of silently meaning "off".
    """
    if value is None or value is False:
        return 0
    if value is True or value == "auto":
        return min(AUTO_MAX_THREADS, _available_cpus())
    if type(value) is int and value >= 0:
        return value
    raise ValueError(
        f"LIVEVIEW_CONFIG['worker_threads'] must be None, False, True, 'auto' or an "
        f"integer >= 0; got {value!r}"
    )


def configured_pool_size() -> int:
    """The pool size the current ``LIVEVIEW_CONFIG`` asks for (0 = stock).

    An invalid value is logged and treated as 0, the stock behaviour; the
    system check reports it at startup.
    """
    from .config import config

    value = config.get("worker_threads", None)
    try:
        return resolve_pool_size(value)
    except ValueError:
        logger.warning(
            "LIVEVIEW_CONFIG['worker_threads'] = %r is not None, False, True, 'auto' "
            "or an integer >= 0; using the default shared thread (see djust.C021)",
            value,
        )
        return 0


def _thread_sensitive_context() -> Optional["contextvars.ContextVar[Any]"]:
    from asgiref.sync import SyncToAsync

    var = getattr(SyncToAsync, "thread_sensitive_context", None)
    return var if isinstance(var, contextvars.ContextVar) else None


def _noop() -> None:
    return None


def _build_slots(cls: Any, size: int, prefix: str) -> List[Any]:
    """Create ``size`` slots, each with a started single-thread executor.

    Named threads make the pool visible in py-spy, ps -M and thread dumps.
    asgiref would otherwise create an unnamed single-thread executor for the
    slot on first use.

    Each thread is started here, from an EMPTY context (#3114). A thread lives
    as long as the process, and on Python 3.14+ a new thread starts with a
    copy of its starter's context (``sys.flags.thread_inherit_context``, on by
    default in free-threaded builds). Started lazily by the first session's
    call, the thread kept that session's context values -- and through them
    the session -- alive for good. (Each call still runs in its caller's
    context: asgiref passes it along with the work.)
    """
    from asgiref.sync import SyncToAsync

    slots = [cls(i) for i in range(size)]
    executors = getattr(SyncToAsync, "context_to_thread_executor", None)
    if executors is None:
        return slots
    empty = contextvars.Context()
    for slot in slots:
        if slot not in executors:
            executor: ThreadPoolExecutor
            if isinstance(slot, _Slot):
                # WebSocket session threads honour db_check_due (#3095).
                executor = _SlotExecutor(slot, prefix)
                slot.checks_on_run = True
            else:
                executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix=f"{prefix}-{slot.index}"
                )
            empty.run(executor.submit, _noop)
            executors[slot] = executor
    return slots


def _ensure_pool(size: int) -> List[_Slot]:
    """Create the pool on first use; later calls return the same slots.

    A resize (the setting changed at runtime, as tests do with
    ``override_settings``) builds the new slots and leaves the old ones to
    finish the sessions still pinned to them.
    """
    global _pool
    if len(_pool) != size:
        _pool = _build_slots(_Slot, size, "djust-worker")
    return _pool


def _ensure_http_pool(size: int) -> List[_HTTPSlot]:
    """The HTTP pool of ``size`` threads (#3114), created on first use."""
    pool = _http_pools.get(size)
    if pool is None:
        pool = _http_pools[size] = _build_slots(_HTTPSlot, size, "djust-http")
    return pool


class SessionBinding:
    """The pool slot a WebSocket session (or, with :class:`PooledHTTP`, an
    HTTP request) is pinned to, while it is bound."""

    __slots__ = ("slot", "_token")

    def __init__(self, slot: Any, token: "contextvars.Token[Any]") -> None:
        self.slot = slot
        self._token = token

    def release(self) -> None:
        var = _thread_sensitive_context()
        if var is not None:
            try:
                var.reset(self._token)
            except ValueError:
                # Reset from a different context (should not happen: the
                # consumer binds and releases in its own __call__). Leave the
                # contextvar alone rather than raise during teardown.
                logger.debug("djust worker pool: binding released from another context")
        with _lock:
            self.slot.sessions -= 1


def bind_session() -> Optional[SessionBinding]:
    """Pin the calling WebSocket session to a pool thread, if the pool is on.

    Returns ``None`` — and changes nothing — when ``worker_threads`` is off,
    when asgiref has no ``thread_sensitive_context`` to set, or when an outer
    context already chose an executor (so a consumer nested inside one keeps
    it). Must be called from the consumer's own task, before it dispatches
    anything; every task the consumer spawns copies the binding.
    """
    size = configured_pool_size()
    if size <= 0:
        return None
    var = _thread_sensitive_context()
    if var is None:
        logger.warning(
            "LIVEVIEW_CONFIG['worker_threads'] is set but this asgiref has no "
            "SyncToAsync.thread_sensitive_context; using the default shared thread"
        )
        return None
    if var.get(None) is not None:
        logger.debug(
            "djust worker pool: an outer context already chose a thread for this "
            "connection; the session keeps it"
        )
        return None
    with _lock:
        pool = _ensure_pool(size)
        slot = min(pool, key=lambda s: (s.sessions, s.index))
        slot.sessions += 1
    return SessionBinding(slot, var.set(slot))


def pool_stats() -> List[dict]:
    """Per-thread session counts, for diagnostics and tests."""
    with _lock:
        return [{"index": s.index, "sessions": s.sessions} for s in _pool]


def _parent_sync_thread() -> bool:
    """Whether asgiref would send thread-sensitive calls to a parent sync
    thread (``async_to_sync`` further up) instead of the session's slot."""
    from asgiref.sync import AsyncToSync

    return getattr(AsyncToSync.executors, "current", None) is not None


def http_pool_stats() -> List[dict]:
    """Per-thread counts of bound HTTP requests (:class:`PooledHTTP`), for
    every HTTP pool, smallest pool first."""
    with _lock:
        return [
            {"index": s.index, "requests": s.sessions}
            for size in sorted(_http_pools)
            for s in _http_pools[size]
        ]


def bind_http_request(size: int) -> Optional[SessionBinding]:
    """Bind the calling HTTP request to one thread of the HTTP pool (#3114).

    ``size`` is the pool size; ``0`` binds nothing. Returns ``None`` -- and
    changes nothing -- when ``size`` is 0, when asgiref has no
    ``thread_sensitive_context``, or when an outer context already chose an
    executor. Otherwise every thread-sensitive call the request makes,
    including Django's ASGI handler's own (its ``ThreadSensitiveContext`` is
    re-entrant and keeps the outer choice), runs on the least-loaded pool
    thread. Call :meth:`SessionBinding.release` when the request ends.
    """
    if size <= 0:
        return None
    var = _thread_sensitive_context()
    if var is None or var.get(None) is not None:
        return None
    with _lock:
        pool = _ensure_http_pool(size)
        slot = min(pool, key=lambda s: (s.sessions, s.index))
        slot.sessions += 1
    return SessionBinding(slot, var.set(slot))


class PooledHTTP:
    """ASGI middleware: run HTTP requests on a bounded pool of threads (#3114).

    Django's ASGI handler gives every request a NEW thread, so a burst of N
    in-flight requests is N threads. On free-threaded CPython each thread gets
    its own allocator heap, and that memory stays resident after the thread
    exits (about 4 MB per concurrent request in the #3114 measurements). Wrap
    the HTTP app to put every request's sync work -- middleware, the view,
    Django's signal handlers -- on one of ``threads`` long-lived threads::

        from djust.worker_pool import PooledHTTP

        application = ProtocolTypeRouter({
            "http": PooledHTTP(get_asgi_application()),
            "websocket": ...,
        })

    ``threads=None`` (the default) uses the size of the WebSocket pool,
    ``LIVEVIEW_CONFIG["worker_threads"]``, and passes every request through
    unchanged while that setting is off. An int sets the size (``0`` = pass
    through); ``True`` / ``"auto"`` mean one thread per CPU, at most 32, as
    for ``worker_threads``. The HTTP pool's threads are separate from the
    WebSocket sessions' (``djust-http-N``); instances of the same size share
    one pool.

    Under a burst, at most ``threads`` requests run sync code at once; the
    rest wait on the event loop, which costs a coroutine each rather than a
    thread. That is the thread model of a WSGI server, with its caveats:

    * a sync view (or a sync streaming iterator) that blocks for a long time
      holds its thread, and requests bound to that thread wait behind it;
    * a sync view that blocks until another request's sync code has run can
      wait forever when both are on one thread;
    * state kept on the thread outlives the request: each pool thread keeps
      its database connection between requests (Django's
      ``close_old_connections`` still runs at each request's start and end),
      and a ``threading.local`` that code sets without clearing is seen by
      the thread's next request.

    A request goes to the thread with the fewest requests bound to it. That
    count is not how busy the thread is: a request cancelled mid-view (its
    client went away) releases its slot while its sync code finishes, and an
    async streaming response keeps its slot while using no thread.

    Scopes other than ``http`` (``websocket``, ``lifespan``) pass through, and
    a request whose context already chose an executor keeps it.
    """

    def __init__(self, app: Any, threads: Union[None, bool, int, str] = None) -> None:
        self.app = app
        if threads is not None:
            try:
                resolve_pool_size(threads)
            except ValueError:
                raise ValueError(
                    "PooledHTTP threads must be None, False, True, 'auto' or an integer "
                    f">= 0; got {threads!r}"
                ) from None
        self.threads = threads

    def _size(self) -> int:
        if self.threads is not None:
            return resolve_pool_size(self.threads)
        from .config import config

        try:
            return resolve_pool_size(config.get("worker_threads", None))
        except ValueError:
            # Invalid worker_threads: pass through, as the WebSocket path falls
            # back to stock. The system check (djust.C021) reports it, and the
            # WebSocket path logs it; logging here would repeat it per request.
            return 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        binding = bind_http_request(self._size())
        if binding is None:
            return await self.app(scope, receive, send)
        try:
            return await self.app(scope, receive, send)
        finally:
            binding.release()


def offload_enabled() -> bool:
    """Whether the calling session is pinned to a pool thread (#3074).

    True inside a WebSocket consumer (and every task it spawned) that
    :func:`bind_session` bound. djust then also moves per-frame work off the
    asyncio event loop onto the session's thread: the pre-event assigns
    snapshot runs in the handler's hop, and a server-push turn runs as one
    hop. Off (stock behaviour) everywhere else: with the one shared thread,
    moving work there would only load the thread that is already the
    bottleneck.
    """
    var = _thread_sensitive_context()
    return var is not None and isinstance(var.get(None), _Slot) and not _parent_sync_thread()


def mark_db_check_due() -> bool:
    """Defer Django's per-message DB-connection check to the session's thread.

    Channels runs ``close_old_connections()`` in its own ``sync_to_async``
    hop before every message a consumer receives: an event-loop round trip
    per WebSocket frame. With the pool on, the check instead runs on the
    session's thread at the start of its next task, before any code of the
    message's turn can touch the database there (#3095). Connections are
    per thread, so marking the thread is what matters; as with the hop,
    other sessions' tasks may run on the thread between the check and this
    message's own work.

    Returns True when the check was deferred (the caller skips the hop),
    False when the calling session is not pinned to a pool thread whose
    executor honours the mark (the caller keeps Channels' hop).
    """
    if not _CHANNELS_CHECKS_PER_MESSAGE:
        return False  # nothing to defer: this Channels makes no per-message check
    var = _thread_sensitive_context()
    slot = var.get(None) if var is not None else None
    if not isinstance(slot, _Slot) or not slot.checks_on_run or _parent_sync_thread():
        return False
    slot.db_check_due = True
    return True
