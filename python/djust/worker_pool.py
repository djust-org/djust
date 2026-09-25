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
requests never pass through here and keep Django's behaviour.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional

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

    __slots__ = ("index", "sessions", "__weakref__")

    def __init__(self, index: int) -> None:
        self.index = index
        self.sessions = 0

    def __repr__(self) -> str:
        return f"<djust worker {self.index} sessions={self.sessions}>"


_lock = threading.Lock()
_pool: List[_Slot] = []


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


def _ensure_pool(size: int) -> List[_Slot]:
    """Create the pool on first use; later calls return the same slots.

    A resize (the setting changed at runtime, as tests do with
    ``override_settings``) builds the new slots and leaves the old ones to
    finish the sessions still pinned to them.
    """
    global _pool
    if len(_pool) != size:
        from asgiref.sync import SyncToAsync

        slots = [_Slot(i) for i in range(size)]
        executors = getattr(SyncToAsync, "context_to_thread_executor", None)
        for slot in slots:
            if executors is not None and slot not in executors:
                # Named threads make the pool visible in py-spy, ps -M and
                # thread dumps. asgiref would otherwise create an unnamed
                # single-thread executor for the slot on first use.
                executors[slot] = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix=f"djust-worker-{slot.index}"
                )
        _pool = slots
    return _pool


class SessionBinding:
    """The pool slot a WebSocket session is pinned to, while it is bound."""

    __slots__ = ("slot", "_token")

    def __init__(self, slot: _Slot, token: "contextvars.Token[Any]") -> None:
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
