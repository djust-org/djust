"""
Transport-agnostic runtime for djust LiveView.

This module factors view-lifecycle dispatch (``dispatch_mount``,
``dispatch_event``, ``dispatch_url_change``) out of the WebSocket consumer
and SSE views so both transports share one code path. See
ADR-016 and issue #1237.

Architecture
------------

The runtime owns the mounted ``view_instance`` and orchestrates the call
sequence (auth check -> mount -> handle_params -> render) without knowing
or caring how outbound frames reach the client. All output flows through
``self.transport.send(...)``.

Two transport adapters live alongside the runtime:

- :class:`WSConsumerTransport` wraps a ``LiveViewConsumer`` so the runtime
  can drive WebSocket frames.
- :class:`SSESessionTransport` wraps an ``SSESession`` so the runtime can
  push frames into the SSE event queue.

Both adapters expose the same minimal :class:`Transport` Protocol, which
also matches the interface ``websocket_utils._validate_event_security``
already expects (``send_error``, ``close``, ``_client_ip``).

Phasing
-------

In this PR (v0.9.2-1) only WebSocket's ``handle_url_change`` migrates to
the runtime. ``handle_event``/``handle_mount`` remain on the existing
WS-specific paths until follow-up PRs migrate them. SSE uses the runtime
for all three verbs (mount / event / url_change).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import contextvars
import hashlib
import inspect
import logging
import os
import re
import sys
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    AsyncIterator,
    Callable,
    ContextManager,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

from asgiref.sync import sync_to_async

from . import _per_turn
from ._params_form import run_handle_params
from ._view_hooks import call_async_result, call_hook

if TYPE_CHECKING:
    from ._async_batch import AsyncBatch
    from ._exposure_children import ChildStateSession

from ._class_snapshot import attribute_names
from ._late_save import LateSaveDropped
from ._turn_ports import RuntimeTurnPort
from .rate_limit import ConnectionRateLimiter
from .security import handle_exception, sanitize_for_log
from .serialization import fast_json_loads
from .turns import Turn, current_turn, run_turn
from .validation import (
    validate_handler_params,
    validated_call_arguments,
)
from .websocket_utils import (
    _call_handler,
    _safe_error,
    _validate_event_security,
    get_handler_coerce_setting,
)

# How long an explicit turn waits for its own state save, by default (#4098).
#
# An ADR-038 explicit save is the turn's commit: the success frame is withheld
# until storage has the state. The turn waits this long for the save, under
# the runtime's event lock, so the next event of the same page starts after
# it: back-to-back events all take effect in order, each with its own frame.
#
# Until #4098 this was 0.150, inherited from the best-effort legacy save
# (#1475) that 2.0 removed. For an explicit turn that was too tight: one slow
# fsync or a loaded process (#3200 measured a p95 near 200 ms) had the
# event's frame replaced by a transient ``state_error`` and a later full-HTML
# catch-up. 2 s absorbs those spikes, so nearly every event keeps its own
# frame.
#
# It is deliberately NOT the 10 s stuck cap. While a turn waits, the page's
# event lock is held: later events queue behind it with no timeout of their
# own, a single-view socket's receive loop is in that turn, ticks and
# ``db_notify`` turns are skipped and pushes are queued. With storage that is
# slow but working (2 s writes) a 10 s wait would answer event k after about
# k x 2 s and never escalate. Past this deadline the deferral path of #3200
# takes over instead (:class:`ExplicitSaveDeferred`): a transient error, one
# catch-up turn that saves the newest state, and the reload error after
# ``_EXPLICIT_MAX_DEFERRALS`` deferrals in a row, so stuck storage is reported
# within seconds. ``DJUST_EXPLICIT_STATE_SAVE_TIMEOUT`` changes it.
#
# The deadline counts only the save's own running time, never its queueing on
# the Django thread: see explicit_state_save_timeout / _run_explicit_save.
#
# It stays a module constant, read at call time, so a test can change it.
EVENT_STATE_SAVE_TIMEOUT_S = 2.0


logger = logging.getLogger(__name__)


def explicit_state_save_timeout() -> float:
    """The storage deadline of an explicit turn's save, in seconds (#3200).

    ``DJUST_EXPLICIT_STATE_SAVE_TIMEOUT`` when set to a valid value, otherwise
    :data:`EVENT_STATE_SAVE_TIMEOUT_S` (2 s since #4098). The constant is read
    at call time, so a test that changes it changes this. An invalid setting falls back to the
    default rather than failing every save; system check ``djust.C024``
    reports it at startup.
    """
    from django.conf import settings

    from ._exposure_sessions import valid_explicit_state_save_timeout

    value = getattr(settings, "DJUST_EXPLICIT_STATE_SAVE_TIMEOUT", None)
    if valid_explicit_state_save_timeout(value):
        return float(value)
    return EVENT_STATE_SAVE_TIMEOUT_S


#: The static error an explicit turn sends when its save outran the deadline
#: (or waited too long for a save thread).
#: Not "reload": the values are kept and the next turn saves them (#3200).
_EXPLICIT_SAVE_DEFERRED_MESSAGE = (
    "Your last change is still being saved. It will appear with your next update."
)


class ExplicitSaveDeferred(Exception):
    """An explicit save outran its storage deadline (#3200).

    Transient, and distinct from a refused or failed save. The turn's values
    are still on the view. If the save started, it keeps running in the Django
    thread (sync work cannot be cancelled) and usually lands; the next save of
    the same runtime waits for it first, so it can never overwrite a newer one.

    ``work`` is that running save (``None`` when this save never started,
    because the previous one was still running at the deadline).

    ``waited`` is the storage deadline the turn waited out, in seconds:
    ``None`` when no storage time was used (the save was still queued for a
    pool thread at the start bound). A turn that waited the stuck cap (a
    deadline configured at 10 s) has nothing left to promise, so its deferral
    is the reload error (#4098).
    """

    work: "Optional[asyncio.Future[None]]" = None
    waited: Optional[float] = None


async def _await_save(work: "asyncio.Future[None]", timeout: float) -> None:
    """Wait up to ``timeout`` for ``work`` without cancelling it.

    Raises ``asyncio.TimeoutError`` when it is still running at the deadline
    and re-raises whatever it raised when it finished in time.

    This is the ``wait_for(shield(work))`` shape minus ``shield``. Since
    Python 3.14 a shield whose waiter has gone adds a done callback to the
    inner future that hands its exception to the loop's exception handler, and
    the ``asyncio`` logger then writes that exception's message and traceback
    at ERROR (``OSError exception in shielded future``). A save that outruns
    its deadline and then fails is exactly that case, and a storage exception
    can carry server-only values: this module reports the failure without them
    (:func:`_log_unobserved_save_failure`) and must be the only reporter.
    ``asyncio.wait`` leaves ``work`` alone on timeout and on cancellation, so
    the save keeps running and nothing else observes its failure.
    """
    done, _pending = await asyncio.wait({work}, timeout=timeout)
    if not done:
        raise asyncio.TimeoutError
    work.result()


def _log_unobserved_save_failure(work: "asyncio.Future[None]") -> None:
    """Report a save failure nobody is waiting for any more (value-free)."""
    if work.cancelled() or work.exception() is None:
        return
    if isinstance(work.exception(), LateSaveDropped):
        return  # a logged-out session, already reported at debug (#3247)
    logger.warning("State save failed after its turn stopped waiting")


def _detach_from_finished_request() -> None:
    """Stop routing a detached task's sync calls to the request that spawned it.

    A task created while an HTTP request is being served through an
    ``async_to_sync`` bridge (the SSE message POST) inherits that request's
    asgiref routing: ``AsyncToSync.executors.current``, the request thread's
    ``CurrentThreadExecutor``, and its per-request ``ThreadSensitiveContext``.
    Both die with the request. After that every ``sync_to_async`` in the task
    raises "CurrentThreadExecutor already quit or is broken", or is queued on
    a thread that never runs it (#3200 review: the SSE catch-up and background
    turns). Called at the start of tasks that outlive their turn, it routes
    their sync work to the loop's default sync thread instead, which is where
    a WebSocket session's work runs.

    Without a bridge (an all-async middleware stack) the task kept the
    request's ``ThreadSensitiveContext``, so its sync work ran on the
    request's executor, and a callback still running when the POST ended
    blocked the event loop in that context's ``__aexit__`` (#3212). That case
    is detached too. A WebSocket worker-pool slot (#3074) and a
    ``PooledHTTP`` slot are long-lived and left alone.
    """
    if _in_request_scoped_executor():
        _leave_request_executors()


def _in_request_scoped_executor() -> bool:
    """Whether thread-sensitive calls made here run on executors a request owns.

    Django's ASGI handler serves every HTTP request inside a per-request
    ``ThreadSensitiveContext``, whose single-thread executor is shut down, and
    WAITED for, when the request ends. Under a sync middleware stack the
    calls go to the ``async_to_sync`` bridge thread of the request instead.
    A WebSocket session's worker-pool slot (#3074) and a ``PooledHTTP`` slot
    (#3114) are long-lived and never shut down, so they do not count.
    """
    from asgiref.sync import AsyncToSync, SyncToAsync, ThreadSensitiveContext

    if getattr(AsyncToSync.executors, "current", None) is not None:
        return True
    return isinstance(SyncToAsync.thread_sensitive_context.get(None), ThreadSensitiveContext)


def _leave_request_executors() -> None:
    """Route this context's thread-sensitive calls to the loop's default sync
    thread. Both writes are context-local (an asgiref ``Local`` and a
    contextvar), so only the context this runs in is changed."""
    from asgiref.sync import AsyncToSync, SyncToAsync

    AsyncToSync.executors.current = None
    SyncToAsync.thread_sensitive_context.set(None)  # type: ignore[arg-type]


#: Threads for saves made inside a request's executors (#3212). A pool, not
#: one thread: one hung store write must not hold every other session's save
#: (review B2). A runtime has at most one save in flight
#: (:func:`_run_explicit_save`), so a pool keeps each view's saves ordered.
_SAVE_POOL_SIZE = min(32, (os.cpu_count() or 1) * 4)
_save_executor: Optional[ThreadPoolExecutor] = None
_save_executor_lock = threading.Lock()


def _close_old_connections_quietly() -> None:
    from django.db import close_old_connections

    try:
        close_old_connections()
    except Exception:  # noqa: BLE001 - never fail a save over connection hygiene
        # Value-free: a database error's message can carry query values.
        logger.warning("djust state save: close_old_connections() failed")


def _run_with_connection_hygiene(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """One job on a long-lived save thread, bracketed like a request (#3095).

    A request thread gets ``close_old_connections()`` from
    ``request_started``/``request_finished``; these threads live as long as
    the process, so without it ``CONN_MAX_AGE`` and ``CONN_HEALTH_CHECKS``
    never apply and a connection broken under a thread (a failover, an idle
    timeout) stays broken for every later save on it (review B1).
    """
    _close_old_connections_quietly()
    try:
        return fn(*args, **kwargs)
    finally:
        _close_old_connections_quietly()


class _SaveExecutor(ThreadPoolExecutor):
    """The save pool: every job runs with the connection hygiene above."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        return super().submit(_run_with_connection_hygiene, fn, *args, **kwargs)


def _request_save_executor() -> ThreadPoolExecutor:
    """The long-lived pool for saves made inside a request's executors.

    Every thread is started here, from an EMPTY context: on Python 3.14+ a new
    thread copies its starter's context, and these live as long as the
    process (the worker pool does the same, #3114). ``ThreadPoolExecutor``
    starts a thread only when none is idle, so the warm-up jobs hold a barrier
    until all of them run at once.
    """
    global _save_executor
    with _save_executor_lock:
        if _save_executor is None:
            size = _SAVE_POOL_SIZE
            executor = _SaveExecutor(max_workers=size, thread_name_prefix="djust-state-save")
            barrier = threading.Barrier(size)

            def warm() -> None:
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    # A warm-up that timed out only leaves a thread to start lazily.
                    pass

            empty = contextvars.Context()
            for _ in range(size):
                empty.run(ThreadPoolExecutor.submit, executor, warm)
            _save_executor = executor
        return _save_executor


def _spawn_save(run: Callable[[], None]) -> "asyncio.Future[None]":
    """Start ``run`` in a Django thread, outside any request's executors (#3212).

    A save can outlive the turn that started it (its deadline passed, or the
    turn was cancelled). Run on a request's executor, it then held the
    request's end: ``ThreadSensitiveContext.__aexit__`` waits for the executor,
    which blocked the EVENT LOOP on the SSE event POST until storage answered,
    and under sync middleware it held the request's bridge thread. Inside a
    request's executors the save therefore runs on a dedicated pool of its
    own, so a request ends when its turn does. It is not the loop's shared
    sync thread: the request's own sync code may be running there, waiting for
    this turn. Anywhere else (a WebSocket session, a ``PooledHTTP`` request)
    the thread is unchanged. Saves of one runtime stay ordered either way
    (:func:`_run_explicit_save`).

    A pool save can still be running after its request ended, when another
    request may have logged the user out. Its session writes re-check the
    session first (:mod:`djust._late_save`, #3247).
    """
    if not _in_request_scoped_executor():
        return asyncio.ensure_future(sync_to_async(run)())
    from ._late_save import detached

    call = sync_to_async(detached(run), thread_sensitive=False, executor=_request_save_executor())
    return asyncio.ensure_future(call())


#: How long a REQUEST-SCOPED save (the SSE/HTTP pool) may wait for a thread
#: before its turn is deferred (#3212). Strictly below the 10 s stuck cap, so a
#: first deferral is transient. WebSocket saves keep #3206's unbounded queue
#: wait on their session's thread (#3229 re-review R1).
_SAVE_START_TIMEOUT_S = 5.0

#: When each save started RUNNING (loop time), keyed by its future. "Stuck"
#: (the terminal escalation) counts from here, not from when it was queued,
#: and the entry follows the future when a new runtime inherits it (SSE
#: navigation or reconnect).
_save_started_at: "weakref.WeakKeyDictionary[asyncio.Future[None], float]" = (
    weakref.WeakKeyDictionary()
)


def _save_running_for(pending: "Optional[asyncio.Future[None]]") -> float:
    """Seconds ``pending`` has been running; 0 while queued or when done."""
    if pending is None or pending.done():
        return 0.0
    started = _save_started_at.get(pending)
    if started is None:
        return 0.0
    return asyncio.get_running_loop().time() - started


#: Consecutive deferred saves after which a deferral is reported as the terminal
#: reload error, as before #3200: storage is not recovering (review I-a).
_EXPLICIT_MAX_DEFERRALS = 3


def _drop_processor_replay(view: Any) -> None:
    """End of the page render that follows a refused scoped component render:
    the processor outputs the check recorded for it are dropped, used or not,
    so they cannot be taken by a later turn (#3996)."""
    from .mixins.context import PROCESSOR_REPLAY_ATTR

    storage = getattr(view, "__dict__", None)
    if storage is not None:
        storage.pop(PROCESSOR_REPLAY_ATTR, None)


def _mints_no_client_snapshot(view: Any) -> bool:
    """True when ``snapshot_codec`` would return None for *view*: an explicit
    view whose contract compiles and declares no ``persist="client"`` field.

    Anything else (an unreadable policy, a contract that does not compile) is
    False, so the caller takes the path that reports it.
    """
    from ._exposure import ExposureContract

    try:
        policy = getattr(view, "exposure_policy", None)
        if type(policy) is not str or policy != "explicit":
            return False
        fields = ExposureContract.from_view_class(type(view)).fields
        return not any(field.persist == "client" for field in fields.values())
    except Exception:  # noqa: BLE001 — the checked path raises and reports it
        return False


async def _run_explicit_save(
    owner: Any, save: Callable[[], None], *, deadline: Optional[float] = None
) -> None:
    """Run one explicit save in ONE Django-thread hop, timed from when it starts.

    The old shape made two or three ``sync_to_async`` hops inside a single
    ``asyncio.wait_for``. With the default single sync thread, those hops queue
    behind every other session's handlers and renders, so under load the
    150 ms budget was spent waiting for the thread, not on storage (#3200).

    Here the whole save (binding check, projection, write) is one sync
    callable, and the deadline starts only when the thread begins running it.
    The queue wait is not bounded here; it is the same wait the turn's handler
    and render hops already accept on that thread.

    Saves are ORDERED per ``owner`` (the runtime). A save that outran its
    deadline is still running; the next save waits for it, within its own
    deadline, before starting. Without that, a turn whose request runs on
    another thread (SSE, the pooled executor) could be acknowledged and then
    overwritten by the older save landing late. If the previous save is still
    running at the deadline, this save does not start and is deferred too.

    ``deadline`` overrides the configured bound; the catch-up turn passes the
    10 s cap, since nobody waits on its save interactively.

    Raises :class:`ExplicitSaveDeferred` when the deadline passes, and
    re-raises whatever ``save`` raised otherwise.
    """
    if deadline is None:
        deadline = explicit_state_save_timeout()
    previous = getattr(owner, "_explicit_save_pending", None)
    if previous is not None and not previous.done():
        try:
            await _await_save(previous, deadline)
        except asyncio.TimeoutError:
            if not previous.done():
                behind = ExplicitSaveDeferred("Previous explicit save still running")
                behind.waited = deadline
                raise behind from None
        except Exception:  # noqa: BLE001 — its failure is logged by its own callback
            logger.debug("Previous explicit save failed; this save writes current values")

    loop = asyncio.get_running_loop()
    started: "asyncio.Future[None]" = loop.create_future()

    work: "Optional[asyncio.Future[None]]" = None

    def mark_started() -> None:
        if work is not None:
            _save_started_at[work] = loop.time()
        if not started.done():
            started.set_result(None)

    def run() -> None:
        loop.call_soon_threadsafe(mark_started)
        save()

    # Only a request-scoped save waits for a pool thread with a bound (#3212
    # review B2): the pool is shared by every SSE session. A WebSocket save
    # keeps the unbounded wait described above (#3229 re-review R1).
    start_bound = _SAVE_START_TIMEOUT_S if _in_request_scoped_executor() else None
    work = _spawn_save(run)
    owner._explicit_save_pending = work
    waited: Optional[float] = None
    try:
        # Bounded for a request-scoped save: queued past the bound (every pool
        # thread busy), it is deferred rather than holding the turn. It still
        # runs when a thread frees, and the runtime's next save waits for it.
        await asyncio.wait(
            {started, work}, timeout=start_bound, return_when=asyncio.FIRST_COMPLETED
        )
        if not started.done() and not work.done():
            raise asyncio.TimeoutError
        waited = deadline
        await _await_save(work, deadline)
    except asyncio.TimeoutError:
        if work.done():
            raise  # the save's own TimeoutError (a backend timeout): a failure
        work.add_done_callback(_log_unobserved_save_failure)
        deferred = ExplicitSaveDeferred("Explicit state save exceeded its storage deadline")
        deferred.work = work
        deferred.waited = waited
        raise deferred from None
    except asyncio.CancelledError:
        # The turn was cancelled (socket closed) while the save runs on.
        work.add_done_callback(_log_unobserved_save_failure)
        raise


#: Marks a deferred root save that never started (``ExplicitSaveDeferred.work``
#: is None): its values are on the view only (#4095).
_SAVE_NEVER_STARTED = object()


def _discard_turn_effects(view: Any) -> None:
    """Drop what a refused turn's handler queued for after its turn (#4095).

    The queues the turn-end drain (``ViewRuntime._flush_all_pending``) and the
    background dispatch read: dropped, not sent, because the turn's state was
    refused. Stream operations stay: they are applied by the next render, and
    the restore asks for full HTML.
    """
    tasks = getattr(view, "_async_tasks", None)
    if tasks:
        view._async_tasks = {}
    for drain in (
        "_drain_push_events",
        "_drain_flash",
        "_drain_page_metadata",
        "_drain_pending_layout",
        "_drain_deferred",
        "_drain_navigation",
    ):
        method = getattr(view, drain, None)
        if callable(method):
            method()


async def apply_restored_state(
    view: Any, restored: Dict[str, Any], request: Any, *, run_hook: bool = True
) -> None:
    """Put validated declared state back on ``view``, then let it reconcile.

    The one runtime restore (#4095): the reconnect mount and the over-limit
    rollback both apply stored values this way, so they cannot drift. The
    values come from the explicit codecs, JSON primitives only, never tagged,
    so there is nothing to decode. ``handle_restore(request)`` then runs, as it
    does after every restore (#3989): the values came back after the view's
    other state was set, so the view rebuilds what it derives from them (rows
    read for a restored id, a "running" flag whose work did not survive).
    ``run_hook=False`` leaves the hook to the caller: the mount runs it later,
    after ``handle_params()`` and the object-permission check, the point every
    transport runs it at (#4088). Exceptions propagate; each caller answers
    them its own way.
    """
    from .security import safe_setattr

    for key, value in restored.items():
        safe_setattr(view, key, value, allow_private=False, raise_on_blocked=True)
    if run_hook:
        handle_restore = getattr(view, "handle_restore", None)
        if callable(handle_restore):
            await call_hook(handle_restore, request)


def _diagnostics_policy_allows(owner: Any) -> bool:
    """Legacy owner or DEBUG: see ``_exposure_diagnostics.diagnostics_policy_allows``."""
    from ._exposure_diagnostics import diagnostics_policy_allows

    return diagnostics_policy_allows(owner)


def _mount_tenant_scope(method: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Restore mount-local tenant/diagnostic scopes on failure and early return."""

    @wraps(method)
    async def scoped(*args: Any, **kwargs: Any) -> None:
        from ._exposure_diagnostics import diagnostic_scope

        with diagnostic_scope(), _tenant_context(None):
            await method(*args, **kwargs)

    return scoped


def _unmount_on_failure(method: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Leave nothing mounted when a mount step raises after admission.

    The steps most likely to raise (``mount()``, ``handle_params()``,
    ``connected()``, the render) end through ``_fail_mount``; any other step
    after ``on_view_mounted`` (the state restore, the route binding, the
    baselines, the post-render hooks) raised out with the view still on the
    runtime, in its groups and in presence. The exception still propagates,
    so its error frame is unchanged."""

    @wraps(method)
    async def guarded(runtime: Any, *args: Any, **kwargs: Any) -> None:
        runtime._admitted_mount_view = None
        try:
            await method(runtime, *args, **kwargs)
        except Exception:
            view = runtime._admitted_mount_view
            if view is not None and runtime.view_instance is view:
                await runtime._on_mount_failed(view)
                runtime.view_instance = None
                runtime._explicit_mount_binding = None
                await _release_failed_view(view)
            raise
        finally:
            runtime._admitted_mount_view = None

    return guarded


def _runtime_diagnostic_scope(
    method: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Keep a mounted owner's diagnostic restriction across one runtime turn."""

    @wraps(method)
    async def scoped(runtime: Any, *args: Any, **kwargs: Any) -> None:
        from ._exposure_diagnostics import diagnostic_scope, watch_diagnostic_owner

        with diagnostic_scope():
            watch_diagnostic_owner(runtime, "view_instance")
            await method(runtime, *args, **kwargs)

    return scoped


def _tenant_context(tenant: Any) -> ContextManager[Any]:
    """Bind *tenant* as the current tenant for a runtime dispatch (Finding #6).

    The runtime drives the SSE mount/event/url-change path and the WS
    url-change path. ``TenantMiddleware`` only binds the tenant on the HTTP
    path, so without this the tenant-scoped managers see ``None`` here and
    (fail-closed) return empty querysets. Lazily imports the canonical
    ``tenant_context`` and falls back to a no-op when tenants is unavailable.
    """
    try:
        from .tenants.middleware import tenant_context

        return tenant_context(tenant)
    except Exception:  # noqa: BLE001 — tenants is optional; never break the live path
        from contextlib import nullcontext

        return nullcontext()


# The tenant *bind* step (set_current_tenant) for the mount path is now
# single-sourced in djust.auth.core._bind_current_tenant, invoked by the shared
# run_pre_mount_auth sequence; the runtime no longer carries its own copy. The
# per-event / url-change re-bind still uses the _tenant_context manager above.


def _lifecycle_step(view: Any, fn: Callable[..., Any]) -> Callable[..., Any]:
    """``fn`` (a view's snapshot hook) as a ``"lifecycle"`` step for ``sync_to_async`` (#3637)."""
    from .sync_turn import sync_step

    return sync_step(view, "lifecycle", fn)


def _consume_async_cancel(view: Any, task_name: str) -> bool:
    """True (and forget the mark) when ``task_name`` was cancelled via
    ``cancel_async`` / ``cancel_async_all``. Same consume-once semantics as
    the WS consumer's ``_run_async_work`` checks."""
    cancelled = getattr(view, "_async_cancelled", None)
    if cancelled and task_name in cancelled:
        cancelled.discard(task_name)
        return True
    return False


def take_consumer_view_groups(consumer: Any) -> List[str]:
    """Hand over the channel-layer groups the consumer's current view joined.

    Its server-push view group, presence and presence-scope groups, db_notify
    groups and scoped server-push groups, as group names. The consumer's group
    attributes are reset, so they describe the next mounted view only, but no
    group is left: the caller either leaves them
    (:func:`leave_consumer_view_groups`) or keeps them on record for the view
    they belong to.
    """
    groups: List[str] = []
    from .presence import presence_groups_of

    groups.extend(presence_groups_of(consumer))
    consumer._presence_groups = set()
    consumer._presence_group = None
    for attr in ("_view_group", "_presence_scope_group"):
        group = getattr(consumer, attr, None)
        if isinstance(group, str) and group:
            groups.append(group)
            setattr(consumer, attr, None)
    channels = getattr(consumer, "_db_notify_channels", None)
    if isinstance(channels, set) and channels:
        groups.extend(f"djust_db_notify_{ch}" for ch in channels)
        consumer._db_notify_channels = set()
    scoped = getattr(consumer, "_push_scope_groups", None)
    if isinstance(scoped, dict) and scoped:
        groups.extend(scoped.values())
        consumer._push_scope_groups = {}
    return groups


def view_groups_of(consumer: Any) -> set:
    """The channel-layer groups the consumer's current view has joined, as names.

    What :func:`take_consumer_view_groups` hands over, read without forgetting
    it: a channel-layer message addressed to one of these groups is for this
    view (#3252).
    """
    groups: set = set()
    from .presence import presence_groups_of

    groups.update(presence_groups_of(consumer))
    for attr in ("_view_group", "_presence_scope_group"):
        group = getattr(consumer, attr, None)
        if isinstance(group, str) and group:
            groups.add(group)
    channels = getattr(consumer, "_db_notify_channels", None)
    if isinstance(channels, set):
        groups.update(f"djust_db_notify_{ch}" for ch in channels)
    scoped = getattr(consumer, "_push_scope_groups", None)
    if isinstance(scoped, dict):
        groups.update(scoped.values())
    return groups


def _groups_of_other_views(consumer: Any) -> set:
    """The groups the other mounted views of ``consumer``'s socket have joined."""
    socket = getattr(consumer, "_slot_consumer", consumer)
    view_consumers = getattr(socket, "_view_consumers", None)
    if not callable(view_consumers):
        return set()
    others = view_consumers()
    if not isinstance(others, list):
        return set()
    kept: set = set()
    for other in others:
        if other is not consumer:
            kept |= view_groups_of(other)
    return kept


async def leave_consumer_view_groups(consumer: Any, groups: Any = None) -> None:
    """Leave every channel-layer group the consumer's current view joined.

    Used when a mount is refused after it joined, and when a mounted view is
    replaced by ``live_redirect`` or a second ``mount`` / ``mount_batch`` frame
    (#3245). ``groups`` adds names to leave as well (the groups a
    ``mount_batch`` sibling joined). Resets the consumer's group attributes so
    ``disconnect`` does not discard them a second time. Discard failures are
    logged, never raised.
    """
    names = take_consumer_view_groups(consumer)
    for group in groups or ():
        if group not in names:
            names.append(group)
    channel_layer = getattr(consumer, "channel_layer", None)
    if channel_layer is None:
        return
    # Group membership belongs to the socket's channel, not to a view: two views
    # of one class, on one db_notify channel or tracking one presence key joined
    # the same group. Leaving it for one would silence the other, so a group a
    # remaining view still names stays joined (#3252 review I1).
    kept = _groups_of_other_views(consumer)
    for group in names:
        if group in kept:
            continue
        try:
            await channel_layer.group_discard(group, consumer.channel_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("Error leaving channel group %s: %s", group, e)


def maybe_start_tick_task(consumer: Any, view_class: Any) -> bool:
    """Start the periodic tick task for ``view_class`` if it opted in.

    A view opts in by setting ``tick_interval`` AND overriding ``handle_tick``
    — the base implementation is a no-op, so starting a loop for it would burn
    a task per connection to render nothing.

    Extracted from the mount hook so a test can assert "mount starts the tick
    task" against THIS function rather than re-implementing the condition
    (#2124). A test that duplicates the rule passes even when the runtime stops
    applying it, which is the decorative-pin failure mode (#1859).

    Returns whether a task was started.
    """
    tick_interval = getattr(view_class, "tick_interval", None)
    if not tick_interval:
        return False

    from .live_view import LiveView as _LV

    if view_class.handle_tick is _LV.handle_tick:
        return False

    consumer._tick_task = asyncio.create_task(consumer._run_tick(tick_interval))
    # The view this task ticks for, so a failed mount stops only its own task
    # (#3027). The runtime holds the mounting view when this runs.
    runtime = getattr(consumer, "_runtime", None)
    if runtime is None and isinstance(consumer, ViewRuntime):
        # SSE has no consumer: the runtime owns the tick (#3809).
        runtime = consumer
    try:
        consumer._tick_task._djust_tick_view = getattr(runtime, "view_instance", None)
    except AttributeError:
        # A custom task factory whose tasks take no new attributes: the
        # failed-mount hook then cancels whatever tick the consumer holds.
        pass
    return True


_DOCUMENT_HEAD_RE = re.compile(r"<head\b[^>]*>(.*?)</head\s*>", re.IGNORECASE | re.DOTALL)
_DOCUMENT_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)


def _head_title_inner(markup: Optional[str]) -> Optional[str]:
    """The raw inner text of the ``<title>`` inside ``markup``'s ``<head>``.

    Only the ``<head>`` is searched, so an SVG ``<title>`` in the body never
    counts."""
    if not markup:
        return None
    head = _DOCUMENT_HEAD_RE.search(markup)
    if head is None:
        return None
    match = _DOCUMENT_TITLE_RE.search(head.group(1))
    return None if match is None else match.group(1)


def document_title(html: Optional[str]) -> Optional[str]:
    """The text of the ``<title>`` inside rendered ``html``'s ``<head>``.

    Entities are decoded (the client assigns ``document.title``, a text sink)
    and whitespace is collapsed the way the browser does for a title.
    """
    inner = _head_title_inner(html)
    if inner is None:
        return None
    import html as html_lib

    text = " ".join(html_lib.unescape(inner).split())
    return text or None


def _view_document_source(view: Any) -> Optional[str]:
    """The view's page template source with inheritance flattened, or None."""
    inline = getattr(view, "template", None)
    if inline:
        return str(inline)
    template_name = getattr(view, "template_name", None)
    if not template_name:
        return None
    return template_source(template_name)


def template_source(template_name: str) -> str:
    """The source of ``template_name`` with inheritance flattened."""
    from django.template import loader

    source = loader.get_template(template_name).template.source
    if "{% extends" in source or "{%extends" in source:
        from ._rust import resolve_template_inheritance
        from .utils import get_template_dirs

        return str(resolve_template_inheritance(template_name, get_template_dirs()))
    return str(source)


def _queued_title(view: Any) -> bool:
    """Whether ``view`` has a ``page_title`` command waiting to be sent."""
    pending = getattr(view, "_pending_page_metadata", None)
    if not isinstance(pending, list):
        return False
    return any(isinstance(cmd, dict) and cmd.get("action") == "title" for cmd in pending)


def navigation_title_variables(view: Any) -> Tuple[str, ...]:
    """The variable names ``navigation_title`` would read for ``view``'s ``<title>``.

    Empty when the title has none, when the template has no ``<head><title>`` and
    when anything fails (the title is then left alone, as ``navigation_title``
    leaves it). A ``static_render`` view uses this to keep exactly those values of
    its last render when it drops the rest (#3626). Never raises.
    """
    try:
        inner = _head_title_inner(_view_document_source(view))
        if inner is None or "block.super" in inner or "{" not in inner:
            return ()
        from ._rust import extract_template_variables

        return tuple(extract_template_variables(inner))
    except Exception:  # noqa: BLE001 — losing the title is the fallback, not an error
        return ()


def navigation_title(view: Any) -> Optional[str]:
    """The ``<title>`` the destination page would have on a full load (#3036).

    ``live_redirect`` mounts the destination view and swaps only its
    ``dj-root``, so the tab kept the previous page's title unless the view set
    ``page_title``. This renders just the ``<title>`` element of the view's
    page template (``{% block title %}`` included) with the values the mount
    render just used, instead of rendering the whole page shell, which could
    run ``{% live_render %}`` and other tags with side effects.

    Conservative by design: returns ``None`` (the title is left alone, as it
    was before) when the template has no ``<head><title>``, when the title
    uses ``{{ block.super }}`` (flattened inheritance loses the parent block),
    when a variable it reads is not a value the view holds, or when anything
    fails. Never raises.
    """
    try:
        inner = _head_title_inner(_view_document_source(view))
        if inner is None or "block.super" in inner:
            return None
        if "{" not in inner:
            return document_title(f"<head><title>{inner}</title></head>")
        from ._rust import extract_template_variables, render_template
        from .serialization import normalize_django_value

        immutables = getattr(view, "_prev_context_immutables", None) or {}
        context: Dict[str, Any] = {}
        for name in extract_template_variables(inner):
            if name in immutables:
                context[name] = immutables[name]
            elif not name.startswith("_") and name in getattr(view, "__dict__", {}):
                value = view.__dict__[name]
                if callable(value):
                    return None
                context[name] = normalize_django_value(value)
            else:
                return None
        rendered = render_template(inner, context)
        return document_title(f"<head><title>{rendered}</title></head>")
    except Exception as exc:  # noqa: BLE001 — a title must never break navigation
        from ._exposure_diagnostics import log_failure_for

        # The title renders view values; its error text can carry them (ADR-038).
        log_failure_for(
            logger,
            (view,),
            exc,
            "navigation title unavailable for %s",
            type(view).__name__,
            level="debug",
            traceback=True,
        )
        return None


# ------------------------------------------------------------------ #
# Transport Protocol + adapters
# ------------------------------------------------------------------ #


@runtime_checkable
class Transport(Protocol):
    """Wire-level transport for a mounted LiveView session.

    Implementations: :class:`WSConsumerTransport`, :class:`SSESessionTransport`.

    The runtime requires only this minimal surface — ``send`` for ordinary
    frames, ``send_error`` for error envelopes, ``close`` for forced
    disconnect, plus ``session_id`` and ``client_ip`` for observability and
    rate-limiting. The ``websocket_utils._validate_event_security`` helper
    already calls ``send_error`` and ``close`` on its first argument; the
    same shape works for both WS and SSE.
    """

    @property
    def session_id(self) -> str:
        """Stable per-session identifier (for observability + rate-limiting)."""

    @property
    def client_ip(self) -> Optional[str]:
        """Resolved client IP, or ``None`` when unavailable."""

    async def send(self, data: Dict[str, Any]) -> None:
        """Send an ordinary outbound frame to the client."""

    async def send_error(self, error: str, **kwargs: Any) -> None:
        """Send an error envelope to the client."""

    async def close(self, code: int = 1000) -> None:
        """Force-disconnect the transport with the given close ``code``."""

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        """Return the wire ``version`` to stamp on a client-CHECKED render frame.

        Client-checked frames (``patch`` / ``html_update``) are validated by the
        client's ``clientVdomVersion === data.version - 1`` rule, so their version
        must come from the SAME monotonic source as ``mount`` / ``event`` for that
        transport — never the raw Rust render counter (#1858, the #1788
        parallel-path twin).

        - WS: returns ``consumer._next_version_armed(html)`` — the per-connection
          counter that ``handle_mount`` / ``handle_event`` use, AND arms
          ``request_html`` recovery to that version (#1788 / #1817).
        - SSE: returns ``rust_version`` unchanged (SSE has no consumer counter;
          its existing behavior is preserved).
        """

    def build_request(self) -> Optional[Any]:
        """Return the transport's real Django request, or ``None`` to synthesize.

        SSE-backed runtimes hold the REAL HTTP request that established the
        stream (with its authenticated ``request.user``, ``request.session``,
        cookies, and path). The pre-mount auth sequence
        (``run_pre_mount_auth`` → ``check_view_auth``) and the post-mount
        object-permission check (``enforce_object_permission``) all read
        ``request.user`` / ``get_object()`` off this request, so the runtime
        MUST mount against it — synthesizing a userless ``RequestFactory``
        request would deny every authenticated SSE view (#1887, ADR-022 Iter 1).

        - SSE: returns the real request captured at stream-GET.
        - WS: returns ``None`` — the runtime synthesizes from ``self.scope``
          (which carries the authenticated ``user`` + ``session``) as before.
        """
        return None

    async def on_view_mounted(self, view_instance: Any) -> None:
        """Stamp transport-specific identity + post-mount setup on the freshly-mounted view.

        Called by ``dispatch_mount`` once ``self.view_instance`` is set and the
        pre-mount auth sequence + on_mount hooks have admitted it, so a
        transport can attach its own back-references the way the legacy bespoke
        mount paths did. SSE stamps ``_sse_session_id`` / ``_sse_session`` (used
        for introspection + limits) and the real query string. WS performs the
        WS-only post-mount channel-layer wiring (server-push view group,
        db_notify groups; the presence group is joined later, after mount(), by
        ``on_mount_render_ready`` — #3202), the periodic ``_tick_task`` start,
        and the real-scope path/query-string stamps (ADR-022
        Iter 3 Phase 3.3b, Finding B residual). Async because the WS impl awaits
        ``channel_layer.group_add(...)``. Behavior-preserving extension hook — the
        runtime stays wire-blind (#1887, ADR-022 Iter 1 / #1919, ADR-022 Iter 3).
        """

    async def on_event_recorded(self, view: Any, snapshot: Any) -> None:
        """Push a freshly-captured time-travel :class:`EventSnapshot` to the client.

        Called by ``_dispatch_event_inner`` (and the component / sticky-child
        branches) immediately after ``record_event_end`` finalizes the snapshot,
        so the debug panel's Time Travel tab can incrementally populate its
        history without re-sending the entire buffer on every event (ADR-022 Iter
        2 Phase 2.2 — replaces the WS ``_maybe_push_tt_event`` direct send with a
        transport hook so the runtime stays wire-blind).

        - WS: serializes + DEBUG-gates the entry and emits a ``time_travel_event``
          frame (the verbatim ``_maybe_push_tt_event`` logic, websocket.py:5267).
        - SSE: a no-op — SSE has no time-travel debug panel surface today.

        Always no-ops when ``snapshot`` is ``None`` (time-travel disabled on the
        view or a guard returned early). Best-effort: a failure here must never
        break the event turn.
        """

    def on_handler_timing(self, view: Any, event_name: str, duration_ms: float) -> None:
        """Record per-handler execution time for percentile telemetry.

        ADR-022 Iter 2 Phase 2.3b (#1907, THE FLIP). The WS bespoke event handler
        recorded the handler duration via ``observability.timings.record_handler_timing``
        in TWO spots — the component path (websocket.py:3498) and the view path
        (websocket.py:3645) — right after the handler call. Once Phase 2.3b routes
        WS events through ``dispatch_event``, that telemetry must keep flowing, so
        the runtime calls this hook after the handler completes on the single-view
        render path (the component path's own port keeps its inline recording).

        - WS: forwards to ``record_handler_timing`` (the same global percentile
          registry the bespoke path populated), so a runtime-routed WS event keeps
          feeding ``djust_audit`` / debug-panel handler-timing stats.
        - SSE: a no-op — the legacy SSE event path never recorded handler timing,
          so adding it here would be a behavior change for SSE; kept WS-scoped.

        Best-effort by contract: implementations MUST swallow their own errors
        (telemetry must never disturb the event turn).
        """

    def on_render_emitted(
        self,
        view: Any,
        *,
        reason: str,
        version: int,
        event_name: Optional[str],
        html: Optional[str] = None,
        patch_count: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit the per-render observability the WS bespoke render branch owned.

        ``context`` (#1908 item 2): the rendered ``get_context_data()`` dict, when
        the caller has it in hand on the ``no_patches`` branch, so the WS impl can
        build the ``context_snapshot`` the bespoke ``_emit_full_html_update`` carried
        for the ``no_patches`` reason. DEBUG-tooling-only metadata — ``None`` on
        every other reason and on transports/callers that do not surface it (the
        signal's load-bearing fields are ``reason`` / ``version`` / ``event_name`` /
        ``html_size``, all unaffected).

        ADR-022 Iter 2 Phase 2.3b (#1907, THE FLIP). Called by ``_render_and_send``
        whenever a SINGLE-VIEW event render falls into the full-HTML (no-patch)
        branch — i.e. the runtime is about to emit an ``html_update`` instead of a
        ``patch`` because there were no VDOM patches, the view forced full HTML, or
        patch-compression discarded the patches. The WS bespoke handler emitted two
        things here that the runtime ``_render_and_send`` did not:

          1. The **DJE-053 ``logger.warning``** (websocket.py:4215-4237): a
             user-facing diagnostic warning when an event with an established VDOM
             baseline (``version > 1``) falls back to full HTML — it tells the
             developer their event listeners + DOM state may be lost and points at
             the dj-root / push_event / system-check remedies. This is the ONE
             residual that is production-visible (not DEBUG-gated) and MUST SURVIVE
             the flip (#1079). ``reason == "first_render"`` (``version <= 1``) emits
             a DEBUG log instead, matching the WS ``else`` branch (websocket.py:4239).
          2. The **``_emit_full_html_update`` signal** (websocket.py:4254 +
             3962/4074/4091/4129): a transport-agnostic observability signal the
             debug tooling subscribes to, carrying the reason
             (``first_render`` / ``no_patches`` / ``force_full_html`` /
             ``patch_compression``) + a context snapshot (for ``no_patches``).

        ``reason`` is one of ``first_render`` / ``no_patches`` / ``force_full_html``
        / ``patch_compression`` / ``static_render`` (a ``static_render`` view's
        full frame, #3626; no DJE-053). ``version`` is the RAW Rust render counter (NOT the
        wire version) — the DJE-053 gate keys on ``version > 1`` to distinguish a
        true first render from a diff failure, mirroring websocket.py:4215.

        - WS: emits the DJE-053 warning + the ``_emit_full_html_update`` signal
          verbatim (single source — the bespoke call sites are deleted by the flip).
        - SSE: a no-op — SSE has no debug-panel signal subscriber and the legacy SSE
          render path emitted neither, so this is WS-scoped (zero SSE behavior
          change).

        Best-effort by contract: implementations MUST swallow their own errors.
        Not called on the patch branch (patches emitted → no full-HTML fallback) nor
        the noop / skip-render branch (no render at all).
        """

    def on_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: Optional[str],
        event_ref: Optional[int] = None,
    ) -> None:
        """Decorate an outbound EVENT render frame with the DEBUG residuals (#1908).

        Called by ``_render_and_send`` for EVERY ``patch`` / ``html_update`` frame it
        emits on a user-event turn — IN-PLACE, just before ``transport.send(frame)``.
        Folds the three DEBUG-only / cosmetic residuals the bespoke WS ``_send_update``
        owned that the runtime render path (which sends via ``transport.send``
        directly) dropped after THE FLIP (#1907):

          1. the per-event ``_debug`` panel payload (DEBUG-
             gated) + the top-level ``timing`` / ``performance`` fields (gated on
             ``_should_expose_timing()`` = DEBUG or ``DJUST_EXPOSE_TIMING``); and
          3. the ``_current_event_name`` / ``_current_event_ref`` consumer attrs the
             bespoke handler set for its no-arg ``_dispatch_async_work()`` (cosmetic
             parity for any out-of-band consumer reader).

        PRODUCTION is byte-identical: all attached fields are DEBUG/timing-gated, so a
        prod-mode frame is unchanged (matching the bespoke path, where both were also
        absent in prod).

        - WS: attaches the gated ``_debug`` + ``timing`` / ``performance`` (verbatim
          ``_attach_debug_payload`` + ``_should_expose_timing`` shape from the deleted
          bespoke ``_send_update``) and stamps the consumer event attrs.
        - SSE / partial test transports: not implemented → the runtime's defensive
          ``getattr`` makes the call a no-op (SSE has no debug panel; zero change).

        Best-effort by contract: implementations MUST swallow their own errors — a
        debug-decoration failure must never break the event turn.
        """

    def event_context(self, view: Any) -> "contextlib.AbstractAsyncContextManager[None]":
        """Async context manager wrapping a single event handler+render turn.

        Establishes (on enter) and tears down (in ``finally``) the per-event
        serialization + observability scope that the WS bespoke event handler
        owns today, so a WS event routed through the runtime in the Phase 2.3
        final flip serializes against the WS-only tick / server-push / db-notify
        render loops IDENTICALLY (the #560 version-interleave guard).

        The runtime CANNOT own this lock: the render serialization is
        consumer-owned (``LiveViewConsumer._render_lock`` websocket.py:619 +
        ``_processing_user_event`` :622) and SHARED with the ``_run_tick`` /
        ``server_push`` / ``db_notify`` loops. A runtime-local lock would be a
        DIFFERENT object and could not serialize against ticks. So the WS
        transport BORROWS the consumer's existing lock via this hook (it does
        NOT create a new one); the dead runtime-local ``_render_lock`` is gone.

        - WS: ``await consumer._render_lock.acquire()``; set
          ``consumer._processing_user_event = True``; set the #1677 origin-channel
          contextvar; start a ``PerformanceTracker`` + the SQL ``capture_for_event``
          observability scope. On exit (``finally``): reset the origin token, clear
          ``_processing_user_event``, RELEASE the borrowed lock, stop the SQL
          capture + clear the tracker. Mirrors websocket.py:3393-3400 / 3150-3154
          (enter) and websocket.py:4311-4313 (exit).
        - SSE: holds the session render lock so event/background results cannot
          race with another POST replacing the page.
        """
        return contextlib.nullcontext()

    async def explicit_event_request(self, view: Any) -> Any:
        """Return a fresh trusted event request; unsupported transports fail closed."""
        raise NotImplementedError("Explicit event requests are not supported")

    async def recheck_event_auth(self, view: Any) -> bool:
        """Opt-in per-event auth re-check for a live event turn (#1777, T3).

        ADR-022 Iter 2 Phase 2.3a. Auth runs once at mount and the mount-time
        principal is cached on the session, so a user who logs out / loses a
        permission mid-session would keep dispatching events on the open
        connection until they reconnect. When ``LIVEVIEW_CONFIG['reauth_on_event']``
        is set AND the view declares ``login_required`` / ``permission_required``,
        this hook re-resolves the CURRENT principal and re-runs the view's auth
        check before the handler runs. Called by ``_dispatch_event_inner`` at the
        same point the WS bespoke handler does — after the view-mounted check,
        BEFORE the normal handler path.

        Returns ``True`` to allow the event to proceed, ``False`` to refuse it.
        On a ``False`` return the caller has ALREADY done nothing — it is the
        hook's responsibility to emit any client-visible redirect/error and to
        terminate the transport (matching the WS bespoke behavior: navigate to
        the login url + ``close(4403)``). The caller then clears ``view_instance``
        and aborts the event (see ``_dispatch_event_inner``).

        - WS: when the flag is set + the view requires auth, re-resolves the user
          from the scope session (``channels.auth.get_user``), reflects it onto
          ``view.request.user``, re-runs ``check_view_auth_lightweight``; on
          failure sends a ``navigate`` to the login url + ``close(4403)`` (verbatim
          from websocket.py:3193-3222) and returns ``False``. Fail-safe: any error
          (no session in scope, etc.) skips the re-check and returns ``True``.
        - SSE: when the flag is set + the view requires auth, re-runs
          ``check_view_auth_lightweight`` against the LIVE event-POST request
          (``session._event_request`` — the current POSTer's request.user, not the
          stale mount request); on failure sends an auth-error frame + ends the
          stream and returns ``False``. The owner-binding check (Finding #24) runs
          at the endpoint BEFORE dispatch, so this is the SSE analog of the WS
          mid-session deauth gate — a still-owning POSTer whose auth was revoked is
          refused. Fail-safe identical to WS.

        Default (Protocol): returns ``True`` (no re-check). A transport that does
        not implement this never blocks events.
        """
        return True

    # ------------------------------------------------------------------ #
    # Mount hooks (ADR-022 Iter 3 Phase 3.2, #1915)
    #
    # Phase 3.3a (#1917) wired these hooks into ``dispatch_mount``; Phase 3.3b
    # (#1919) routed WS mounts through it. ``LiveViewConsumer.RUNTIME_OWNED_VERBS``
    # includes "mount", and ``handle_mount`` delegates direct callers to the
    # same runtime path. WS implements consumer-specific behavior; SSE keeps
    # its no-op / raw-version behavior.
    # ------------------------------------------------------------------ #

    def on_view_instantiated(self, view: Any) -> None:
        """Stamp transport back-references on a freshly-instantiated view.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding B — ``view_instance``
        ownership INVERTS at mount: url_change/event mirror consumer→runtime,
        but mount CREATES the view, so every WS-only consumer attr that the
        bespoke ``handle_mount`` stamped right after instantiation must move
        into a transport hook that writes onto the consumer during the runtime
        mount). Called by ``dispatch_mount`` (Phase 3.3a) immediately after the
        view is instantiated, BEFORE auth / mount / render.

        - WS: sets ``view._ws_consumer`` (streaming support, websocket.py:2128),
          registers the view in the observability
          registry (websocket.py:2161-2167), and stashes the validated
          ``view._websocket_host`` / ``_websocket_secure`` (websocket.py:2268-2270).
        - SSE: a no-op — SSE has no consumer to back-reference; its identity
          stamp already lands in :meth:`on_view_mounted`.

        Live in ``dispatch_mount`` since Phase 3.3a (#1917), including WS
        mounts since Phase 3.3b (#1919).
        """

    def next_mount_version(self, html: Optional[str], rust_version: int = 1) -> int:
        """Return the wire ``version`` to stamp on the MOUNT frame.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding C). Mount ESTABLISHES the client
        VDOM baseline — it must NOT arm ``request_html`` recovery (a fresh mount
        has no prior frame to recover to). This is distinct from
        :meth:`next_client_version`, which arms recovery (``_next_version_armed``)
        because it stamps render-SEND frames (patch / html_update). The mount
        frame is client-checked too (the client sets ``clientVdomVersion =
        data.version`` on mount, ``static/djust/src/03-websocket.js:382``), so the
        version MUST come from the SAME monotonic per-connection source as
        ``event`` — just WITHOUT the recovery arm.

        ``rust_version`` is the raw ``render_with_diff()`` version (SSE's source);
        it mirrors :meth:`next_client_version`'s second arg so the runtime can hand
        every transport the same inputs. WS ignores it (returns the consumer
        counter); SSE returns it unchanged. The default preserves compatibility
        with single-argument callers.

        - WS: returns ``consumer._next_version()`` — the no-arm counter the
          mount path uses. It does NOT call
          ``_next_version_armed`` / ``_arm_recovery``, so ``_recovery_html`` stays
          ``None`` after a mount.
        - SSE: returns ``rust_version`` (the raw Rust ``render_with_diff()``
          version the runtime stamped inline before Phase 3.3a) — SSE has no
          consumer counter (the #1788 unification never reached SSE); ``html`` is
          accepted for signature parity and ignored.

        Wired into ``dispatch_mount`` (Phase 3.3a). The Protocol default raises so
        a transport that participates in mounts MUST override it.
        """
        raise NotImplementedError("next_mount_version is transport-specific.")

    async def on_mount_render_ready(self, view: Any, html: Optional[str]) -> Optional[str]:
        """Post-render mount hook: sticky preservation + ``sticky_hold`` frame.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding B residual). Called by
        ``dispatch_mount`` (Phase 3.3a) AFTER the initial render produced ``html``
        but BEFORE the mount frame is sent, so a transport can adjust the
        outgoing HTML and/or emit pre-mount frames. Returns the (possibly
        adjusted) HTML the caller should use for the mount frame.

        - WS: on the ``live_redirect`` path (when the consumer staged a
          ``_sticky_preserved`` dict), scans the rendered HTML for surviving
          ``[dj-sticky-slot]`` ids (``_find_sticky_slot_ids``), re-registers the
          survivors onto the new parent (``view._register_child``), and emits the
          ``sticky_hold`` frame BEFORE the mount frame so the client's
          ``reattachStickyAfterMount`` reconciliation is authoritative
          (websocket.py:2080-2082 reset + 2836-2903 emit). Returns ``html``
          unchanged (sticky preservation adjusts registration + emits a frame,
          not the mount HTML).
        - SSE: returns ``html`` unchanged (no sticky / live_redirect surface).

        Live in ``dispatch_mount`` since Phase 3.3a (#1917); WS sticky
        preservation runs here since the Phase 3.3b mount flip (#1919).
        """
        return html

    async def finalize_mount_auth(self, view: Any, verdict: Any) -> None:
        """Apply the transport-level finalization of a BLOCKING mount-auth verdict.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding E — auth verdict→frame drift).
        The runtime's ``_check_auth`` / ``run_on_mount_hooks`` paths already SEND
        the verdict frame in the runtime's shape (``{type:error, error:...}`` for
        a permission denial, ``{type:navigate, to:...}`` for a login/hook
        redirect) and clear ``view_instance``. The runtime's
        ``_finalize_mount_auth`` invokes this hook to apply the WS-specific
        socket ``close(4403)`` after sending the verdict frame.

        ``verdict`` is a marker describing the kind of block. The two redirect
        verdicts (login-redirect at websocket.py:2347-2370 and the
        ``run_on_mount_hooks`` redirect at 2389-2400) MUST gate the close on
        ``not consumer._mounting_in_batch`` — a batched login-required view
        reports as ``navigate[]`` and closing would kill the shared socket's
        sibling mounts (#291 / #1780; ``test_ws_auth_close_socket.py`` is the
        net). A permission denial (websocket.py:2344-2346) closes unconditionally
        in the WS bespoke path.

        - WS: ``await consumer.close(code=4403)``, gated on
          ``not consumer._mounting_in_batch`` for the redirect verdicts (the
          ``_mounting_in_batch`` flag is exposed via :attr:`mounting_in_batch`).
        - SSE: no socket close — SSE has no persistent socket to drop; the
          verdict frame (error / navigate) the runtime already sent is the
          SSE-side finalization. A no-op here.

        Wired through ``_finalize_mount_auth`` in Phase 3.3a (#1917); live WS
        mounts use it since the Phase 3.3b mount flip (#1919).
        """

    @property
    def mounting_in_batch(self) -> bool:
        """Whether this transport is mid-``mount_batch`` on a shared socket.

        ADR-022 Iter 3 Phase 3.2 (#1915, #291 / #1780). Exposes the consumer's
        ``_mounting_in_batch`` flag through the transport seam so
        :meth:`finalize_mount_auth` can gate the ``close(4403)`` without reaching
        into a WS-specific attr. Default ``False`` (single mount).
        """
        return False


def _joins_presence_group(view: Any) -> bool:
    """Whether a mounted view joins a presence group (#3202).

    Any view with a ``get_presence_key`` does, except one whose only source of
    it is ``TenantMixin`` (tenant-scoped, but with no presence to broadcast):
    reading its key would cost a thread hop per mount for a group nothing
    sends to.
    """
    if not hasattr(view, "get_presence_key"):
        return False
    from .presence import PresenceMixin

    if isinstance(view, PresenceMixin):
        return True
    tenant_mixin_module = sys.modules.get("djust.tenants.mixin")
    tenant_mixin = getattr(tenant_mixin_module, "TenantMixin", None)
    if tenant_mixin is not None and isinstance(view, tenant_mixin):
        # Join if any class other than TenantMixin defines get_presence_key,
        # wherever it sits in the MRO (TenantMixin's own calls super()).
        return any(
            "get_presence_key" in vars(klass)
            for klass in type(view).__mro__
            if klass is not tenant_mixin
        )
    return True


async def _stored_scope_user(scope: Any) -> Any:
    """The socket's user as the session store holds it NOW.

    ``channels.auth.get_user(scope)`` reads ``scope["session"]``, the session
    object loaded when the socket connected: its data stays cached for the
    life of the connection. A sign-out in another tab (or over HTTP) deletes
    the stored session, yet the cached copy still names the user, so a
    per-event re-check against it never sees the sign-out. Re-read the
    session by its key from the configured engine; a deleted or expired
    session loads empty and resolves to ``AnonymousUser``. Without a session
    key there is nothing stored to re-read, so the scope's own answer stands.
    """
    from channels.auth import get_user
    from django.conf import settings

    session = scope.get("session")
    key = getattr(session, "session_key", None)
    if not key:
        return await get_user(scope)
    from importlib import import_module

    store = import_module(settings.SESSION_ENGINE).SessionStore(key)
    return await get_user({**scope, "session": store})


def _stored_request_user(request: Any) -> Any:
    """``request`` as the session store holds its principal NOW (SSE server turns).

    The SSE twin of :func:`_stored_scope_user`: a copy of the mount request whose
    session is re-read by key from the configured engine and whose user is
    resolved from it, so a sign-out elsewhere (the stored session is gone) or a
    deactivated account resolves to ``AnonymousUser``. A request with no session
    key has nothing stored to re-read, so it is returned as it is.
    """
    from copy import copy
    from importlib import import_module

    from django.conf import settings
    from django.contrib.auth import get_user

    session = getattr(request, "session", None)
    key = getattr(session, "session_key", None)
    if not key:
        return request
    fresh = copy(request)
    fresh.session = import_module(settings.SESSION_ENGINE).SessionStore(key)
    fresh.user = get_user(fresh)
    return fresh


class WSConsumerTransport:
    """Transport adapter wrapping ``LiveViewConsumer``.

    Forwards all outbound frames through ``send_json`` so existing client
    JSON-mode behavior is preserved verbatim.
    """

    #: ``Turn.transport`` for turns this transport carries (ADR-044).
    turn_transport = "ws"

    def __init__(self, consumer: Any):
        self._consumer = consumer

    @property
    def mount_turn_source(self) -> Optional[str]:
        """``"navigation"`` while a ``live_redirect_mount`` mounts its destination."""
        return "navigation" if getattr(self._consumer, "_live_redirect_mounting", False) else None

    @property
    def session_id(self) -> str:
        return getattr(self._consumer, "session_id", None) or ""

    @property
    def client_ip(self) -> Optional[str]:
        return getattr(self._consumer, "_client_ip", None)

    # The following pass-throughs let existing helpers (e.g.
    # ``_validate_event_security``) treat the transport interchangeably
    # with the consumer they used to receive directly.
    @property
    def _client_ip(self) -> Optional[str]:
        return self.client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        capture = getattr(self._consumer, "_capture_recovery_contracts", None)
        if callable(capture):
            capture(data)
        await self._consumer.send_json(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        await self._consumer.send_error(error, **kwargs)

    async def close(self, code: int = 1000) -> None:
        await self._consumer.close(code=code)

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        """Stamp the consumer-owned wire version + arm recovery (#1858 / #1788 / #1817).

        Routes runtime-emitted client-checked frames (``url_change`` patch /
        html_update) through the SAME ``_next_version_armed`` helper ``handle_event``
        uses, so the wire version stays monotonic with the mount baseline and a later
        ``request_html`` recovery serves the matching version. ``html`` MUST be the full
        PRE-STRIP HTML returned by ``render_with_diff()`` (see
        ``LiveViewConsumer._next_version_armed``). ``rust_version`` is ignored on WS.
        """
        return int(self._consumer._next_version_armed(html))

    def build_request(self) -> Optional[Any]:
        """WS has no captured HTTP request — the runtime synthesizes from
        ``self.scope`` (carrying the authenticated user + session)."""
        return None

    async def on_view_mounted(self, view_instance: Any) -> None:
        """WS post-mount channel-layer wiring + tick + flags (#1919, Finding B residual).

        Verbatim fold of the WS bespoke ``handle_mount`` post-instantiation setup
        block (websocket.py:2148-2217 + the per-mount ``_sticky_auto_reattached``
        reset at websocket.py:2082) that ``dispatch_mount`` did NOT carry pre-flip.
        The runtime calls this hook AFTER instantiation + back-refs AND after the
        pre-mount auth sequence + on_mount hooks have admitted the view, BEFORE
        state restore / mount(). A refused or redirected mount therefore joins no
        channel-layer group. It writes onto the CONSUMER (the runtime→consumer
        ownership direction at mount, Finding B):

          * real-scope path/query-string stamps for path-aware VDOM cache keys
            (websocket.py:2153-2156) — the runtime set ``_websocket_path =
            page_url`` + ``_websocket_query_string = ""``; the WS bespoke path uses
            the handshake ``scope`` values, so overwrite them here for parity;
          * server-push view group join (``view_group_name`` + ``group_add``,
            websocket.py:2172-2174);
          * NOT the presence group: a templated ``presence_key`` reads
            attributes mount() sets, so ``on_mount_render_ready`` joins it
            after mount() / restore (#3202);
          * db_notify group joins for the class-level ``_listen_channels``
            (websocket.py:2190-2200). This runs PRE-mount(), so channels
            ``listen()`` adds in mount() are joined later, by
            ``on_mount_render_ready`` (#2962);
          * periodic tick task start when the subclass overrides ``handle_tick``
            (websocket.py:2202-2208).

        Async because ``channel_layer.group_add`` is a coroutine. Mirrors the
        bespoke try/except envelope shape (a setup failure surfaces the same way
        the bespoke instantiation try/except did — see ``dispatch_mount``'s
        instantiation guard, which wraps this hook's call site is the
        instantiation block; here we keep the per-step ``try/except`` the bespoke
        path used for presence + db_notify).
        """
        consumer = self._consumer

        # Reset the per-mount sticky auto-reattach tracker (websocket.py:2082): each
        # mount starts with an empty set; the template tag pushes ids onto it as it
        # claims survivors. The live_redirect path also resets it before calling the
        # shim (websocket.py:3768), but a plain mount needs the reset too — and this
        # hook fires for EVERY runtime mount, so it is the single source post-flip.
        consumer._sticky_auto_reattached = set()

        # Real-scope path + query string for path-aware VDOM cache keys
        # (websocket.py:2153-2156). The runtime stamped ``page_url`` + ``""``; the
        # WS bespoke path sources these from the handshake ``scope`` so /emails/ and
        # /emails/?sender=1 get separate VDOM caches. Overwrite for parity.
        scope = getattr(consumer, "scope", None) or {}
        view_instance._websocket_path = scope.get("path", "/")
        view_instance._websocket_query_string = scope.get("query_string", b"").decode("utf-8")

        # The dotted view path is the client-supplied frame value
        # (``data["view"]``), stashed on the view by ``dispatch_mount`` as
        # ``_djust_mount_view_path``. The bespoke path keyed the server-push group
        # name on this EXACT string (websocket.py:2172-2174), and external
        # broadcasters (``apush_to_view("app.MyView")``) string-mangle the same
        # value — so they MUST agree byte-for-byte. The consumer also stashes it as
        # ``_view_path`` (websocket.py:2172) for server-push introspection.
        dotted = getattr(view_instance, "_djust_mount_view_path", None) or ""
        consumer._view_path = dotted

        # Join per-view channel group for server-push (websocket.py:2169-2174).
        from .push import view_group_name

        consumer._view_group = view_group_name(dotted)
        await consumer.channel_layer.group_add(consumer._view_group, consumer.channel_name)

        # The presence group is joined AFTER mount() / session restore, by
        # ``_join_presence_group`` from ``on_mount_render_ready`` (#3202): a
        # templated ``presence_key`` ("chat:{room}") interpolates attributes
        # mount() sets, so reading it here joined the unformatted group. The
        # groups a previous view joined were left by its teardown
        # (disconnect / live_redirect), so nothing is reset here.

        # Join db_notify groups for every channel the view subscribed to via
        # NotificationMixin.listen() (websocket.py:2186-2200). Addressed
        # per-channel (djust_db_notify_<channel>) so a NOTIFY on one channel never
        # fans out to views listening on another. This runs BEFORE mount(), so it
        # only sees class-level ``_listen_channels``; channels ``listen()`` adds in
        # mount(), on a session restore, or in a later handler are joined by
        # ``_join_listen_channels`` from ``on_mount_render_ready`` and at the end
        # of every event turn (#2962).
        consumer._db_notify_channels = set()
        await self._join_listen_channels(view_instance)

        # Start periodic tick if the subclass overrides handle_tick
        # (websocket.py:2202-2208).
        view_class = type(view_instance)
        maybe_start_tick_task(consumer, view_class)

    async def _join_listen_channels(self, view: Any) -> None:
        """Join the ``djust_db_notify_<channel>`` group for every channel the
        view listens on that this consumer has not joined yet (#2962).

        ``NotificationMixin.listen()`` only records the channel and starts the
        process listener; the group join is transport work. It is idempotent
        (already-joined channels are skipped), so it is safe to call after
        mount() and after every event turn. A join failure is logged and the
        channel is retried on the next call.
        """
        consumer = self._consumer
        listen_channels = getattr(view, "_listen_channels", None)
        if not listen_channels:
            return
        joined = getattr(consumer, "_db_notify_channels", None)
        if not isinstance(joined, set):
            joined = set()
            consumer._db_notify_channels = joined
        for ch in sorted(set(listen_channels) - joined):
            try:
                await consumer.channel_layer.group_add(
                    f"djust_db_notify_{ch}", consumer.channel_name
                )
                joined.add(ch)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error joining db_notify group for %s: %s", ch, e)

    async def _join_presence_group(self, view: Any) -> None:
        """Join the presence group of the view's FORMATTED presence key (#3202).

        Called from ``on_mount_render_ready``, once mount() (or a session
        restore) has set the attributes a templated ``presence_key``
        interpolates. ``broadcast_to_presence`` sends to the group of
        ``get_presence_key()``, so this must read the key after mount() too.
        The group is recorded in ``consumer._presence_groups`` (a
        ``mount_batch`` can join several; ``_presence_group`` is the latest),
        and ``leave_presence_groups`` discards every one of them on
        disconnect and on a ``live_redirect`` teardown.

        ``get_presence_key`` is application code that may touch the database,
        so it runs on the session's thread. A failure is logged and the
        session joins no presence group.
        """
        from .presence import PresenceManager

        if not _joins_presence_group(view):
            return
        consumer = self._consumer
        try:
            from .sync_turn import sync_step

            presence_key = await sync_to_async(sync_step(view, "hook", view.get_presence_key))()
            group = PresenceManager.presence_group_name(presence_key)
            joined = getattr(consumer, "_presence_groups", None)
            if not isinstance(joined, set):
                joined = set()
                consumer._presence_groups = joined
            if group not in joined:
                await consumer.channel_layer.group_add(group, consumer.channel_name)
                joined.add(group)
            consumer._presence_group = group
        except Exception as e:  # noqa: BLE001
            from ._exposure_diagnostics import log_failure

            log_failure(logger, e, "Error setting up presence group: %s", e, level="warning")

    async def _sync_push_scopes(self, view: Any) -> None:
        """Match the consumer's scoped-push groups to ``view.push_scope`` (#3004).

        See ``push.sync_push_scope_groups``: idempotent, joins new scopes and
        leaves dropped ones, logs (never raises) on a bad value or a layer
        error.
        """
        from .push import sync_push_scope_groups

        await sync_push_scope_groups(self._consumer, view)

    async def on_event_recorded(self, view: Any, snapshot: Any) -> None:
        """Emit the DEBUG-gated ``time_travel_event`` frame for WS.

        Delegates to the consumer's existing ``_maybe_push_tt_event`` (the
        verbatim DEBUG-gate + size-cap + ``__components__`` mirror logic at
        websocket.py:5267), so the runtime port and the legacy WS
        ``_handle_event_inner`` path share ONE implementation of the frame shape
        (the #1646 cure — Phase 2.3 deletes the WS-side call sites but keeps this
        single source). No-op when the consumer lacks the helper."""
        push = getattr(self._consumer, "_maybe_push_tt_event", None)
        if push is not None:
            await push(view, snapshot)

    def on_handler_timing(self, view: Any, event_name: str, duration_ms: float) -> None:
        """Forward handler timing to the global percentile registry (#1907).

        Verbatim port of the WS bespoke view-path recording (websocket.py:3645):
        ``record_handler_timing(view_class, event_name, handler_ms)``. Best-effort —
        a telemetry failure must never disturb the event turn, so the import +
        record is wrapped (mirroring the bespoke ``try/except Exception: pass``)."""
        try:
            from djust.observability.timings import record_handler_timing

            record_handler_timing(view.__class__.__name__, event_name, duration_ms)
        except Exception:  # noqa: BLE001 — telemetry must never break the event turn
            pass

    def on_render_emitted(
        self,
        view: Any,
        *,
        reason: str,
        version: int,
        event_name: Optional[str],
        html: Optional[str] = None,
        patch_count: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit the DJE-053 warning + ``_emit_full_html_update`` signal (#1907).

        Verbatim fold of the WS bespoke render-branch observability the flip moves
        off ``_handle_event_inner``:

          * DJE-053 ``logger.warning`` for the ``version > 1`` no-patch fallback
            (websocket.py:4215-4237) — the production-visible developer diagnostic
            that MUST SURVIVE the flip (#1079). ``version <= 1`` (a genuine first
            render) logs at DEBUG instead, matching websocket.py:4239.
          * The ``_emit_full_html_update`` signal carrying ``reason`` +
            (for ``no_patches``) a context snapshot — the single source now that
            the bespoke call sites (websocket.py:4074/4091/4129/4254) are deleted.

        Best-effort: a telemetry/signal failure must never break the event turn."""
        from .websocket import _emit_full_html_update

        # The WS bespoke DJE-053 warning fires for EVERY no-patch html_update frame
        # whose Rust version is > 1 — including a deliberate ``_force_full_html``
        # (the bespoke ``force_full_html`` branch sets ``patches = None`` then falls
        # into the SAME no-patches ``else`` arm that logs DJE-053, websocket.py:4087
        # → 4215). Reproduce that exactly: the warning gate is "this frame carried no
        # patches AND the view had an established VDOM baseline", i.e. NOT a
        # first_render and NOT a patch-compression fallback (which had patches).
        _emits_dje053 = reason in ("no_patches", "force_full_html") and version > 1
        try:
            if _emits_dje053:
                template = getattr(view, "template_name", None) or "<inline template>"
                logger.warning(
                    "[djust] Event '%s' on %s fell back to full HTML update "
                    "(DJE-053). Template: %s. "
                    "VDOM diff returned no patches — this may "
                    "cause event listeners and DOM state to be lost. "
                    "Debugging steps: "
                    "(1) Verify your template has <div dj-root> wrapping "
                    "all dynamic content. "
                    "(2) If this event only updates client-side state, use "
                    "push_event + _skip_render = True instead. "
                    "(3) Run with DJUST_VDOM_TRACE=1 for detailed diff output. "
                    "(4) Run 'python manage.py check --tag djust' to detect "
                    "common configuration issues. "
                    "See: https://djust.org/errors/DJE-053",
                    sanitize_for_log(event_name or ""),
                    view.__class__.__name__,
                    sanitize_for_log(str(template)),
                )
            elif reason == "first_render":
                logger.debug("[WebSocket] First render, sending full HTML update.")
        except Exception:  # noqa: BLE001 — diagnostic logging must never break the turn
            logger.debug("DJE-053 diagnostic emit failed", exc_info=True)

        # Emit the transport-agnostic full-HTML-update signal. The signal subscriber
        # is dev tooling; a failure here must never disturb the event turn. The
        # ``html`` passed in is the rendered (pre-strip) full HTML so ``html_size`` +
        # ``html_snippet`` match the WS bespoke emit (websocket.py:4254-4269). The
        # ``no_patches`` context snapshot (#1908 item 2): the WS bespoke path captured
        # the ``get_context_data()`` dict it had in hand and passed it as
        # ``context_snapshot`` for the ``no_patches`` reason. ``_render_and_send`` now
        # threads that same dict in via ``context`` (only on the no-patch branch, only
        # when it had to call ``get_context_data()`` anyway — no extra render-cost),
        # so the runtime path carries the snapshot the bespoke path did. It is
        # DEBUG-tooling-only metadata (the signal's load-bearing fields are ``reason``
        # / ``version`` / ``event_name`` / ``html_size``); ``None`` everywhere else.
        # ``_previous_html`` is read-only (never assigned in production), so
        # ``previous_html_snippet`` is inert on both paths.
        try:
            from .websocket import _build_context_snapshot
            from ._exposure import explicit_debug_projection

            html_for_snapshot = getattr(view, "_previous_html", None)
            explicit_debug = explicit_debug_projection(view)
            context_snapshot = (
                (explicit_debug if explicit_debug is not None else _build_context_snapshot(context))
                if reason == "no_patches" and context is not None
                else None
            )
            _emit_full_html_update(
                view,
                reason,
                event_name,
                html,
                version,
                context_snapshot=context_snapshot,
                patch_count=patch_count,
                html_snippet=(html[:500] if reason == "no_patches" and html else None),
                previous_html_snippet=(
                    html_for_snapshot[:500]
                    if reason == "no_patches" and html_for_snapshot
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 — observability signal must never break the turn
            from ._exposure_diagnostics import log_failure

            # full_html_update uses send(), so application receivers'
            # exceptions arrive here.
            log_failure(
                logger, exc, "full-HTML-update signal emit failed", level="debug", traceback=True
            )

    def on_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: Optional[str],
        event_ref: Optional[int] = None,
    ) -> None:
        """Fold the DEBUG event-render residuals onto a runtime-emitted WS frame (#1908).

        Verbatim fold of the three residuals the bespoke ``_send_update`` owned (now
        deleted for the event path) that the runtime ``_render_and_send`` dropped:

          1. top-level ``timing`` / ``performance`` — gated on ``_should_expose_timing()``
             (DEBUG or ``DJUST_EXPOSE_TIMING``), exactly the bespoke gate
             (websocket.py:1407-1411); ``performance`` is the borrowed
             ``PerformanceTracker`` summary established by ``event_context``; and
          1b. the per-event ``_debug`` panel payload, via the consumer's existing
             ``_attach_debug_payload`` (its own DEBUG gate,
             websocket.py:1627); plus
          3. the cosmetic ``_current_event_name`` / ``_current_event_ref`` consumer
             attrs the bespoke handler set for its no-arg ``_dispatch_async_work()``.

        PRODUCTION byte-identical: ``_should_expose_timing()`` is False and
        ``_attach_debug_payload`` early-returns when ``DEBUG`` is False, so a prod
        frame is unchanged (both were absent in prod on the bespoke path too). The
        consumer event attrs are internal-only (never serialized).

        Best-effort: any failure here is swallowed — a debug decoration must never
        break the event turn.
        """
        consumer = self._consumer
        # (3) cosmetic parity: stamp the consumer event attrs the bespoke handler set.
        # Internal-only — never serialized onto the frame; read by the consumer's
        # ``_dispatch_async_work`` (not on the runtime event path, but kept for any
        # out-of-band consumer reader).
        try:
            consumer._current_event_name = event_name
            consumer._current_event_ref = event_ref
        except Exception:  # noqa: BLE001 — cosmetic attr stamp must never break the turn
            pass

        try:
            from .websocket import _should_expose_timing

            # (1) Performance summary from the borrowed tracker (event_context set it
            # current); mirrors the bespoke ``tracker.get_summary()`` feed.
            from .performance import PerformanceTracker

            tracker = PerformanceTracker.get_current()
            performance = tracker.get_summary() if tracker is not None else None

            # Top-level timing/performance — production-gated identically to the
            # bespoke ``_send_update`` (websocket.py:1402-1411). ``timing`` carries the
            # render duration the runtime stamped on the frame under ``_timing_render_ms``
            # (popped here so it never leaks onto the wire when the gate is closed).
            render_ms = frame.pop("_timing_render_ms", None)
            # ADR-032 D4: ``scope == "component"`` marks a scoped patch so the
            # debug panel and ``benchmark_event`` can tell it from a page render.
            scope = frame.pop("_timing_scope", None)
            if _should_expose_timing():
                if render_ms is not None:
                    frame["timing"] = {"render": render_ms}
                    if scope is not None:
                        frame["timing"]["scope"] = scope
                if performance:
                    frame["performance"] = performance

            # (1b) The per-event ``_debug`` panel payload (DEBUG + panel-active gated
            # inside the helper). Reuse the consumer's existing implementation so the
            # runtime path and the still-bespoke tick/broadcast/async paths share
            # ONE ``_debug`` shape (#1646). ``performance`` rides into ``_debug`` too,
            # matching the bespoke ``_attach_debug_payload(response, event_name, performance)``.
            attach = getattr(consumer, "_attach_debug_payload", None)
            if attach is not None:
                attach(frame, event_name, performance)
        except Exception:  # noqa: BLE001 — debug decoration must never break the event turn
            logger.debug("on_event_frame debug decoration failed", exc_info=True)

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any) -> AsyncIterator[None]:
        """Borrow the consumer's render-lock + origin + observability for one event.

        ENTER mirrors the WS bespoke ``_handle_event_inner`` setup VERBATIM:

          * ``await consumer._render_lock.acquire()`` — the EXISTING consumer lock
            (websocket.py:3393), NOT a new one, so the runtime serializes against
            the WS-only ``_run_tick`` / ``server_push`` / ``db_notify`` render
            loops (the #560 version-interleave guard);
          * ``consumer._processing_user_event = True`` (websocket.py:3394) so ticks
            yield priority to user interactions;
          * set the #1677 origin-channel contextvar to the consumer's
            ``channel_name`` (websocket.py:3398-3400) so push_to_view broadcasts
            this handler emits skip this session's own redundant self-broadcast;
          * start a ``PerformanceTracker`` + the SQL ``capture_for_event`` scope
            (websocket.py:3150-3154 + 3469-3475) for observability.

        EXIT (``finally``) mirrors websocket.py:4311-4313: reset the origin token,
        clear ``_processing_user_event``, RELEASE the borrowed lock, then close the
        SQL-capture scope + restore the task-local tracker. Separate consumer
        tasks cannot overwrite each other's tracker; nested scopes restore the
        enclosing tracker even on exception or cancellation (#3089).
        """
        consumer = self._consumer

        # Acquire render lock to serialize with tick renders (#560). The lock is
        # the consumer's EXISTING object (websocket.py:3393) — borrowed, never
        # re-created — so it serializes against the WS tick/push/notify loops.
        await consumer._render_lock.acquire()
        consumer._processing_user_event = True

        # Tag any push_to_view broadcasts this handler emits with the originating
        # channel, so this same session skips its OWN redundant self-broadcast
        # (#1677). Reset in the finally below (websocket.py:3398-3400 / 4311).
        from djust import push as _djust_push

        _origin = getattr(consumer, "_origin_token", None)
        # Scoped to this turn: work the handler starts does not keep the tag
        # once the turn is over (the pushes it sends later are news here).
        _origin_turn = _djust_push.originating_turn(
            _origin() if callable(_origin) else getattr(consumer, "channel_name", None)
        )
        _origin_turn.__enter__()

        # Observability: comprehensive performance tracking (websocket.py:3150-3154)
        # + per-handler SQL-query capture (websocket.py:3469-3475). Both are
        # transport-owned scopes the runtime borrows so a runtime-routed WS event
        # populates the same debug/perf surfaces the bespoke path did.
        from djust.observability.sql import capture_for_event as _dj_sql_capture
        from djust.performance import PerformanceTracker

        tracker = PerformanceTracker()
        _tracker_token = PerformanceTracker.set_current(tracker)

        _sid = getattr(consumer, "session_id", None)
        # ``capture_for_event`` reads ``handler_name`` at enter, but the runtime
        # parses ``event_name`` INSIDE the wrapped body (after this CM has
        # entered), so it isn't available here. The session_id + event_id tags
        # are the load-bearing ones for query attribution; the per-event handler
        # name is omitted (the WS bespoke path tags it because its SQL scope wraps
        # only the handler call, where event_name is already known). See #1899.
        sql_scope = _dj_sql_capture(
            session_id=_sid,
            event_id=f"{_sid}:{time.perf_counter()}" if _sid else None,
            owner=view,  # ADR-038 D-d: params are redacted for a nonlegacy owner
        )
        sql_scope.__enter__()
        try:
            yield
            # A handler that called ``listen()`` joins its NOTIFY group now,
            # while the turn still holds the render lock (#2962).
            await self._join_listen_channels(view)
            # Likewise a handler that changed ``push_scope`` (#3004) -- unless
            # the view was replaced during the turn (a live_redirect does not
            # take the render lock): its scopes are not this socket's any more.
            if getattr(consumer, "view_instance", None) is view:
                await self._sync_push_scopes(view)
        finally:
            sql_scope.__exit__(None, None, None)
            PerformanceTracker.reset_current(_tracker_token)
            _origin_turn.__exit__(None, None, None)
            consumer._processing_user_event = False
            consumer._render_lock.release()

    async def recheck_event_auth(self, view: Any) -> bool:
        """WS per-event auth re-check — verbatim from websocket.py:3193-3222 (#1777).

        Re-resolves the user from the scope session, reflects it onto
        ``view.request.user`` (the mount request stored on the view), and re-runs
        the view's auth check. On failure: navigate to the login url + ``close(4403)``
        and return ``False``. On success return ``True``; on any error in the re-check,
        deny (fail closed).

        Gated on ``reauth_on_event`` + ``login_required``/``permission_required``
        so default views never pay the session read. Live since Phase 2.3b
        (#1907): ``LiveViewConsumer.RUNTIME_OWNED_VERBS`` routes ``event`` frames
        through ``dispatch_message`` → ``dispatch_event``; direct ``handle_event``
        calls also delegate to ``dispatch_event``. Its event body invokes this
        hook for legacy-exposure views before normal handler
        dispatch. ``check_view_auth_lightweight`` delegates to ``check_view_auth``,
        including an overridden ``check_permissions(request)`` hook.
        """
        from .config import config as djust_config

        if not (
            djust_config.get("reauth_on_event")
            and (
                getattr(view, "login_required", None) or getattr(view, "permission_required", None)
            )
        ):
            return True

        consumer = self._consumer
        try:
            from .auth.core import acheck_view_auth_lightweight

            fresh_user = await _stored_scope_user(consumer.scope)
            # The mount request is stored on the view (handle_mount:
            # ``view.request = request``), not on the consumer.
            request = getattr(view, "request", None)
            if request is None:
                # No request to re-check against — fail-safe skip (matches the WS
                # bespoke guard, which only re-checks when a request exists).
                return True
            request.user = fresh_user  # reflect current auth for the check + handler
            # An ``async def check_permissions`` is awaited on the loop.
            authorized = await acheck_view_auth_lightweight(view, request)
        except Exception as exc:  # noqa: BLE001 — fail CLOSED: an unverifiable check denies
            # Only the exception TYPE and view class are logged — never the
            # message, traceback or request data (log-exposure pin).
            exc_type = type(exc).__name__
            logger.warning(
                "reauth_on_event re-check raised %s on view %s (WS); denying the event",
                exc_type,
                type(view).__name__,
            )
            authorized = False
        if not authorized:
            from django.conf import settings as _dj_settings

            login_url = getattr(view, "login_url", None) or getattr(
                _dj_settings, "LOGIN_URL", "/accounts/login/"
            )
            # Best-effort refusal frames: the event is denied even if the peer is gone.
            # A view beside the page view is refused in its own container, and
            # only it goes (#3252); the page view's refusal is the page's.
            with contextlib.suppress(Exception):
                if not await self.send_view_refusal("login_required", login_url):
                    await consumer.send_json({"type": "navigate", "to": login_url})
            with contextlib.suppress(Exception):
                await consumer.close(code=4403)
            return False
        return True

    async def explicit_event_request(self, view: Any) -> Any:
        """Supply freshly loaded session authentication for explicit events."""
        from ._exposure_auth import fresh_socket_request

        return await sync_to_async(fresh_socket_request)(view)

    # ------------------------------------------------------------------ #
    # Mount hooks (ADR-022 Iter 3 Phase 3.2, #1915)
    # WS implementations extracted from the former bespoke ``handle_mount``.
    # Wired into ``dispatch_mount`` in Phase 3.3a (#1917), and live for WS
    # mounts since Phase 3.3b (#1919).
    # ------------------------------------------------------------------ #

    def on_view_instantiated(self, view: Any) -> None:
        """Stamp WS consumer back-references on the freshly-instantiated view.

        Verbatim fold of the WS bespoke ``handle_mount`` post-instantiation
        stamps (#1915, Finding B):

          * ``view._ws_consumer = consumer`` (streaming support, and the
            ``flush_push_events()`` lookup that lets ``@background`` handlers
            flush push_commands mid-execution);
          * observability ``register_view(session_id, view)`` (weakref registry;
            never break the connection, websocket.py:2161-2167);
          * ``view._websocket_host`` / ``view._websocket_secure`` from the
            validated handshake scope (websocket.py:2268-2270) so
            runtime-rebuilt requests carry the same host (#1646).
        """
        consumer = self._consumer
        view._ws_consumer = consumer
        # A view mounted beside the page view knows its slot (#3252).
        slot_target = getattr(consumer, "target_id", None)
        if isinstance(slot_target, str) and slot_target:
            view._djust_slot_target = slot_target

        # Observability registry (weakrefs — best-effort; must never break the
        # connection, mirroring websocket.py:2161-2167).
        try:
            from djust.observability.registry import register_view

            register_view(consumer.session_id, view)
        except Exception as e:  # noqa: BLE001 — observability must never break a WS connection
            logger.warning("Failed to register view in observability registry: %s", e)

        # Validated handshake host/scheme stash (websocket.py:2243-2270). The
        # bespoke path computes this from ``validated_host_from_scope(scope)`` and
        # stamps it onto the view so runtime-rebuilt requests carry the same host
        # (#1646). Compute from the SAME validator + scope here. ``(None, False)``
        # for absent/invalid Hosts (non-browser clients) so the runtime falls back
        # to RequestFactory's default — matching the bespoke ``if host:`` guard.
        from .websocket import validated_host_from_scope

        scope = getattr(consumer, "scope", None) or {}
        host, is_secure = validated_host_from_scope(scope)
        view._websocket_host = host
        view._websocket_secure = is_secure

    def next_mount_version(self, html: Optional[str], rust_version: int = 1) -> int:
        """Stamp the consumer-owned NO-ARM wire version for the mount frame (#1915, Finding C).

        Returns ``consumer._next_version()`` — the SAME monotonic per-connection
        counter the bespoke ``handle_mount`` uses (websocket.py:2746). Crucially
        this is NOT ``_next_version_armed`` / ``_arm_recovery``: a mount
        ESTABLISHES the client VDOM baseline and has no prior frame to recover to,
        so ``_recovery_html`` MUST stay unset after a mount (distinct from
        :meth:`next_client_version`, which arms recovery for render-SEND frames).
        ``html`` and ``rust_version`` are accepted for signature parity with the
        Protocol but are unused on WS (the no-arm consumer counter is the source).
        """
        return int(self._consumer._next_version())

    async def on_mount_render_ready(self, view: Any, html: Optional[str]) -> Optional[str]:
        """Sticky preservation + ``sticky_hold`` frame before the mount frame (#1915, Finding B residual).

        Verbatim fold of the WS bespoke sticky block (websocket.py:2836-2903).
        On the ``live_redirect`` path the consumer staged a ``_sticky_preserved``
        dict; this scans the just-rendered ``html`` for surviving
        ``[dj-sticky-slot]`` ids, re-registers survivors onto the new parent
        (``view._register_child``), updates ``consumer._sticky_preserved`` to the
        authoritative survivor set, and emits the ``sticky_hold`` frame BEFORE the
        mount frame (ordering matters — the client's ``reattachStickyAfterMount``
        must see the hold first). The ``_sticky_auto_reattached`` set (reset per
        mount at websocket.py:2082) records survivors the template tag already
        re-registered, so we don't double-register them.

        It first joins the NOTIFY groups for channels ``listen()`` added in
        mount() or a session restore (#2962), and the presence group of the
        view's formatted ``presence_key`` (#3202): this is the first transport
        hook after mount() on every admitted mount.

        Returns ``html`` unchanged: sticky preservation adjusts child
        registration + emits a frame, it does not rewrite the mount HTML. No-op
        (returns ``html``) when no stickys were staged.
        """
        from ._child_lifecycle import discard_sticky_child
        from ._exposure import uses_legacy_exposure
        from .websocket import _find_sticky_slot_ids

        consumer = self._consumer
        # mount() (or a session restore) has run and the view is admitted:
        # join the NOTIFY groups for channels ``listen()`` added (#2962), and
        # the scoped-push groups for the view's ``push_scope`` (#3004).
        await self._join_listen_channels(view)
        await self._sync_push_scopes(view)
        await self._join_presence_group(view)
        sticky_preserved = getattr(consumer, "_sticky_preserved", None)
        if not sticky_preserved:
            return html

        try:
            matched_ids = _find_sticky_slot_ids(html or "")
            survivors_final: Dict[str, Any] = {}
            auto_reattached: set[str] = getattr(consumer, "_sticky_auto_reattached", set())
            for sticky_id, child in list(sticky_preserved.items()):
                if sticky_id in auto_reattached:
                    # The tag already re-registered this survivor at render time;
                    # don't call _register_child again (it would ValueError), but
                    # keep it in survivors_final for an authoritative hold list.
                    survivors_final[sticky_id] = child
                elif sticky_id in matched_ids and uses_legacy_exposure(child):
                    # Explicit children must pass the tag's expected-class,
                    # inputs and request-identity checks. Bare slot markup
                    # cannot authorize their reattachment.
                    if hasattr(view, "_register_child"):
                        try:
                            view._register_child(sticky_id, child)
                            survivors_final[sticky_id] = child
                        except ValueError:
                            logger.warning(
                                "sticky_id %s collided with new child on reattach",
                                sticky_id,
                            )
                            discard_sticky_child(child)
                else:
                    # No slot on the new page (or an explicit child the tag
                    # did not reattach): drop it, waiters included (#3244).
                    discard_sticky_child(child)
            consumer._sticky_preserved = survivors_final
            await consumer.send_json(
                {
                    "type": "sticky_hold",
                    "views": list(survivors_final.keys()),
                }
            )
        except Exception:  # noqa: BLE001 — defensive: never break mount
            logger.exception("failed to emit sticky_hold frame before mount")
        return html

    async def finalize_mount_auth(self, view: Any, verdict: Any) -> None:
        """Close the socket (4403) after a blocking mount-auth verdict (#1915, Finding E).

        The runtime path already SENT the verdict frame (error / navigate) and
        cleared ``view_instance``; this adds the transport-level
        ``close(code=4403)`` the WS bespoke ``handle_mount`` performed
        (websocket.py:2344-2399).

        ``verdict`` is a marker string describing the block kind. ALL verdicts
        gate the ``close(4403)`` on ``not consumer._mounting_in_batch`` (#291 /
        #1780, #1922): inside a ``mount_batch`` the socket is SHARED across
        sibling mounts, so closing it on ANY single view's blocking verdict —
        login redirect OR object/permission denial — collaterally drops the
        siblings (the #291 multiplexed-path failure). The denial still holds
        regardless: the runtime already sent the verdict frame (error /
        navigate) and cleared ``view_instance`` BEFORE this hook runs, so the
        blocked view is not mounted; only the socket-level close is suppressed
        in the batch case (the denied view simply reports in ``failed[]`` /
        ``navigate[]``, exactly as the redirect case already did).

          * ``"permission_denied"`` → close GATED on ``not mounting_in_batch``
            (#1922 — was unconditional, websocket.py:2345; over-closed the
            shared socket on a batched object/perm denial);
          * ``"redirect"`` / ``"hook_redirect"`` → close GATED on
            ``not consumer._mounting_in_batch`` (websocket.py:2368 / 2398) so a
            batched login-required view does NOT drop the shared socket's sibling
            mounts (#291 / #1780).

        A SINGLE (non-batch) blocking mount still closes 4403 for every verdict
        (``mounting_in_batch`` is ``False`` outside a batch).
        """
        consumer = self._consumer
        # The refused mount leaves the consumer with no mounted view, so it also
        # leaves every channel-layer group it holds. This matters inside a
        # mount_batch, where the socket stays open: the post-mount object-permission
        # refusal runs after ``on_view_mounted`` joined the view's groups.
        await self._leave_view_groups()
        # #1922 / #291: gate the close on the batch flag for ALL verdicts so a
        # single denied/redirected view inside a shared-socket mount_batch does
        # not kill the sibling mounts. The denial itself is already enforced
        # upstream (verdict frame sent + view_instance cleared).
        if not self.mounting_in_batch:
            await consumer.close(code=4403)

    async def send_view_refusal(self, reason: str, to: Optional[str] = None) -> bool:
        """Report a refused view beside the page view to its own container (#3252).

        True when this transport runs such a view (the ``view_refused`` frame is
        sent, addressed to it) and the caller sends nothing else; False for the
        page view, whose refusal is the page's: an error, or a navigation.
        """
        consumer = self._consumer
        if not (isinstance(getattr(consumer, "target_id", None), str) and consumer.target_id):
            return False
        await consumer.send_refusal(reason, to)
        return True

    @property
    def capture_document_title(self) -> bool:
        """True while the consumer mounts a ``live_redirect`` destination
        (#3036), so the runtime records the page's document ``<title>``."""
        return bool(getattr(self._consumer, "_live_redirect_mounting", False))

    @property
    def capture_page_shell(self) -> bool:
        """True while the consumer mounts a ``live_redirect`` destination
        (#3036), so the mount frame carries its page-shell fingerprint."""
        return bool(getattr(self._consumer, "_live_redirect_mounting", False))

    async def on_mount_failed(self, view: Any) -> None:
        """Undo what the mount set up for a view whose mount then failed.

        Stops the tick task ``on_view_mounted`` started (#3027), leaves the
        view / presence / db_notify groups it joined and untracks the presence
        ``mount()`` may have tracked: the view is not mounted, so nothing may
        still reach it or announce it."""
        await self._stop_tick_of(view)
        await self._leave_view_groups()
        if getattr(view, "_presence_tracked", False):
            from ._child_lifecycle import untrack_view_presence

            await sync_to_async(untrack_view_presence)(view)

    async def _stop_tick_of(self, view: Any) -> None:
        """Only a task still ticking for THIS view is cancelled; a later mount
        on the socket owns its own task."""
        consumer = self._consumer
        task = getattr(consumer, "_tick_task", None)
        if task is None or task.done():
            return
        ticking_for = getattr(task, "_djust_tick_view", None)
        if ticking_for is not None and ticking_for is not view:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected: we just cancelled the tick task and only wait for it to
            # unwind.
            pass
        except Exception as exc:  # noqa: BLE001 — the loop logs its own errors
            from ._exposure_diagnostics import log_failure_for

            log_failure_for(
                logger,
                (view,),
                exc,
                "tick task ended with an error after a failed mount",
                level="debug",
                traceback=True,
            )
        if getattr(consumer, "_tick_task", None) is task:
            consumer._tick_task = None

    async def _leave_view_groups(self) -> None:
        """Leave the view / presence / db_notify groups the mount joined."""
        await leave_consumer_view_groups(self._consumer)

    @property
    def mounting_in_batch(self) -> bool:
        """Expose the consumer's ``_mounting_in_batch`` flag (#1915, #291)."""
        return bool(getattr(self._consumer, "_mounting_in_batch", False))


class SSESessionTransport:
    """Transport adapter wrapping ``SSESession``.

    Frames are pushed onto the session queue; the SSE stream generator
    drains them and writes ``data:`` lines to the client.
    """

    #: ``Turn.transport`` for turns this transport carries (ADR-044).
    turn_transport = "sse"

    def __init__(self, session: Any):
        self._session = session

    @property
    def turn_request(self) -> Any:
        """The live request for a turn: the event POST, else the stream GET."""
        session = self._session
        return getattr(session, "_event_request", None) or getattr(session, "_request", None)

    @property
    def mount_turn_source(self) -> Optional[str]:
        """``"navigation"`` while a ``live_redirect_mount`` replaces the page view."""
        return "navigation" if getattr(self._session, "_replacing_view", False) else None

    @property
    def mount_is_reconnect(self) -> bool:
        """An EventSource reconnect re-opened the stream with a known session id."""
        return bool(getattr(self._session, "_mount_reconnect", False))

    @property
    def session_id(self) -> str:
        return str(self._session.session_id)

    @property
    def client_ip(self) -> Optional[str]:
        return getattr(self._session, "_client_ip", None)

    @property
    def _client_ip(self) -> Optional[str]:
        return self.client_ip

    @property
    def capture_page_shell(self) -> bool:
        """True while the session mounts a ``live_redirect_mount`` destination
        (#3036), so the mount frame carries its page-shell fingerprint."""
        return bool(getattr(self._session, "_replacing_view", False))

    async def send(self, data: Dict[str, Any]) -> None:
        # SSESession.push is a sync method (it uses queue.put_nowait).
        self._session.push(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        await self._session.send_error(error, **kwargs)

    async def close(self, code: int = 1000) -> None:
        await self._session.close(code=code)

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        """SSE has no per-connection consumer counter — preserve the Rust version (#1858).

        SSE stamps the raw ``render_with_diff()`` version on its frames today (see
        ``sse.py``); the #1788 consumer-counter unification never reached SSE, so SSE
        clients are calibrated to the Rust counter. Returning it unchanged keeps SSE
        behavior identical. (If SSE ever adopts a consumer-owned counter, this is the
        single place to wire it — tracked alongside #1858.)
        """
        return rust_version

    def build_request(self) -> Optional[Any]:
        """Return the REAL HTTP request that established the SSE stream.

        The stream-GET view stashes the request on the session
        (``session._request``) before driving ``dispatch_mount`` so the runtime
        mounts against the authenticated victim/owner request — preserving the
        legacy ``_sse_mount_view`` behavior (real ``request.user`` / session /
        path for auth + object-perm) after convergence (#1887, ADR-022 Iter 1).
        Returns ``None`` if no request was stashed (defensive — falls back to
        the runtime's synthesized request).
        """
        return getattr(self._session, "_request", None)

    async def on_view_mounted(self, view_instance: Any) -> None:
        """Stamp SSE-transport identity on the mounted view (legacy parity).

        ``_sse_mount_view`` set ``session.view_instance`` (the endpoint-level
        "is this session mounted?" reference the ``/message/``
        guards read, plus SSE introspection) and ``_sse_session_id`` /
        ``_sse_session`` on the view, and the real ``_websocket_query_string``
        from the request. The runtime already sets ``_websocket_session_id`` /
        ``_websocket_path``; this adds the SSE-only attrs so nothing the legacy
        path exposed is lost (#1887, ADR-022 Iter 1). Setting
        ``session.view_instance`` is load-bearing: the runtime owns
        ``runtime.view_instance`` for dispatch, but the SSE endpoints gate on
        ``session.view_instance`` — the two must agree after a successful mount.
        """
        session = self._session
        session.view_instance = view_instance
        view_instance._sse_session_id = session.session_id
        view_instance._sse_session = session
        request = getattr(session, "_request", None)
        if request is not None:
            view_instance._websocket_query_string = request.META.get("QUERY_STRING", "")
        # The periodic tick, as the WebSocket starts it (#3809): the same
        # opt-in rule, run by this session's runtime.
        maybe_start_tick_task(session.runtime, type(view_instance))

    def client_backlogged(self) -> bool:
        """Whether the client has stopped reading server-driven frames (#3809).

        The session queue holds every frame until the stream writes it, and a
        producer never waits on it, so a stalled reader would grow it without
        bound. At ``_MAX_QUEUED_SERVER_FRAMES`` queued frames a tick is skipped
        and a push is deferred (see the runtime's back-pressure note)."""
        from . import sse as _sse

        return bool(self._session.queue.qsize() >= _sse._MAX_QUEUED_SERVER_FRAMES)

    def render_busy(self, view: Any) -> bool:
        """Whether a turn holds the session's render lock right now (#3809).

        A server push that finds the session busy is deferred, as the
        WebSocket's is, instead of waiting inside the caller."""
        return bool(self._session._render_lock.locked())

    async def on_mount_failed(self, view: Any) -> None:
        """A view whose mount failed is not the session's mounted view.

        ``on_view_mounted`` set ``session.view_instance``, which the event and
        message endpoints read; clear it, stop the tick it started (#3809), and
        untrack the presence ``mount()`` may have tracked."""
        session = self._session
        session.runtime.stop_server_work(view)
        if getattr(session, "view_instance", None) is view:
            session.view_instance = None
        if getattr(view, "_presence_tracked", False):
            from ._child_lifecycle import untrack_view_presence

            await sync_to_async(untrack_view_presence)(view)

    async def on_event_recorded(self, view: Any, snapshot: Any) -> None:
        """No-op for SSE — there is no time-travel debug-panel surface on the SSE
        transport today (the debug panel is a WS-only client feature). The
        runtime still RECORDS the snapshot into the view's buffer (server-side
        time-travel state is transport-agnostic); only the incremental client
        push is WS-specific."""
        return None

    def on_handler_timing(self, view: Any, event_name: str, duration_ms: float) -> None:
        """No-op for SSE (#1907).

        The legacy SSE event path never recorded per-handler percentile timing —
        ``record_handler_timing`` was a WS-only telemetry call. Forwarding it here
        would be a behavior change for SSE (new telemetry rows), so it stays
        WS-scoped: SSE drops the timing exactly as it always has."""
        return None

    def on_render_emitted(
        self,
        view: Any,
        *,
        reason: str,
        version: int,
        event_name: Optional[str],
        html: Optional[str] = None,
        patch_count: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """No-op for SSE (#1907).

        SSE has no debug-panel ``full_html_update`` signal subscriber and the legacy
        SSE render path emitted neither the DJE-053 warning nor the signal, so this
        WS-scoped fold is a no-op on SSE — zero SSE behavior change. (The DJE-053
        warning is a developer diagnostic for the WS VDOM-baseline-loss case; SSE's
        own render path retains its existing logging.) ``context`` (#1908) is ignored
        — the SSE path never built a context snapshot."""
        return None

    def on_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: Optional[str],
        event_ref: Optional[int] = None,
    ) -> None:
        """No-op for SSE (#1908).

        The DEBUG event-render residuals (``_debug`` panel payload, top-level
        ``timing`` / ``performance``, the ``_current_event_*`` consumer attrs) are a
        WS debug-panel surface; the legacy SSE event path never carried them, so this
        fold is a no-op on SSE — zero SSE behavior change."""
        return None

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any) -> AsyncIterator[None]:
        """Serialize event/results with SSE page replacement."""
        async with self._session._render_lock:
            yield

    async def recheck_event_auth(self, view: Any) -> bool:
        """SSE per-event auth re-check against the LIVE event-POST request (#1777).

        SSE parity for the WS mid-session deauth gate. The owner-binding check
        (Finding #24) already runs at the ``/message/`` endpoint
        BEFORE dispatch, so the POSTer is the session owner — but a still-owning
        principal whose ``login_required`` / ``permission_required`` posture was
        revoked mid-session would keep driving the view. When ``reauth_on_event``
        is set + the view requires auth, re-run ``check_view_auth_lightweight``
        against the CURRENT event-POST request (``session._event_request``, stamped
        by the SSE endpoint just before ``dispatch_event`` — the live POSTer's
        ``request.user``, NOT the stale mount request that ``build_request``
        returns). On failure: send an auth-error frame + end the stream and return
        ``False``. Fail closed: any error in the re-check denies and returns ``False``.

        LIVE for SSE today: SSE events route through ``dispatch_event`` →
        ``_dispatch_event_inner`` since Iter 1 (#1887), so this hook fires on the
        live SSE event path the moment ``reauth_on_event`` is enabled.
        """
        from .config import config as djust_config

        if not (
            djust_config.get("reauth_on_event")
            and (
                getattr(view, "login_required", None) or getattr(view, "permission_required", None)
            )
        ):
            return True

        session = self._session
        try:
            from .auth.core import acheck_view_auth_lightweight

            # The live event-POST request, stamped on the session by the SSE
            # endpoint right before dispatch. Falls back to the mount request
            # (``session._request``) defensively if the endpoint didn't stamp one
            # (e.g. a direct runtime.dispatch_event call) — the mount request
            # still carries a real user, so the re-check stays meaningful.
            request = getattr(session, "_event_request", None)
            if request is None:
                # A server-originated turn (tick, push, background result): no
                # POST is in flight. The mount request's user was resolved when
                # the stream opened and stays cached, so it cannot see a logout
                # or a deactivation since: re-read the session and the user, as
                # the WebSocket does (#3809).
                request = getattr(session, "_request", None)
                if request is None:
                    return True
                request = await sync_to_async(_stored_request_user)(request)
            # An ``async def check_permissions`` is awaited on the loop.
            authorized = await acheck_view_auth_lightweight(view, request)
        except Exception as exc:  # noqa: BLE001 — fail CLOSED: an unverifiable check denies
            # Only the exception TYPE and view class are logged — never the
            # message, traceback or request data (log-exposure pin).
            exc_type = type(exc).__name__
            logger.warning(
                "reauth_on_event re-check raised %s on view %s (SSE); denying the event",
                exc_type,
                type(view).__name__,
            )
            authorized = False
        if not authorized:
            # Best-effort refusal frames: the event is denied even if the stream is gone.
            with contextlib.suppress(Exception):
                # A view beside the page view is refused in its own container (#3252).
                if not await self.send_view_refusal("permission_denied"):
                    await session.send_error(
                        "Session is no longer authorized. Please reload the page.",
                        code="permission_denied",
                    )
            with contextlib.suppress(Exception):
                await session.close(code=4403)
            return False
        return True

    async def explicit_event_request(self, view: Any) -> Any:
        """Use the current owner-checked POST; never fall back to mount auth.

        A project without ``AuthenticationMiddleware`` gives the POST no user.
        The mount read its user from the session then, so the event does too.
        """
        request = getattr(self._session, "_event_request", None)
        self._session._event_request = None
        if request is not None:
            await sync_to_async(_derive_missing_principal)(request)
        return request

    # ------------------------------------------------------------------ #
    # Mount hooks (ADR-022 Iter 3 Phase 3.2, #1915; wired in Phase 3.3a, #1917)
    # SSE implementations: no-op / raw / refuse. SSE has no consumer to
    # back-reference, no sticky/live_redirect surface, and
    # no persistent socket to close — so these preserve SSE behaviour exactly.
    # ------------------------------------------------------------------ #

    def on_view_instantiated(self, view: Any) -> None:
        """Stamp the slot a view mounted beside the page view lives in (#3252).

        SSE has no WS consumer to back-reference; the SSE-transport identity
        stamp (``_sse_session_id`` / ``_sse_session`` / query string) already
        lands in :meth:`on_view_mounted`. The page view has no slot."""
        slot_target = getattr(self._session, "target_id", None)
        if isinstance(slot_target, str) and slot_target:
            view._djust_slot_target = slot_target

    async def send_view_refusal(self, reason: str, to: Optional[str] = None) -> bool:
        """Report a refused view beside the page view to its own container (#3252).

        See :meth:`WSConsumerTransport.send_view_refusal`."""
        session = self._session
        if not (isinstance(getattr(session, "target_id", None), str) and session.target_id):
            return False
        await session.send_refusal(reason, to)
        return True

    def next_mount_version(self, html: Optional[str], rust_version: int = 1) -> int:
        """SSE stamps the raw Rust ``render_with_diff()`` version on its mount frame.

        ADR-022 Iter 3 Phase 3.3a (#1917, Finding C). SSE has no consumer-owned
        counter (the #1788 unification never reached SSE — see
        :meth:`next_client_version`), so the mount frame keeps the raw Rust
        ``render_with_diff()`` version. Phase 3.3a wires ``dispatch_mount`` to
        route the mount version through this hook; SSE returns the ``rust_version``
        the runtime computed inline (the value it stamped before the wiring), so
        SSE clients stay calibrated to the Rust counter exactly as before. ``html``
        is unused (mirrors :meth:`next_client_version`, which returns its
        ``rust_version`` unchanged for the same reason).
        """
        return rust_version

    async def on_mount_render_ready(self, view: Any, html: Optional[str]) -> Optional[str]:
        """No-op for SSE (#1915, Finding B residual).

        SSE has no sticky / ``live_redirect`` surface, so there is no survivor
        set to reconcile and no ``sticky_hold`` frame to emit. Returns ``html``
        unchanged."""
        return html

    async def finalize_mount_auth(self, view: Any, verdict: Any) -> None:
        """No-op for SSE (#1915, Finding E).

        SSE has no persistent socket to drop on a blocking mount-auth verdict —
        the verdict frame (error / navigate) the runtime already sent IS the
        SSE-side finalization. Nothing further to do."""
        return None

    @property
    def mounting_in_batch(self) -> bool:
        """SSE never mounts in a shared-socket batch (#1915, #291). Always ``False``."""
        return False


# ------------------------------------------------------------------ #
# ViewRuntime
# ------------------------------------------------------------------ #


# --------------------------------------------------------------------------- #
# ADR-032 — component-scoped rendering (#2917)
# --------------------------------------------------------------------------- #

#: ``LiveComponent._bind`` stores a view's bound component under this
#: prefix + the attribute name (``_component_nav``); that is the key the
#: change-detection snapshot sees when only the component's State changed.
_COMPONENT_SLOT_PREFIX = "_component_"

_TEMPLATE_TOKEN_RE = re.compile(r"{{.*?}}|{%.*?%}|{#.*?#}", re.S)
_UNRESOLVED_TEMPLATE_RE = re.compile(r"{%\s*(include|extends)\b")


async def _release_failed_view(view: Any) -> None:
    """Release a view whose mount failed or was refused after ``mount()`` ran.

    ``mount()`` may already have registered children, waiters, uploads or live
    handles, and a failing ``connected()`` was entered; the view is gone, so
    its ``disconnected()`` hook runs and they go with it. Never raises.
    """
    from ._child_lifecycle import arelease_root_view, fire_view_disconnected

    try:
        try:
            await fire_view_disconnected(view)
        finally:
            await arelease_root_view(view, navigation=False, reason="mount_failed")
    except Exception as exc:  # noqa: BLE001 — cleanup after a failure already reported
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(logger, (view,), exc, "releasing a failed mount raised", level="debug")


def _scoped_component_for(view: Any, changed_keys: Optional[Any]) -> Optional[Any]:
    """ADR-032 D1(a)(c): the ONE assign the handler changed is a bound
    component's slot, and that component declares a template.

    Returns the :class:`~djust.components.base.BoundComponent`, or ``None``
    when the event changed nothing, several things, a plain assign, an
    instance component (D7 — no per-view slot), or a component without a
    template (``{{ nav }}`` is then its dict repr, not markup).
    """
    if not changed_keys or len(changed_keys) != 1:
        return None
    key = next(iter(changed_keys))
    if not isinstance(key, str) or not key.startswith(_COMPONENT_SLOT_PREFIX):
        return None
    name = key[len(_COMPONENT_SLOT_PREFIX) :]
    registry = getattr(view, "_components", None)
    component = registry.get(name) if isinstance(registry, dict) else None
    if component is None:
        return None
    from .components.base import BoundComponent

    if not isinstance(component, BoundComponent) or component.component_id != name:
        return None
    if getattr(component._descriptor, "_descriptor_storage_key", None) != key:
        return None
    if not (component.template or component.template_name):
        return None
    return component


def _template_is_component_opaque(source: str, name: str) -> bool:
    """ADR-032 D2 on a template SOURCE: every reference to ``name`` is the bare
    ``{{ name }}`` — never ``name.active``, ``name|length``, ``{% for x in
    name %}``, ``{% with name.state as s %}`` — and no ``{% include %}`` /
    ``{% extends %}`` is left that could read it elsewhere. Any other read of
    the component's state would have to re-render too, so such a template
    never takes the scoped path. Conservative by construction: the name
    inside a string literal or a comment tag also says "not opaque".
    """
    word = re.compile(rf"(?<![\w.]){re.escape(name)}(?!\w)")
    for match in _TEMPLATE_TOKEN_RE.finditer(source):
        token = match.group(0)
        if not word.search(token):
            continue
        if token.startswith("{{") and token[2:-2].strip() == name:
            continue
        return False
    return _UNRESOLVED_TEMPLATE_RE.search(source) is None


def _context_changed_besides(view: Any, context: Dict[str, Any], name: str) -> Optional[str]:
    """ADR-032 D1, verified rather than predicted (review of #2920 🔴1).

    The template is not the only reader of a component's state: a view
    ``@property``, a ``get_context_data`` override or a dep-less ``@computed``
    can derive a value from ``self.nav.active`` that the template reads under
    another name. Such a value lands in the render CONTEXT, so the context is
    compared, key by key, against what the last render synced — the same
    three-layer rule ``_sync_state_to_rust`` applies (identity, value for
    immutables, structural fingerprint for containers; #2664) — and any key
    other than the component's own that changed, appeared or vanished names
    the reason the scoped path is not exact. Returns that key, or ``None``
    when only the component changed.
    """
    from .change_detection import deep_fingerprint, fingerprints_by_content
    from .mixins.rust_bridge import _FRAMEWORK_KEYS, _IMMUTABLE_TYPES_FOR_SYNC

    prev_refs = getattr(view, "_prev_context_refs", None)
    prev_immutables = getattr(view, "_prev_context_immutables", None)
    prev_fps = getattr(view, "_prev_context_fingerprints", None)
    if prev_refs is None or prev_immutables is None or prev_fps is None:
        return "<no previous render>"
    # The same keys the sync leaves untracked or never diffs: context-
    # processor values, the request handle (re-assigned per event) and the
    # framework keys (``csrf_token``, the settings-constant date formats …).
    from .mixins.context import _request_scoped_keys

    skip = _request_scoped_keys(view)
    skip.update(_FRAMEWORK_KEYS)
    skip.add(name)
    for key, value in context.items():
        if key in skip:
            continue
        if isinstance(value, (dict, list, tuple)) or fingerprints_by_content(value):
            fp, truncated = deep_fingerprint(value)
            if truncated or key not in prev_fps or prev_fps[key] != fp:
                return str(key)
        elif isinstance(value, _IMMUTABLE_TYPES_FOR_SYNC):
            if key not in prev_immutables or prev_immutables[key] != value:
                return str(key)
        elif prev_refs.get(key) != id(value):
            return str(key)
    for key in prev_refs:
        if key not in context and key not in skip:
            return str(key)
    return None


def _view_is_component_opaque(view: Any, name: str) -> bool:
    """D2, decided once per (view class, template source, component) and
    cached on the class. Also false when a memoised ``@computed`` on the
    class lists the component among its dependencies — its value would
    change with the component's state and the template may read it."""
    cls = type(view)
    cache = cls.__dict__.get("_djust_component_opaque")
    if cache is None:
        cache = {}
        setattr(cls, "_djust_component_opaque", cache)
    # A file template (``template_name``) is resolved through the loader —
    # and the Rust inheritance resolver when it extends — on every
    # ``get_template()``; outside DEBUG the files do not change under a
    # running process, so the verdict is keyed by name and the source is
    # read once per class. In DEBUG (hot reload) the source is re-read and
    # hashed every time, so an edit that adds ``{{ nav.active }}`` is seen.
    from django.conf import settings as _dj_settings

    template_name = getattr(view, "template_name", None)
    inline = getattr(view, "template", None)
    key: Tuple[str, str]
    source: Any = None
    if isinstance(template_name, str) and not isinstance(inline, str) and not _dj_settings.DEBUG:
        key = (f"name:{template_name}", name)
        verdict = cache.get(key)
        if verdict is not None:
            return bool(verdict)
    try:
        source = view.get_template()
    except Exception:  # noqa: BLE001 — no template, no scoped path
        return False
    if not isinstance(source, str):
        return False
    if source is not None and not (
        isinstance(template_name, str) and not isinstance(inline, str) and not _dj_settings.DEBUG
    ):
        key = (
            hashlib.sha1(
                source.encode("utf-8", "surrogatepass"), usedforsecurity=False
            ).hexdigest(),
            name,
        )
    verdict = cache.get(key)
    if verdict is None:
        verdict = _template_is_component_opaque(source, name)
        if verdict:
            for attr in attribute_names(cls):  # dir() races class writes (#3151)
                prop = getattr(cls, attr, None)
                deps = getattr(prop, "_computed_deps", None)
                if deps and name in deps:
                    verdict = False
                    break
        if len(cache) > 256:
            cache.clear()
        cache[key] = verdict
    return verdict


def _reconstruct_explicit_page_shell(view: Any, request: Any) -> None:
    """Rebuild an explicit root's page-shell children on a live mount (E3-6).

    Only views whose page shell comes from ``template_name`` inheritance have
    children outside ``dj-root``. The full render registers them (restoring
    their declared server state through the child adapter); the caller's
    root render then reuses them rather than remounting.
    """
    if not getattr(view, "template_name", None) or getattr(view, "template", None):
        return
    from ._child_rendering import render_view_full_template

    view.get_template()
    if getattr(view, "_full_template", None) is None:
        return
    render_view_full_template(view, request)
    view._cached_context = None


def _explicit_descendant(root: Any, view_id: Any) -> Any:
    """Resolve a nested explicit child by ``view_id`` (ADR-038 E3-4).

    Direct children keep their existing lookup. Below them, an explicit root
    routes to a descendant only through the server-owned registry chain, and
    only when exactly one owned explicit descendant carries that id; an
    ambiguous or unknown id is not routed. Legacy roots are unchanged.
    """
    from ._exposure import uses_legacy_exposure

    if type(view_id) is not str or uses_legacy_exposure(root):
        return None
    matches = []
    pending = [root]
    seen: set = set()
    while pending and len(seen) < 257:
        parent = pending.pop()
        if id(parent) in seen:
            continue
        seen.add(id(parent))
        registry = getattr(parent, "_child_views", None)
        if type(registry) is not dict:
            continue
        for slot, child in tuple(registry.items()):
            if (
                getattr(child, "_parent_view", None) is not parent
                or getattr(child, "_view_id", None) != slot
                or getattr(child, "_djust_child_disposed", False)
                or uses_legacy_exposure(child)
            ):
                continue
            if parent is not root and slot == view_id:
                matches.append(child)
            pending.append(child)
    return matches[0] if len(matches) == 1 else None


def _public_decorator_metadata(view_instance: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """``(name, _djust_decorators)`` for every public decorated method.

    The mount frame's ``cache_config`` and ``handler_config`` are read from
    here. A class attribute that is a descriptor other than a function,
    ``staticmethod`` or ``classmethod`` is not read: that would run a
    ``@property`` or bind a class-level component on the event loop. The
    framework's own ``changed_fields`` / ``is_dirty`` properties fingerprint
    the whole view state, so two scans per mount cost four full state walks,
    and an app property that raised (an ORM read on the loop) made the
    caller's ``except`` drop the whole config.
    """
    from ._parameter_metadata import _ABSENT, _METHOD_TYPES, _mro_lookup

    cls = type(view_instance)
    found: List[Tuple[str, Dict[str, Any]]] = []
    # dir() races a first-use class cache on a free-threaded build, and the
    # callers' ``except`` would silently drop the config (#3151).
    for attr_name in attribute_names(cls):
        if attr_name.startswith("_"):
            continue
        declared = _mro_lookup(cls, attr_name)
        if (
            declared is not _ABSENT
            and type(declared) not in _METHOD_TYPES
            and _mro_lookup(type(declared), "__get__") is not _ABSENT
        ):
            continue
        decorators = getattr(getattr(view_instance, attr_name, None), "_djust_decorators", None)
        if decorators:
            found.append((attr_name, decorators))
    return found


#: How often a deferred SSE push re-checks that the client is draining (#3809).
#: A pause between checks only while the client is backlogged; nothing asserts on it.
_BACKLOG_POLL_S = 0.05


def _derive_missing_principal(request: Any) -> None:
    """Give an explicit mount the principal its events will have.

    A socket request has a ``user`` only when the ASGI stack put one in the
    scope. Behind a session-only stack (``DjustMiddlewareStack``) it has none,
    and an explicit mount binds its state to the request's user, so it could
    not mount at all. Every explicit event already reads its user from the
    session (``_exposure_auth.fresh_socket_request``), so the mount reads it
    the same way: mount and event then name the same principal, which the
    event check requires.

    Called after the pre-mount auth sequence and the ``on_mount`` hooks have
    passed with the request as the stack built it, so which views that stack
    admits is unchanged: a ``login_required`` or ``permission_required`` view
    is still refused there. Later gates keep judging it that way
    (``auth.core.request_as_the_stack_built_it``); the view's own code, the
    state binding, presence, rate limits and tenant resolution see the user.
    A ``user`` the stack supplied is never replaced, and a legacy view is
    never given one. Without ``django.contrib.auth`` there is nothing to read
    and the binding's own refusal stands.
    """
    if getattr(request, "user", None) is not None:
        return
    from django.apps import apps

    if not apps.is_installed("django.contrib.auth"):
        return
    if getattr(request, "session", None) is None:
        return
    from django.contrib.auth import get_user

    from .auth.core import USER_READ_FROM_SESSION

    request.user = get_user(request)
    # The access check keeps judging this request as the stack built it
    # (``auth.core.request_as_the_stack_built_it``), wherever it is reused.
    setattr(request, USER_READ_FROM_SESSION, True)


class ViewRuntime(RuntimeTurnPort):
    """Wire-blind runtime for a single mounted LiveView session.

    Responsible for view instantiation, auth check, mount, render, and
    all subsequent dispatch (events, URL changes). Output flows through
    ``self.transport.send(...)`` — the runtime has no notion of WebSocket
    frames or HTTP responses.

    Attributes:
        transport: The :class:`Transport` adapter for this session.
        view_instance: The mounted LiveView, or ``None`` before mount.
        session_id: Stable per-session ID, proxied from the transport.
        scope: Optional ASGI scope (only available for WS-backed runtimes).
        rate_limiter: Optional :class:`ConnectionRateLimiter` (per-session).
    """

    def __init__(
        self,
        transport: Transport,
        *,
        scope: Optional[Dict[str, Any]] = None,
        rate_limiter: Optional[ConnectionRateLimiter] = None,
        renderer_factory: Optional[Any] = None,
    ) -> None:
        self.transport = transport
        self.view_instance: Optional[Any] = None
        self.scope = scope
        self._rate_limiter = rate_limiter or ConnectionRateLimiter()
        # Stashed mount-failure error frame (set by _instantiate_view, awaited +
        # cleared by dispatch_mount). See _instantiate_view for the #1919 rationale.
        self._instantiate_error_frame: Optional[Dict[str, Any]] = None
        # NOTE: the runtime does NOT own a render lock. Render serialization is
        # consumer-owned (``LiveViewConsumer._render_lock``, websocket.py:619) and
        # SHARED with the WS-only tick / server-push / db-notify loops; a
        # runtime-local lock would be a different object and could not serialize
        # against those (#560). The transport borrows the consumer's existing lock
        # via ``transport.event_context(view)`` (ADR-022 Iter 2 Phase 2.3a, #1899).
        # ADR-019 LVN-I PR-2: Renderer factory plumbed through. Stored
        # for PR-3 (handshake) to set based on ``?platform=`` selection;
        # ``None`` means "use the default ``HtmlRenderer``" which the
        # dispatch site (``TemplateMixin.render_with_diff``) already
        # constructs inline. Type kept ``Any`` to avoid circular import
        # with ``djust.renderers``; runtime use-site will cast.
        self.renderer_factory = renderer_factory
        self._explicit_mount_binding: Any = None
        # The view a running mount admitted (see ``_unmount_on_failure``).
        self._admitted_mount_view: Any = None
        # Explicit request/auth/save state is per turn, including the work
        # before entering a transport's render lock.
        self._explicit_event_lock = asyncio.Lock()
        # #3200: the explicit save that may still be running (saves are ordered
        # per runtime), the count of committed explicit turns, and the pending
        # catch-up turn scheduled after a deferred save.
        self._explicit_save_pending: Optional["asyncio.Future[None]"] = None
        # The last deferred root save, until a save succeeds (#4095).
        self._explicit_unlanded: Any = None
        self._explicit_commits = 0
        # The request whose binding this turn's root save just checked against
        # the mount binding; read once by ``_explicit_event_snapshot`` (#3997).
        self._explicit_binding_verified: Any = None
        self._explicit_deferrals = 0
        self._explicit_catch_up: Optional["asyncio.Task[None]"] = None
        self._in_explicit_catch_up = False
        # #3201: vanished session key -> the replacement session this
        # connection created, so repeated mounts reuse one replacement.
        self._replacement_sessions: Dict[str, str] = {}
        # ADR-036: only transport-local advertisement state, never application
        # state or a cached owner/contract. A later all-legacy render must clear
        # a previously advertised strict manifest.
        self._parameter_contracts_active = False
        self._parameter_contract_view: Optional[str] = None
        # #3036: the destination page's <title>, recorded after the mount
        # render when the transport asks for it (``capture_document_title``,
        # WS: during a live_redirect mount). See ``navigation_title``.
        self.mount_document_title: Optional[str] = None
        # Server-originated turns on a transport with no channel layer (SSE,
        # #3809). ``_mount_sent_for`` is the view whose mount frame went out;
        # the tick task and the deferred-push queue and drain belong to it.
        self._mount_sent_for: Any = None
        self._tick_task: Optional["asyncio.Task[None]"] = None
        self._deferred_pushes: Optional["collections.deque[Tuple[Any, Dict[str, Any]]]"] = None
        self._push_drain_task: Optional["asyncio.Future[None]"] = None

    # ------------------------------------------------------------------ #
    # Public properties
    # ------------------------------------------------------------------ #

    @property
    def session_id(self) -> str:
        return self.transport.session_id

    # Compatibility shim — security helpers read ``_client_ip`` directly.
    @property
    def _client_ip(self) -> Optional[str]:
        return self.transport.client_ip

    # ------------------------------------------------------------------ #
    # Top-level dispatch
    # ------------------------------------------------------------------ #

    @_runtime_diagnostic_scope
    async def dispatch_message(self, data: Dict[str, Any]) -> None:
        """Route an inbound frame to the appropriate handler by ``type``.

        Unknown types emit a structured error envelope but never raise —
        this is the forward-compat seam for transports that may receive
        future frame types (uploads, presence) the runtime doesn't yet
        own.
        """
        from ._exposure_diagnostics import diagnostics_allowed

        try:
            msg_type = data.get("type")
            if msg_type == "mount":
                await self.dispatch_mount(data)
            elif msg_type == "event":
                await self.dispatch_event(data)
            elif msg_type == "url_change":
                await self.dispatch_url_change(data)
            else:
                await self.transport.send_error(f"Unknown message type: {msg_type}")
        except Exception as exc:
            if diagnostics_allowed():
                # Legacy callers keep their existing transport-specific catches.
                raise
            response = handle_exception(exc, expose_details=False, logger=logger)
            if isinstance(data, dict) and data.get("type") == "event":
                response["source"] = "event"
                raw_ref = data.get("ref")
                if type(raw_ref) is int or type(raw_ref) is float:
                    try:
                        response["ref"] = int(raw_ref)
                    except (ValueError, OverflowError):
                        # Match existing numeric refs without letting a forged
                        # NaN/infinity defeat the protected error boundary.
                        pass
            try:
                await self.transport.send(response)
            except Exception:  # noqa: BLE001 — do not leak delivery errors to outer handlers
                logger.error("Protected error response could not be delivered")
                try:
                    await self.transport.close(code=1011)
                except Exception:  # noqa: BLE001 — even close failures must be value-free
                    logger.error("Protected transport could not be closed")

    # ------------------------------------------------------------------ #
    # Mount dispatch (used by SSE in this PR; WS still uses handle_mount)
    # ------------------------------------------------------------------ #

    async def dispatch_mount(
        self,
        data: Dict[str, Any],
        *,
        turn_source: Optional[str] = None,
        reconnect: Optional[bool] = None,
    ) -> None:
        """Mount a LiveView from a mount frame, as one ``mount`` turn (ADR-044).

        Every mount entry point (page, slot, ``mount_batch``, live-redirect
        navigation, the SSE stream) comes through here, so the turn-middleware
        chain wraps them all; see :meth:`_dispatch_mount_body` for the mount.
        ``turn_source`` names the caller (``"batch"``, ``"navigation"``); a frame
        with a ``target_id`` is a ``"slot"`` mount, any other a ``"page"`` one.
        ``reconnect`` (or the client's ``reconnect`` hint) makes it a
        ``reconnect`` turn; that is informational and changes nothing else.
        """
        # Idempotent — second mount on the same runtime is a no-op (no turn).
        if self.view_instance is not None:
            return
        source = (
            turn_source
            or getattr(self.transport, "mount_turn_source", None)
            or ("slot" if data.get("target_id") is not None else "page")
        )
        if reconnect is None:
            reconnect = data.get("reconnect") is True or bool(
                getattr(self.transport, "mount_is_reconnect", False)
            )
        view_path = data.get("view")
        view_class = None
        if isinstance(view_path, str) and view_path:
            from .security.mount import resolve_view_class

            view_class = resolve_view_class(view_path).view_class
        params = data.get("params")
        turn = Turn(
            "reconnect" if reconnect else "mount",
            self.turn_transport,
            source=source,
            view_class=view_class,
            params=MappingProxyType(params) if isinstance(params, dict) else None,
            request=getattr(self.transport, "turn_request", None),
            scope=self.scope,
            session_id=self._turn_session_id(),
            port=self,
        )

        async def body(turn: Turn) -> None:
            try:
                await self._dispatch_mount_body(data)
            finally:
                turn.view = self.view_instance

        await run_turn(turn, body)

    def _turn_session_id(self) -> Optional[str]:
        try:
            return self.session_id
        except Exception:  # noqa: BLE001 - duck-typed transports may lack one
            return None

    @_mount_tenant_scope
    @_unmount_on_failure
    async def _dispatch_mount_body(self, data: Dict[str, Any]) -> None:
        """Mount a LiveView from a mount frame (the body of the ``mount`` turn).

        Idempotent: if ``view_instance`` is already set the call is a no-op.
        This protects against the legacy ``?view=`` GET-mount + new POST
        mount-frame double-fire (Risk #1 in the plan).

        Frame schema (matches WebSocket ``handle_mount`` at
        ``websocket.py:1657-1660``)::

            {
                "type": "mount",
                "view": "myapp.views.HomeView",  # dotted view path
                "params": {...},                  # initial mount kwargs
                "url": "/items/42/",              # client's window.location.pathname
                "has_prerendered": false,
                "client_timezone": "America/New_York",
            }
        """
        # Idempotent — second mount on the same runtime is a no-op.
        if self.view_instance is not None:
            return

        view_path = data.get("view")
        params: Dict[str, Any] = data.get("params", {}) or {}
        # F23 (#1819 traversal fix, parallel-path-drift #1646): the client URL is
        # attacker-controlled and is fed to RequestFactory.get() / resolve() /
        # logs / build_absolute_uri below. Validate it through the SAME shared
        # validator the WebSocket path uses (websocket.handle_mount), so the SSE
        # / runtime path neutralises "/%2e%2e/admin/" identically. _build_request
        # validates again defensively.
        from .security.mount import is_view_path_allowed, validate_mount_url

        self.mount_document_title = None
        page_url = validate_mount_url(data.get("url", "/"))
        client_timezone = data.get("client_timezone")

        if not view_path:
            await self.transport.send_error("Missing view path in mount request")
            return

        # ---- Security (F22 — unsafe reflection / arbitrary module import) ----
        # Shape + allowlist gate (no import) for a clean early reject frame even
        # when a test overrides _instantiate_view. The full resolver
        # (resolve_view_class) runs inside _instantiate_view as defense in depth.
        if not is_view_path_allowed(view_path):
            logger.warning(
                "Blocked attempt to mount disallowed/uninitialized view: %s",
                sanitize_for_log(view_path),
            )
            await self.transport.send_error(
                _safe_error(f"View {view_path} is not allowed", "View not found")
            )
            return

        # ---- Instantiate view (extension hook for tests) ----
        view_instance = self._instantiate_view(view_path)
        if view_instance is None:
            # _instantiate_view stashed the error frame (#1919): AWAIT-send it HERE
            # so the send lands inside this dispatch (and, under the mount_batch
            # collector, inside the CORRECT _mount_one window — a fire-and-forget
            # send leaked a failed view's error into the next survivor's collector).
            # getattr-guarded: a MagicMock-stubbed _instantiate_view that returns a
            # view never sets it, and a stub returning None without setting it sends
            # nothing (the test contract — the stubs assert on dispatch, not frames).
            error_frame = getattr(self, "_instantiate_error_frame", None)
            if error_frame is not None:
                await self.transport.send(error_frame)
                self._instantiate_error_frame = None
            return

        from ._exposure_diagnostics import restrict_diagnostics

        restrict_diagnostics(view_instance)
        running = current_turn()
        if running is not None and running.view is None:
            running.view = view_instance

        # ---- Transport back-references on the freshly-instantiated view ----
        # ADR-022 Iter 3 Phase 3.3a (#1917, Finding B). Wire the
        # ``on_view_instantiated`` hook at the SAME point the bespoke WS
        # ``handle_mount`` stamps its consumer back-refs (websocket.py:2128 ff):
        # IMMEDIATELY after instantiation, BEFORE auth / mount /
        # render. WS: stamps ``_ws_consumer`` /
        # observability ``register_view`` / validated handshake host. SSE: no-op.
        # getattr-guarded so duck-typed test transport fakes that predate this
        # Protocol method (and the default-bearing Protocol itself) keep working.
        on_view_instantiated = getattr(self.transport, "on_view_instantiated", None)
        if on_view_instantiated is not None:
            on_view_instantiated(view_instance)

        self.view_instance = view_instance

        # ADR-019 LVN-I: if the handshake selected a renderer (factory
        # passed in via __init__), bind it to the view so
        # TemplateMixin.render_with_diff can dispatch through it
        # instead of always constructing HtmlRenderer inline.
        if self.renderer_factory is not None:
            view_instance._djust_renderer = self.renderer_factory(view_instance)

        # Stash the client-supplied dotted view path from the mount frame so the
        # transport hook (WS ``on_view_mounted``) can derive the server-push channel
        # group name from the SAME string external broadcasters use
        # (``view_group_name(data["view"])`` — websocket.py:2172-2174 / push.py). It
        # MUST be the frame value, NOT ``module.qualname`` of the instance, because
        # ``apush_to_view("app.MyView")`` string-mangles whatever the caller passes
        # and the two must match byte-for-byte for delivery (#1919, Finding B).
        view_instance._djust_mount_view_path = view_path

        # Stash transport identity on the view (used by VDOM caching).
        view_instance._websocket_session_id = self.session_id
        # The tab the page says it is (#4106): selects one of this session's
        # own saved states. None, or a malformed one, selects nothing.
        from ._automatic_state import tab_id as _tab_id

        view_instance._djust_tab = _tab_id(data.get("tab"))
        view_instance._websocket_path = page_url
        view_instance._websocket_query_string = ""

        # Optional client timezone (validate IANA string).
        view_instance.client_timezone = None
        if client_timezone:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(client_timezone)
                view_instance.client_timezone = client_timezone
            except Exception:
                logger.warning("Invalid client timezone: %s", client_timezone)

        # ---- Build request ----
        request = await self._build_request(page_url=page_url, params=params)
        # A view mounted beside the page view (#3252) is addressed by its slot
        # wherever the request is bound to an identity (explicit saved state,
        # client snapshots, event authorization).
        slot_target = getattr(view_instance, "_djust_slot_target", None)
        if slot_target:
            request._djust_slot_target = slot_target

        from ._exposure import uses_legacy_exposure

        if not uses_legacy_exposure(view_instance):
            # #3201: a cookie naming a vanished session (cache flush, expiry)
            # mounts fresh under a replacement session, as the HTTP GET does,
            # instead of failing the state binding on every reconnect. Runs
            # before the pre-mount auth sequence so auth sees the re-derived
            # (anonymous) user, not the connect-time one.
            from ._exposure_auth import establish_mount_session

            presented = getattr(getattr(request, "session", None), "session_key", None)
            # A WebSocket runtime synthesizes its request (it has a scope); an
            # SSE runtime mounts on the real stream request, whose response
            # issues the replacement as a cookie (review I-b).
            replacement = await sync_to_async(establish_mount_session)(
                request,
                self._replacement_sessions.get(presented or ""),
                ephemeral=self.scope is not None,
            )
            if replacement and presented:
                self._replacement_sessions[presented] = replacement

        try:
            view_instance.request = request
            # The Django session key the view mounted under; None/empty for an
            # anonymous session.
            session_obj = getattr(request, "session", None)
            view_instance._django_session_key = (
                getattr(session_obj, "session_key", None) if session_obj is not None else None
            )
            if hasattr(view_instance, "_initialize_temporary_assigns"):
                await sync_to_async(view_instance._initialize_temporary_assigns)()
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error initializing {sanitize_for_log(view_path)}",
                expose_details=_diagnostics_policy_allows(view_instance),
            )
            await self.transport.send(response)
            self.view_instance = None
            return

        # ---- Pre-mount security sequence (auth + tenant resolve/bind) ----
        # _check_auth runs the shared djust.auth.core.run_pre_mount_auth sequence
        # (auth via check_view_auth, then — only on auth success — _ensure_tenant
        # + the tenant ContextVar bind). Single-sourcing the SEQUENCE with the WS
        # + SSE mount paths means a future edit cannot reorder the steps or drop
        # one on this path (#1646 / #1853). The runtime-specific verdict→frame
        # mapping (error frame / navigate frame / tenant-error envelope) stays
        # inside _check_auth; it remains the mockable auth seam tests stub.
        redirect_or_block = await self._check_auth(request)
        if redirect_or_block is not None:
            # _check_auth already pushed the appropriate frame.
            self.view_instance = None
            return

        # ---- on_mount hooks (after auth, before mount) ----
        # Ported from WS handle_mount (websocket.py:2383-2401), placed at the
        # SAME point: AFTER the pre-mount auth sequence, BEFORE mount(). The
        # registered ``on_mount`` hooks (djust.hooks.run_on_mount_hooks) run
        # against the live view + request and may return a redirect URL.
        #
        # Transport-agnostic redirect handling: the runtime emits the navigate
        # frame, then the ``finalize_mount_auth`` hook (#1917, Finding E) applies
        # the transport-level ``close(4403)`` — WS gated on
        # ``not _mounting_in_batch`` (#291 / #1780: a batched login-required view
        # reports as ``navigate[]`` and closing would kill sibling mounts);
        # SSE no-op. The ``"hook_redirect"`` verdict honors the SAME batch gate as
        # the auth ``"redirect"`` verdict (websocket.py:2389-2400). The hook is
        # called BEFORE clearing ``view_instance`` so the WS impl's view arg is
        # valid (it routes the close through the consumer regardless).
        from .hooks import run_on_mount_hooks

        # mount() and the on_mount hooks take each repeated query key's LAST
        # value, as in 1.3; handle_params() alone gets the list (#4125).
        last_values = {
            key: (value[-1] if value else "") if isinstance(value, list) else value
            for key, value in params.items()
        }
        hook_redirect = await sync_to_async(run_on_mount_hooks)(
            view_instance, request, **last_values
        )
        if hook_redirect:
            if not await self._refused_view("redirect", hook_redirect):
                await self.transport.send({"type": "navigate", "to": hook_redirect})
            await self._finalize_mount_auth("hook_redirect")
            self.view_instance = None
            return
        # ---- End on_mount hooks ----

        # After every admission decision (the auth sequence and the on_mount
        # hooks judged the request as the stack built it), before mount().
        if not uses_legacy_exposure(view_instance):
            await sync_to_async(_derive_missing_principal)(request)

        # Transport-specific identity + post-mount setup hook (#1887 / #1919):
        # SSE stamps _sse_session_id / _sse_session + the real query string here so
        # the converged runtime mount preserves everything legacy _sse_mount_view
        # exposed. WS performs its post-mount channel-layer wiring (view/presence/
        # db_notify group_add), tick-task start, and real-scope
        # path/query-string stamps (ADR-022 Iter 3 Phase 3.3b, Finding B residual).
        # Async (the WS impl awaits group_add). getattr-guarded + awaitable-guarded
        # so duck-typed test transport fakes that predate this Protocol method (or
        # still expose a SYNC no-op) keep working.
        #
        # Runs only once the pre-mount auth sequence and the on_mount hooks have
        # admitted the view: a view that is redirected or refused never joins the
        # view / presence / db_notify groups, never starts a tick task, and never
        # becomes the SSE session's mounted view. Still BEFORE state restore and
        # mount(), so the db_notify join reads ``_listen_channels`` at the same
        # point it always has.
        self._admitted_mount_view = view_instance
        on_view_mounted = getattr(self.transport, "on_view_mounted", None)
        if on_view_mounted is not None:
            result = on_view_mounted(view_instance)
            if inspect.isawaitable(result):
                await result

        # ---- Mount kwargs ----
        mount_kwargs = dict(last_values)
        url_kwargs = self._resolve_url_kwargs(page_url)
        if url_kwargs:
            mount_kwargs.update(url_kwargs)

        legacy_exposure = uses_legacy_exposure(view_instance)
        # Whether saved state was put back over mount(): handle_restore() then
        # runs after the object-permission check (#4088).
        run_restore_hook = False

        # ADR-035: a view that looks its object up from the route gets only the
        # kwargs of its own route, never client params. Bound before mount().
        bind_route = getattr(view_instance, "_djust_bind_route_kwargs", None)
        if callable(bind_route):
            bind_route(self._own_route_kwargs(view_instance, page_url))

        try:
            await call_hook(view_instance.mount, request, **mount_kwargs)
            # Which ``_private`` attributes mount() created (saved with the
            # view's state, #4107). A view object that is not a LiveView (the
            # runtime only needs mount/render of it) has no such record, and
            # nothing of it is saved.
            snapshot_private = getattr(view_instance, "_snapshot_user_private_attrs", None)
            if callable(snapshot_private):
                snapshot_private()
            if not legacy_exposure:
                from ._exposure_sessions import load_server_state

                if callable(getattr(view_instance, "resolve_tenant", None)):
                    request.tenant = getattr(view_instance, "_tenant", None)
                restored = await sync_to_async(load_server_state)(view_instance, request)
                from django.conf import settings

                incoming = data.get("state_snapshot")
                if (
                    getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True)
                    and not slot_target
                    and type(incoming) is dict
                    and incoming.get("view_slug") == view_path
                    and await sync_to_async(
                        _lifecycle_step(view_instance, view_instance._should_restore_snapshot)
                    )(request)
                ):
                    from ._exposure_snapshots import snapshot_codec

                    codec = await sync_to_async(snapshot_codec)(view_instance, request)
                    client_values = codec.restore(incoming.get("state_json")) if codec else None
                    if client_values is not None:
                        # Server/client fields are disjoint by declaration;
                        # both dictionaries are completely validated first.
                        restored = {**(restored or {}), **client_values}
                if restored is not None:
                    # #3989: the declared state came back AFTER mount() ran, so
                    # mount() never saw it. The ``handle_restore`` hook lets the
                    # view act on what was restored (read rows again from a
                    # restored id, clear a persisted "running" flag whose work
                    # did not survive). It runs below, after handle_params(),
                    # the saved state's re-apply and the object-permission
                    # check: the order of the HTTP fallback (mixins/request.py)
                    # and an embedded child (templatetags/live_tags.py), so the
                    # hook sees the same view on every transport (#4088).
                    await apply_restored_state(view_instance, restored, request, run_hook=False)
                    run_restore_hook = True
                    # #1977: the client's DOM predates this restore, so the
                    # Rust diff baseline primed by the mount render does not
                    # match it. The first event after the restore must send
                    # full HTML, not a patch against that stale baseline. The
                    # mount render reads the flag and never resets it; the
                    # first event's render consumes it. Not when the restore
                    # put back exactly what mount() built (#4107): the page
                    # GET saved that state and rendered the page from it, so
                    # the browser's DOM and the baseline already agree.
                    if view_instance.__dict__.get("_djust_restore_changed_view", True):
                        view_instance._force_full_html = True
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error in {sanitize_for_log(view_path)}.mount()",
                expose_details=_diagnostics_policy_allows(view_instance),
            )
            await self._fail_mount(view_instance, exc, response)
            return

        # ---- Mount-request stash + dirty/private baselines (ADR-022 Iter 3
        # Phase 3.0 transport-agnostic grows) ----
        # Ported from WS handle_mount (websocket.py:2596-2603), placed at the
        # SAME point: AFTER mount(), BEFORE handle_params. The object-permission
        # check runs after handle_params (below); a denial there clears the
        # explicit binding set here along with the view.
        #
        # #1895: stash the mount request + kwargs on the view, for the code
        # that needs the request a view was mounted under after the mount
        # (its path and session). Behavioral-parity pin in
        # test_transport_behavioral_parity.py (gate-off: delete this stash → the
        # mount-stash net goes RED).
        view_instance._djust_mount_request = request
        view_instance._djust_mount_kwargs = mount_kwargs
        if not legacy_exposure:
            from ._exposure_sessions import request_binding

            if callable(getattr(view_instance, "resolve_tenant", None)):
                request.tenant = getattr(view_instance, "_tenant", None)
            self._explicit_mount_binding = await sync_to_async(request_binding)(request)

        # _capture_dirty_baseline: record the dirty-tracking baseline so
        # subsequent renders/change-detection see the correct "since mount"
        # delta. A pure view method (no transport), hasattr-guarded to stay safe
        # against duck-typed test views.
        if hasattr(view_instance, "_capture_dirty_baseline"):
            await sync_to_async(view_instance._capture_dirty_baseline)()

        # ---- handle_params (Phoenix-parity, fixes #1237 bug 3) ----
        try:
            from urllib.parse import urlencode

            query_string = urlencode(params, doseq=True) if params else ""
            uri = f"{page_url}?{query_string}" if query_string else page_url
            await run_handle_params(view_instance, params, uri)
            if not legacy_exposure:
                # #4107: what a handler set (restored above) goes back over the
                # defaults handle_params() just assigned; a declared
                # persist="server" field keeps the URL's value (#3624).
                from ._exposure_sessions import reapply_saved_state

                reapply_saved_state(view_instance)
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error in {sanitize_for_log(view_path)}.handle_params()",
                expose_details=_diagnostics_policy_allows(view_instance),
            )
            await self._fail_mount(view_instance, exc, response)
            return

        # ---- Object-permission check (ADR-017 §Decision 5) ----
        # Runs AFTER mount() AND handle_params(), before connected() and the
        # first render — the same order as the HTTP GET render
        # (RequestMixin.get) and dispatch_url_change. get_object() may read
        # access-determining state that handle_params() sets from the mount
        # frame's params (e.g. ``self.doc_id`` from ``?id=``), so checking any
        # earlier would authorize the pre-params object and then render the one
        # the params selected. The ADR-035 one-shot verdict a managed-object
        # view leaves in mount() is consumed here, as on HTTP GET.
        # Routed through the shared enforce_object_permission chokepoint so the
        # runtime path cannot drift from WS/SSE/API (#1646). No-op for views
        # that don't override get_object. Fail-closed: the chokepoint maps a
        # None request / any non-PermissionDenied get_object failure to denial;
        # the raised PermissionDenied becomes the permission_denied error frame
        # (matching dispatch_url_change).
        from django.core.exceptions import PermissionDenied

        from .auth.core import enforce_object_permission

        try:
            await sync_to_async(enforce_object_permission)(view_instance, request)
        except PermissionDenied:
            logger.info(
                "Object-permission denied for %s (runtime mount)",
                view_instance.__class__.__name__,
            )
            if not await self._refused_view("permission_denied"):
                await self.transport.send_error(
                    "Access denied for this object.", code="permission_denied"
                )
            # ADR-022 Iter 3 Phase 3.3b (#1919, Finding E): the runtime sent the
            # error frame; finalize_mount_auth adds the transport-level
            # ``close(4403)`` the bespoke WS handle_mount performed UNCONDITIONALLY
            # on an object-perm denial (websocket.py:2622 — matching the
            # PermissionDenied verdict, NOT the batch-gated redirect verdicts). SSE
            # no-op. Without this the WS socket stayed open after a denied mount
            # (the test_object_permission_denied_mount_refused net).
            # mount() ran: undo what it and the transport set up (tick,
            # groups, presence), as for a mount that raised.
            await self._on_mount_failed(view_instance)
            await self._finalize_mount_auth("permission_denied")
            self.view_instance = None
            self._explicit_mount_binding = None
            await _release_failed_view(view_instance)
            return

        # ---- handle_restore() (#3989, #4088) ----
        # One order on every transport: mount() (or the restore), the saved
        # state put back, handle_params(), the saved state re-applied, the
        # object-permission check, then this hook, so it reads from the state
        # the view will render and never for a denied object. The HTTP fallback
        # (mixins/request.py) and an embedded child (templatetags/live_tags.py)
        # call it at the same point.
        if run_restore_hook:
            handle_restore = getattr(view_instance, "handle_restore", None)
            if callable(handle_restore):
                try:
                    await call_hook(handle_restore, request)
                except Exception as exc:
                    response = handle_exception(
                        exc,
                        error_type="mount",
                        view_class=view_path,
                        logger=logger,
                        log_message=f"Error in {sanitize_for_log(view_path)}.handle_restore()",
                        expose_details=_diagnostics_policy_allows(view_instance),
                    )
                    await self._fail_mount(view_instance, exc, response)
                    return

        # ---- connected() view hook (#3007) ----
        # The live mount only: the view is admitted, set up (mount() or a
        # restore) and its URL state applied, and has not rendered yet, so
        # state the hook sets is in the first frame. A failure fails the mount
        # like one from mount(); the view still gets its disconnected().
        try:
            from ._child_lifecycle import begin_live_connection

            connected_hook = begin_live_connection(view_instance)
            if connected_hook is not None:
                await sync_to_async(connected_hook)()
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error in {sanitize_for_log(view_path)}.connected()",
                expose_details=_diagnostics_policy_allows(view_instance),
            )
            await self._fail_mount(view_instance, exc, response)
            return

        # ---- Initial render ----
        rust_version = 1
        # The page root's own dj-poll attributes, for the mount frame.
        root_poll: Optional[Dict[str, str]] = None
        from .mixins.template import _root_poll_attrs

        try:
            if hasattr(view_instance, "_initialize_rust_view"):
                await sync_to_async(view_instance._initialize_rust_view)(request)
            if hasattr(view_instance, "_sync_state_to_rust"):
                await sync_to_async(view_instance._sync_state_to_rust)()
            # ADR-019 LVN: capture patches (was discarded as ``_patches``) — for
            # native renderers (NativeRenderer) ``html`` is empty and the wire
            # payload is the patch list, shipped on the mount frame below so the
            # native client can bootstrap its widget tree on connect.
            from ._child_rendering import render_view_with_diff

            if not uses_legacy_exposure(view_instance):
                # ADR-038 E3-6: the page shell stays in the browser across
                # a (re)connect, but this is a new root instance. Render the
                # full page once, as the HTTP GET does, so explicit
                # page-shell children are reconstructed from their stored
                # state and registered for routing.
                await sync_to_async(_reconstruct_explicit_page_shell)(view_instance, request)
            html, render_patches, rust_version = await sync_to_async(render_view_with_diff)(
                view_instance
            )
            if hasattr(view_instance, "_strip_comments_and_whitespace"):
                html = await sync_to_async(view_instance._strip_comments_and_whitespace)(html)
            if hasattr(view_instance, "_extract_liveview_content"):
                root_poll = _root_poll_attrs(html)
                html = await sync_to_async(view_instance._extract_liveview_content)(html)
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="render",
                view_class=view_path,
                logger=logger,
                log_message=f"Error rendering {sanitize_for_log(view_path)}",
                expose_details=_diagnostics_policy_allows(view_instance),
            )
            await self._fail_mount(view_instance, exc, response)
            return

        # #3036: a live_redirect mount records the destination page's
        # <title>; the WS consumer sends it when the view queued none.
        # Skipped when the view already queued its own title, which wins.
        if getattr(self.transport, "capture_document_title", False) and not _queued_title(
            view_instance
        ):
            self.mount_document_title = await sync_to_async(navigation_title)(view_instance)

        # #3036: a live_redirect mount also names the destination's page
        # shell, so the client can fall back to a full load when the document
        # it holds loads different stylesheets or scripts. See ``_page_shell``.
        page_shell_fingerprint: Optional[str] = None
        if getattr(self.transport, "capture_page_shell", False):
            from ._page_shell import page_shell

            page_shell_fingerprint = await sync_to_async(page_shell)(view_instance)

        # ---- Post-render mount hook (#1917, Finding B residual) ----
        # ``on_mount_render_ready`` runs AFTER the render produced ``html`` but
        # BEFORE the mount frame is sent, so a transport can adjust the outgoing
        # HTML and/or emit pre-mount frames at the WS-faithful position
        # (websocket.py:2836-2903 emits the ``sticky_hold`` frame here, BEFORE the
        # mount frame — the ordering the client's reattachStickyAfterMount needs).
        # WS: sticky preservation + ``sticky_hold``; SSE: returns ``html``
        # unchanged. getattr-guarded for duck-typed test fakes.
        on_mount_render_ready = getattr(self.transport, "on_mount_render_ready", None)
        if on_mount_render_ready is not None:
            html = await on_mount_render_ready(view_instance, html)

        if not await self._persist_explicit_children_after_event(
            view_instance, request=request, mounted=False
        ):
            return

        # ---- Mount-frame wire version (#1917, Finding C) ----
        # ``next_mount_version`` stamps the baseline the client calibrates to. WS:
        # the consumer-owned monotonic ``_next_version()`` (NO arm — mount has no
        # prior frame to recover to, websocket.py:2746). SSE: the raw Rust
        # ``render_with_diff()`` version (``rust_version``), unchanged. Crucially
        # this is the NO-ARM mount baseline — it does NOT route through the ARMING
        # wire-version helper the event path uses (mount has no prior frame to
        # recover to). getattr-guarded: a transport without the hook falls back to
        # the raw Rust version (the pre-3.3a inline behavior).
        next_mount_version = getattr(self.transport, "next_mount_version", None)
        if next_mount_version is not None:
            version = next_mount_version(html, rust_version)
        else:
            version = rust_version

        mount_msg: Dict[str, Any] = {
            "type": "mount",
            "session_id": self.session_id,
            "view": view_path,
            "version": version,
        }
        if getattr(view_instance, "_explicit_child_vdom_tracking", False):
            # Only committed render-issued locations enter the mount protocol.
            # This contains no state, mount inputs, or restoration authority.
            mount_msg["owned_children"] = [
                [child._view_id, node_id]
                for node_id, child in view_instance._explicit_child_vdom_bindings.items()
            ]
        if page_shell_fingerprint:
            mount_msg["page_shell"] = page_shell_fingerprint
        if root_poll is not None and html:
            # The root's own dj-poll: the client applies it to the root element,
            # which a mount (content only) leaves as the previous view had it.
            mount_msg["root_poll"] = root_poll

        # The mount frame always carries the freshly rendered HTML: restored
        # declared state can combine fresh server state and a client snapshot,
        # and HTML the client already holds is not proof that it matches.
        if html is not None:
            mount_msg["html"] = html
            mount_msg["has_ids"] = "dj-id=" in html
            if getattr(view_instance, "_component_bindings", {}):
                # This fresh HTML establishes the current diff baseline; an
                # unchanged native observation must not force another render.
                view_instance._force_full_html = False

        # ADR-019 LVN: for native renderers (NativeRenderer), ``html`` is empty and
        # the wire payload is patches. Ship them in the mount frame so the native
        # client can bootstrap its widget tree on connect (the browser/HTML path
        # needs ``html`` only — patches arrive on subsequent updates). ``render_patches``
        # was captured at the render call above; not every render branch sets it, so
        # read it via ``locals()`` and only attach when a non-HTML renderer is bound
        # and patches are present + non-empty.
        if getattr(view_instance, "_djust_renderer", None) is not None and locals().get(
            "render_patches"
        ):
            mount_msg["patches"] = locals()["render_patches"]

        # state_snapshot_signed EMIT. An explicit view with ``persist="client"``
        # fields ships their signed token on the mount frame; the client stores
        # it verbatim and echoes it back on the next back-navigation, where the
        # restore above verifies it. Every other explicit mount ships null,
        # which clears a token the browser still holds. A legacy view ships
        # nothing. Emission can never break the mount (#1788 posture).
        if not legacy_exposure:
            from django.conf import settings

            # Clear stale browser state when capture is disabled or unavailable.
            mount_msg["state_snapshot_signed"] = None
            if getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True):
                from ._exposure_snapshots import snapshot_codec

                try:
                    codec = await sync_to_async(snapshot_codec)(view_instance, request)
                    if codec is not None:
                        mount_msg["state_snapshot_signed"] = await sync_to_async(codec.capture)(
                            view_instance
                        )
                except Exception:
                    # No repr or traceback: descriptor factories and codec
                    # failures can contain server-only values.
                    logger.warning(
                        "Explicit client snapshot unavailable; cached snapshot invalidated"
                    )

        # A view beside the page view (#3252) ships no navigation snapshot, not
        # even the null that clears one: the client keeps one token per view
        # class for the page view, which a sibling of that class would overwrite
        # or revoke.
        if slot_target:
            mount_msg.pop("state_snapshot_signed", None)

        # ADR-038 D-b / D-n: value-free service-worker cache signals — an
        # ineligibility marker for explicit pages, an HMAC identity marker the
        # client compares to clear caches on identity change or logout, and
        # the snapshot lifetime when a snapshot is shipped. Never breaks mount.
        try:
            from .security.service_worker import mount_frame_metadata

            mount_msg.update(
                await sync_to_async(mount_frame_metadata)(
                    view_instance, request, mount_msg.get("state_snapshot_signed")
                )
            )
        except Exception:  # noqa: BLE001 — value-free; fail closed on eligibility
            logger.warning("Service-worker cache metadata unavailable for mount")
            mount_msg["sw_cache"] = "no-store"

        # #2966: dj-track-static. A reconnecting client sends the tracked asset
        # URLs its page loaded; report the ones the current static manifest
        # has replaced. Additive, and absent for pages that track nothing.
        track_static = data.get("track_static")
        if track_static:
            try:
                from ._track_static import stale_static_urls

                stale_static = stale_static_urls(track_static)
                if stale_static:
                    mount_msg["stale_static"] = stale_static
            except Exception:  # noqa: BLE001 — an asset check must never break mount
                logger.warning("dj-track-static check failed; no stale assets reported")

        # Optional cache_config (mirrors WS consumer)
        cache_config = self._extract_cache_config(view_instance)
        if cache_config:
            mount_msg["cache_config"] = cache_config

        # handler_config (#2656) — @debounce / @throttle metadata for the
        # client-side rate-limit gate in src/05-handler-rate-limit.js. Same
        # transport as cache_config deliberately: the mount frame is
        # CSP-strict (no inline <script>) and survives the #1610 mount morph,
        # unlike the `window.handlerMetadata` script tag, which the client
        # now reads only as an HTTP-path fallback.
        handler_config = self._extract_handler_config(view_instance)
        if handler_config:
            mount_msg["handler_config"] = handler_config

        # ADR-036: public input contracts retain their dispatch owner rather
        # than being merged into the global handler-name rate-limit map.
        from ._parameter_metadata import parameter_contract_manifest

        parameter_contracts = parameter_contract_manifest(view_instance)
        self._parameter_contracts_active = parameter_contracts is not None
        self._parameter_contract_view = view_path
        if parameter_contracts is not None:
            mount_msg["parameter_contracts"] = parameter_contracts

        # optimistic_rules (DEP-002, WS websocket.py:2823-2826) — descriptor
        # components with tier="optimistic" ship their client-side rules on the
        # mount frame so the client can apply an optimistic UI update before the
        # server round-trip.
        optimistic_rules = self._extract_optimistic_rules(view_instance)
        if optimistic_rules:
            mount_msg["optimistic_rules"] = optimistic_rules

        # upload_configs (WS websocket.py:2828-2834) — views using UploadMixin
        # ship their upload configuration so the client knows the accept/size
        # constraints before the first upload frame.
        upload_manager = getattr(view_instance, "_upload_manager", None)
        if upload_manager:
            upload_state = upload_manager.get_upload_state()
            if upload_state:
                mount_msg["upload_configs"] = {
                    name: info["config"] for name, info in upload_state.items()
                }

        await self.transport.send(mount_msg)
        # From here a server-originated turn (push / tick, #3809) may address
        # this view: before the mount frame the client has nothing to patch.
        self._mount_sent_for = view_instance
        logger.info(
            "Runtime: mounted view %s (session %s)",
            sanitize_for_log(view_path),
            sanitize_for_log(self.session_id),
        )

        # Mount-time queue drain (ADR-022 Iter 3 Phase 3.0, WS
        # websocket.py:2916-2917). Drain push events queued during mount()/on_mount
        # hooks (#1283) and dispatch any start_async()/assign_async() callbacks
        # scheduled in mount() (#1280), AFTER the mount frame establishes the view
        # on the client. CRITICAL (#1391 source-grep pin moved to this location in
        # test_handle_mount_drains_queues.py): WS mount drains ONLY these two
        # queues — NOT the 8-queue ``_flush_all_pending`` that the turn-end event
        # path uses. Preserve EXACTLY: mount establishes the baseline, it does not
        # run a full event turn-end flush. ``_flush_push_events`` is sync (queues
        # the sends fire-and-forget); ``_dispatch_async_work`` takes the event_name
        # (None at mount).
        self._flush_push_events()
        self._dispatch_async_work(None)

    # ------------------------------------------------------------------ #
    # Event dispatch (used by SSE in this PR; WS still uses handle_event)
    # ------------------------------------------------------------------ #

    @_runtime_diagnostic_scope
    async def dispatch_event(self, data: Dict[str, Any], *, turn_source: str = "client") -> None:
        """Dispatch a client event to the mounted view, as one ``event`` turn (ADR-044).

        Frame schema::

            {
                "type": "event",
                "event": "increment",
                "params": {"amount": 1, ...},
            }

        Returns ``noop`` on no-state-change, ``patch`` on diff-able render,
        ``html_update`` otherwise. Mirrors the simplified single-view path
        from the legacy ``_sse_handle_event``.

        Wrapped in the tenant context (Finding #6) so the handler + render see
        the correct tenant in the tenant-scoped managers, cleared on exit.
        """
        from ._exposure import uses_legacy_exposure

        explicit_request = None
        view = self.view_instance
        if view is not None and not uses_legacy_exposure(view):
            try:
                # Capture the current POST before waiting on another turn's
                # lock (or a middleware's await). A later POST may replace the
                # SSE session's request slot.
                explicit_request = await self.transport.explicit_event_request(view)
            except Exception:
                # The inner fail-closed gate emits a static denial for None.
                explicit_request = None

        async def body(turn: Turn) -> None:
            tenant = getattr(self.view_instance, "_tenant", None) if self.view_instance else None
            with _tenant_context(tenant):
                await self._dispatch_event_inner(data, explicit_request=explicit_request)

        if view is None:
            # No view, no view code: the inner path answers "not mounted".
            await body(None)  # type: ignore[arg-type]
            return
        event_name = data.get("event")
        params = data.get("params")
        await run_turn(
            Turn(
                "event",
                self.turn_transport,
                source=turn_source,
                view=view,
                event=event_name if isinstance(event_name, str) else None,
                params=MappingProxyType(params) if isinstance(params, dict) else None,
                scope=self.scope,
                session_id=self._turn_session_id(),
                port=self,
                ref=data.get("ref"),
            ),
            body,
        )

    async def _dispatch_event_inner(
        self, data: Dict[str, Any], *, explicit_request: Any = None
    ) -> None:
        """Event dispatch body (see :meth:`dispatch_event` for the tenant wrapper).

        The handler + render runs inside ``transport.event_context(view)`` (ADR-022
        Iter 2 Phase 2.3a, #1899): on WS this BORROWS the consumer's existing
        ``_render_lock`` + sets ``_processing_user_event`` + the #1677 origin
        channel + observability scopes, so a WS event routed here in the Phase
        2.3b flip serializes against the WS-only tick / server-push / db-notify
        render loops identically (the #560 guard). SSE holds its session lock. The
        view-mounted check runs OUTSIDE the context (we need a non-None view to
        borrow its lock — matching WS, which acquires only after the view exists).
        """
        if not self.view_instance:
            await self.transport.send_error("View not mounted. Please reload the page.")
            return

        # ``reauth_on_event`` (#1777) runs before this, on the turn chain's
        # ``reauth`` built-in (ADR-044 D7): a failed re-check has already
        # answered, closed the transport and cleared ``view_instance``.
        from ._exposure import uses_legacy_exposure

        explicit_lock = (
            contextlib.nullcontext()
            if uses_legacy_exposure(self.view_instance)
            else self._explicit_event_lock
        )
        async with explicit_lock, self.transport.event_context(self.view_instance):
            if self.view_instance is None:
                return
            if not uses_legacy_exposure(self.view_instance):
                try:
                    from ._exposure_auth import aauthorize_event

                    authorized_request = await aauthorize_event(
                        self.view_instance, explicit_request, self._explicit_mount_binding
                    )
                    self.view_instance._djust_event_request = authorized_request
                except Exception:
                    # No exception text/traceback: auth providers may include
                    # credentials or other internal state in their exceptions.
                    self.view_instance = None
                    await self.transport.send_error(
                        "Event authorization failed. Please reload the page.",
                        code="permission_denied",
                    )
                    await self.transport.close(code=4403)
                    return
            try:
                await self._dispatch_event_render(data)
            finally:
                if self.view_instance is not None:
                    self.view_instance.__dict__.pop("_djust_event_request", None)

    @_runtime_diagnostic_scope
    async def _dispatch_event_render(self, data: Dict[str, Any]) -> None:
        """Parse, validate, run the handler, and render one event turn.

        Always invoked inside ``transport.event_context`` (see
        :meth:`_dispatch_event_inner`) so the render serialization + observability
        scope is established for the whole handler+render turn."""
        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

        event_name = data.get("event")
        params: Dict[str, Any] = dict(data.get("params") or {})

        # Event ref echo (#560, ADR-022 Iter 2 Phase 2.0): the client sends a
        # monotonic ``ref`` with each event so it can match responses to requests
        # and distinguish event responses from out-of-band tick / broadcast /
        # async pushes. The runtime echoes it back on BOTH the noop and the
        # update frame (mirrors WS handle_event websocket.py:3119-3121 + the
        # _send_noop / _send_update ref echo at websocket.py:776-780 / 1381-1403).
        # Coerce to int to prevent type confusion (the int/float-only WS rule).
        raw_ref = data.get("ref")
        event_ref: Optional[int] = int(raw_ref) if isinstance(raw_ref, (int, float)) else None

        if not event_name:
            await self.transport.send_error("Missing 'event' field")
            return

        # Bind the mounted view to a local non-None handle. _dispatch_event_render
        # is only ever entered from _dispatch_event_inner AFTER its
        # `if not self.view_instance: return` guard (and inside event_context,
        # which holds the render lock so view_instance can't be nulled mid-turn),
        # so this guard never fires in practice — it narrows Optional[Any] for the
        # type checker and is behavior-equivalent to the caller's contract.
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            await self.transport.send_error("View not mounted. Please reload the page.")
            return

        start_time = time.perf_counter()

        # Strip internal params (matches WS handler)
        cache_request_id = params.pop("_cacheRequestId", None)
        positional_args = params.pop("_args", [])

        # Embedded-child routing (ADR-022 Iter 2 Phase 2.1).
        #
        # Two transport-agnostic child-routing subsystems route the event AWAY
        # from the top-level view before the single-view path below runs (per
        # #1467 canon they are DISTINCT mechanisms with different wire frames):
        #
        #   1. ``view_id`` → a sticky-child LiveView (resolved via
        #      ``_get_all_child_views()``; emits a scoped ``embedded_update``).
        #   2. ``component_id`` → a LiveComponent (resolved via
        #      ``_components``; validates the handler against the COMPONENT and
        #      emits a full-HTML ``component_event`` frame; does NOT reassign
        #      the target view, per #1467).
        #
        # Both were ported from the WS ``_handle_event_inner`` subsystems, which
        # are gone: every transport (WebSocket and SSE alike) routes its events
        # here, so a component or sticky-child event over SSE takes the same
        # path as over a socket (#4087). A view with neither falls straight
        # through to the single-view path — the empty-registry no-op. Each
        # helper returns ``True`` when it fully handled the event (we return),
        # ``False`` to fall through.
        if await self._dispatch_sticky_child_event(
            data, event_name, params, positional_args, event_ref
        ):
            return
        if await self._dispatch_component_event(event_name, params, positional_args, event_ref):
            return

        # v0.7.0 dj_activity gate (ADR-022 Iter 2 Phase 2.3a, #1903). Verbatim
        # transport-agnostic port of the WS ``_handle_event_inner`` gate
        # (websocket.py:3254-3273): if the event was triggered inside a HIDDEN
        # (non-eager) ``{% dj_activity %}`` region, queue it per-activity and send
        # a no-op so the client's loading state clears — the event replays when
        # the panel is next shown (drained by ``_flush_deferred_activity_events``
        # below). The view methods (``is_activity_visible`` / ``_is_activity_eager``
        # / ``_queue_deferred_activity_event``) are the SAME transport-agnostic
        # ActivityMixin API the WS path uses; only the send shape differs (the
        # runtime's self-describing noop vs the WS ``_send_noop``). SSE views with
        # no ``{% dj_activity %}`` region carry no ``_activity`` marker, so the
        # whole block is a no-op for them (parity-improving but zero-cost when
        # unused). Routes LIVE for SSE today (events run through this path since
        # Iter 1, #1887); WS stays on its bespoke gate until Phase 2.3b.
        _activity_name = params.pop("_activity", None)
        if (
            _activity_name
            and hasattr(view, "is_activity_visible")
            and not view.is_activity_visible(_activity_name)
            and not view._is_activity_eager(_activity_name)
        ):
            logger.debug(
                "[djust] Runtime event %r on hidden activity %r — deferring",
                sanitize_for_log(event_name or ""),
                sanitize_for_log(_activity_name),
            )
            # Queued WITHOUT permission/rate-limit check (by design: per-handler
            # auth runs on dispatch via the lock-free re-dispatcher
            # ``_dispatch_single_event``). Per-activity cap bounds memory.
            view._queue_deferred_activity_event(_activity_name, event_name, params)
            # No-op so the client clears its loading state. Mirrors the runtime's
            # skip-render noop shape (type/source/event_name/ref) so the client
            # can match the ack to its pending event (#560 sequencing).
            noop_msg: Dict[str, Any] = {
                "type": "noop",
                "source": "event",
                "event_name": event_name,
            }
            if event_ref is not None:
                noop_msg["ref"] = event_ref
            await self.transport.send(noop_msg)
            return

        # Time-travel record (v0.6.1 dev-only, ADR-022 Iter 2 Phase 2.2). Capture
        # ``state_before`` BEFORE the security check so a PERMISSION-DENIED or
        # VALIDATION-FAILED event STILL records a snapshot (with the error set +
        # state_after == state_before) for the debug panel — mirrors WS
        # ``_handle_event_inner`` (websocket.py:3541-3565), which did
        # ``_tt_start`` → validate → on ``handler is None`` set
        # ``_tt_error = "permission_denied"`` + ``_tt_end`` BEFORE returning.
        # #1907 THE FLIP regression fix: the first flip ran ``record_event_start``
        # AFTER the security check, so a denied handler on a time-travel view
        # recorded ZERO entries (the bespoke recorded one with
        # error="permission_denied"). ``record_event_start`` is a no-op unless the
        # view opted into time-travel, so default views pay nothing.
        from .time_travel import record_event_end, record_event_start

        _tt_snapshot = record_event_start(view, event_name, params, event_ref)
        _tt_error: Optional[str] = None

        # Security
        handler = await _validate_event_security(
            self.transport, event_name, view, self._rate_limiter
        )
        if handler is None:
            # Permission denied / rate-limited / unsafe name — record the denial
            # in the time-travel buffer (with error) before returning, matching the
            # WS bespoke path (websocket.py:3550-3553).
            _tt_error = "permission_denied"
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)
            return

        # Parameter validation
        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler, params, event_name, coerce=coerce, positional_args=positional_args
        )
        if not validation["valid"]:
            logger.error(
                "Runtime: parameter validation failed: %s",
                sanitize_for_log(validation["error"]),
            )
            # Record the validation failure in the time-travel buffer too (the WS
            # bespoke path set ``_tt_error = "validation_failed"`` + ``_tt_end``
            # before the error send, websocket.py:3563-3565).
            _tt_error = "validation_failed"
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)
            await self.transport.send_error(
                validation["error"],
                validation_details={
                    "expected_params": validation["expected"],
                    "provided_params": validation["provided"],
                    "type_errors": validation["type_errors"],
                },
            )
            return

        coerced_params = validation.get("coerced_params", params)
        call_args, call_kwargs = validated_call_arguments(validation)

        # Snapshot pre-handler assigns for change detection.
        from .websocket import _compute_changed_keys, _resolve_skip_render, _snapshot_assigns

        # Identity snapshot for the #700 push_commands-only auto-skip below:
        # {attr: id(value)} over the public assigns. Immune to the deep-copy
        # sentinel false-positives _snapshot_assigns can produce for non-copyable
        # public attrs (querysets, file handles), so a handler that only calls
        # push_event()/push_commands() without touching real state is detected as
        # a true no-op (mirrors WS handle_event websocket.py:3551-3556).
        _fw_attrs: frozenset[str] = getattr(view, "_framework_attrs", frozenset())
        pre_assigns: Optional[Dict[str, Any]]
        pre_identity: Optional[Dict[str, int]]
        _pre_box: Dict[str, Any] = {}
        from .worker_pool import offload_enabled

        if offload_enabled() and not inspect.iscoroutinefunction(handler):
            # Worker pool on (#3074): take both snapshots on the session's
            # thread, in the SAME hop as the sync handler, instead of on the
            # event loop before it. The render lock is held across both, so
            # nothing can change the view between the snapshot and the call.
            _inner_handler = handler

            def handler(*args: Any, **kwargs: Any) -> Any:  # noqa: F811
                _pre_box["assigns"] = _snapshot_assigns(view)
                _pre_box["identity"] = {
                    k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs
                }
                return _inner_handler(*args, **kwargs)

            pre_assigns = pre_identity = None
        else:
            pre_assigns = _snapshot_assigns(view)
            pre_identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        # Call handler. The time-travel record is finalized + pushed in the
        # ``finally`` for BOTH the success and the raising path (mirrors WS
        # ``_handle_event_inner`` websocket.py:3625-3635, which records in a
        # finally so permission-denied / raising handlers still appear in the
        # debug panel). On a raise we set ``_tt_error`` and return early after
        # sending the error frame.
        _handler_start = time.perf_counter()
        try:
            try:
                await _call_handler(handler, call_kwargs or None, positional_args=call_args)
                restrict_diagnostics(view)
            except Exception as exc:
                restrict_diagnostics(view)
                _tt_error = str(exc)[:200] if diagnostics_allowed() else "[redacted]"
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name=event_name,
                    view_class=view.__class__.__name__,
                    logger=logger,
                    log_message=f"Error in handler {view.__class__.__name__}.{sanitize_for_log(event_name)}()",
                )
                await self.transport.send(response)
                return
        finally:
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)
        if _pre_box:
            pre_assigns = _pre_box["assigns"]
            pre_identity = _pre_box["identity"]
        if pre_assigns is None or pre_identity is None:
            # Unreachable: the offloaded wrapper always snapshots before it
            # calls the handler, and a raising handler returned above.
            raise RuntimeError("pre-event snapshot missing")

        # Per-handler percentile telemetry (#1907, THE FLIP). The WS bespoke
        # view-path recorded ``record_handler_timing`` right after a SUCCESSFUL
        # handler call (websocket.py:3645) — a raising handler returns early above
        # before this point, matching the WS path where the record line is
        # unreachable on a raise. Folded behind ``on_handler_timing`` so it stays
        # WS-scoped (SSE no-op). Called defensively (mirroring the existing
        # ``on_view_mounted`` / ``on_event_recorded`` hook calls) so a partial test
        # transport without the hook is a no-op. Best-effort by hook contract.
        on_handler_timing = getattr(self.transport, "on_handler_timing", None)
        if on_handler_timing is not None:
            on_handler_timing(
                view,
                event_name,
                (time.perf_counter() - _handler_start) * 1000.0,
            )

        # Waiter notification (ADR-002 Phase 1b): resolve any pending
        # wait_for_event waiters on the view whose event_name matches. Runs AFTER
        # the handler so new waiters created during this call aren't self-notified.
        # Transport-agnostic; mirrors WS handle_event websocket.py:3608-3616 (and
        # the deferred-event path at websocket.py:1478-1482). No-op when the view
        # has no pending waiters. Best-effort: a waiter-callback failure must never
        # break the event turn (matches WS posture).
        self._notify_waiters_safely(
            view,
            event_name,
            coerced_params or {},
            log_message="Waiter notification for %r failed: %s",
            log_args=(event_name,),
        )

        # An explicit view commits its declared state before the frame goes out
        # (E3); a legacy view persists nothing after an event.
        from ._exposure import uses_legacy_exposure

        target_view = self.view_instance
        # #4107: the post-event snapshot, taken before the commit so the save
        # can tell from it whether any saved attribute changed (and walk
        # nothing when none did). It is the snapshot the render decision below
        # needs anyway: the commit and the client snapshot between here and
        # there do not change the view's state. Off with the shortcut, the
        # snapshot is taken where it always was.
        early_post_assigns: Optional[Dict[str, Any]] = None
        if not uses_legacy_exposure(target_view):
            if (
                _per_turn.on("unchanged_save_skip")
                and target_view is view
                and pre_assigns is not None
                and not getattr(view, "_force_full_html", False)
            ):
                early_post_assigns = _snapshot_assigns(view)
                view.__dict__["_djust_auto_changed"] = _compute_changed_keys(
                    pre_assigns, early_post_assigns
                )
            # A failed explicit save withholds the success frame (E3); the
            # child tree is saved on the render/noop branches below.
            try:
                committed = await self.commit_explicit_turn(
                    target_view, source="event", children=False
                )
            finally:
                # This turn's answer only: a commit that never reached the
                # save must not leave it for a later turn's save to trust.
                view.__dict__["_djust_auto_changed"] = None
            if not committed:
                return

        snapshot_fields = await self._explicit_event_snapshot(view)

        # Auto-detect unchanged state. _resolve_skip_render owns the skip
        # decision (#2834) — it consumes an explicit ``_skip_render`` so a
        # stale True never leaks, and ``_force_full_html`` ALWAYS wins over
        # it (a handler that explicitly requested a forced full-HTML render
        # must not be silently dropped, the #1646 hatch-dropped class) — the
        # same resolution as the WS tick/server_push/db_notify paths. The
        # render branch consumes + resets the force flag (see
        # _render_and_send).
        skip_render = _resolve_skip_render(view)
        force_html = getattr(view, "_force_full_html", False)
        from ._exposure import uses_legacy_exposure

        # The parent's assign snapshot does not include owned child state.
        # A handler can mutate a child without changing a parent declaration.
        has_explicit_children = not uses_legacy_exposure(view) and bool(
            getattr(view, "_child_views", None)
        )
        if not skip_render and not force_html:
            post_assigns = (
                early_post_assigns if early_post_assigns is not None else _snapshot_assigns(view)
            )
            if pre_assigns == post_assigns and not has_explicit_children:
                skip_render = True
            else:
                view._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        # #700: push_commands-only handlers auto-skip the render. When push events
        # are pending and the *identity* of every public attr is unchanged, the
        # handler only emitted push commands (no state mutation) so a VDOM
        # re-render is wasted work (and can trigger morphdom recovery during
        # tours). Identity comparison sidesteps the assigns-snapshot
        # false-positives on non-copyable attrs. Mirrors WS handle_event
        # websocket.py:3867-3891. Guarded by force_html so an explicit
        # full-HTML request still renders.
        if not skip_render and not force_html:
            pending = getattr(view, "_pending_push_events", None)
            if pending:
                post_identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}
                if pre_identity == post_identity and not has_explicit_children:
                    skip_render = True

        from ._async_batch import AsyncBatch

        async_batch = AsyncBatch(view)
        has_async = bool(async_batch.token)

        if skip_render:
            if not await self._persist_explicit_children_after_event(view):
                return
            # (_skip_render was already consumed by _resolve_skip_render
            # above — it is the single owner of that reset, #2834.)
            # Legacy: drain ALL queued side-effects BEFORE the noop, matching the WS
            # bespoke skip-render path (websocket.py:3941 — ``await
            # self._flush_all_pending()`` then ``_send_noop``). #1907 THE FLIP:
            # the runtime skip-render branch previously only flushed push_events,
            # so a state-unchanging handler that queued navigation (e.g.
            # ``live_redirect()``) dropped its ``navigation`` frame on the WS path
            # once events routed here (caught by
            # ``test_live_redirect_from_state_unchanging_handler_emits_navigation_frame``).
            # ``_flush_all_pending`` is the single 8-queue drain (#1646) and
            # includes ``_flush_push_events``, so this also covers the push drain.
            if not snapshot_fields:
                await self._flush_all_pending()
            # ref echo (#560) + source/event_name (#560 sequencing) on the noop
            # frame so the client can match the ack to its pending event (clear
            # _pendingEventRefs / stop the right loading state) and distinguish it
            # from out-of-band pushes. WS echoes ``ref`` on its noop
            # (_send_noop, websocket.py:776-781); ``source``/``event_name`` are a
            # runtime ADD (the WS noop omits them) for self-describing #560
            # sequencing — harmless extra fields the client tolerates.
            noop_msg = {
                "type": "noop",
                "source": "event",
                "event_name": event_name,
            }
            if event_ref is not None:
                noop_msg["ref"] = event_ref
            if has_async:
                noop_msg["async_pending"] = True
            noop_msg.update(async_batch.fields())
            noop_msg.update(snapshot_fields)
            await self.transport.send(noop_msg)
            if snapshot_fields:
                # Explicit acknowledgements publish (or invalidate) navigation
                # state before a queued redirect triggers before-navigate.
                # Preserve the legacy side-effect ordering unchanged.
                await self._flush_all_pending()
            # Dispatch background work UNCONDITIONALLY after the turn (matches WS
            # handle_event websocket.py:4235, NOT the legacy SSE which gated this
            # on has_async). The captured batch includes named and legacy work
            # (both task formats, as ``has_pending_async_work`` reads them since
            # #2963), and its token was advertised in the acknowledgement. An
            # empty batch is a no-op; it must not capture later unrelated
            # queued work. Converging onto the WS behavior here FIXES the legacy
            # SSE drop of ``start_async`` work (#1887 / #1646).
            self._dispatch_async_work(event_name, async_batch)
            # dj_activity flush (Phase 2.3a, #1903): a skip-render handler can
            # still flip an activity visible via set_activity_visible(); drain its
            # queue so deferred events for that panel arrive in the same
            # round-trip. Mirrors the WS flush placement after the async dispatch
            # (websocket.py:4283-4294). Safe + no-op when no activities exist.
            await self._flush_deferred_activity_events()
            return

        # Render — scoped to one bound component when that is all that
        # changed (ADR-032 D1/D5); ``_render_and_send`` checks the other gates.
        await self._render_and_send(
            event_name=event_name,
            cache_request_id=cache_request_id,
            has_async=has_async,
            async_batch=async_batch,
            force_html=force_html,
            event_ref=event_ref,
            scoped_component=_scoped_component_for(view, getattr(view, "_changed_keys", None)),
            snapshot_fields=snapshot_fields,
        )

        # Dispatch background work UNCONDITIONALLY after the render (WS parity,
        # websocket.py:4235): start_async / @background callbacks run off-thread
        # and stream their re-rendered result via the transport when ready.
        self._dispatch_async_work(event_name, async_batch)

        # dj_activity flush (Phase 2.3a, #1903): if this handler flipped any
        # activity visible, drain its deferred-event queue now so in-flight events
        # for that panel are delivered in-order in the SAME round-trip. The flush
        # is async + awaited inline (websocket.py:4290-4294). Safe to call even
        # when no activities exist (the flush early-returns on an empty queue).
        await self._flush_deferred_activity_events()

        logger.debug(
            "Runtime: event '%s' handled in %.1fms",
            sanitize_for_log(event_name),
            (time.perf_counter() - start_time) * 1000,
        )

    # ------------------------------------------------------------------ #
    # dj_activity deferred-event drain (ADR-022 Iter 2 Phase 2.3a, #1903)
    #
    # The transport-agnostic flush + lock-free re-dispatcher that the runtime
    # uses to drain queued events when a panel flips visible. The ActivityMixin
    # flush method (mixins/activity.py:212) is consumer-blind: it takes any
    # object exposing ``_dispatch_single_event(target_view, event_name, params)``
    # and awaits it once per queued event. WS passes the consumer; the runtime
    # passes ITSELF (option (a) — no change to the view-method semantics). The
    # re-dispatcher runs INSIDE ``event_context`` (the flush is invoked from
    # ``_dispatch_event_render``, which is wrapped in ``transport.event_context``),
    # which on WS already holds the borrowed consumer ``_render_lock`` — so, like
    # the WS ``_dispatch_single_event`` (websocket.py:1467), this method MUST NOT
    # re-acquire any lock and MUST NOT re-enter ``event_context`` (that would
    # deadlock on the non-reentrant ``asyncio.Lock``). On SSE ``event_context`` is
    # a no-op, so there is no lock either way. Each queued event is re-VALIDATED
    # through the full auth stack here (a denied queued event never dispatches).
    # ------------------------------------------------------------------ #

    @_runtime_diagnostic_scope
    async def _flush_deferred_activity_events(self) -> None:
        """Drain the view's deferred-activity queues via the runtime re-dispatcher.

        Thin wrapper: hands the runtime itself to the ActivityMixin flush as the
        consumer-like dispatcher. No-op when the view has no ActivityMixin or no
        queued events. Best-effort — a drain bug must never break the event turn
        (mirrors the WS flush call-site guard, websocket.py:4290-4294)."""
        view = self.view_instance
        if view is None or not hasattr(view, "_flush_deferred_activity_events"):
            return
        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

        try:
            await view._flush_deferred_activity_events(self)
        except Exception:  # noqa: BLE001 — never fail the event for a drain bug
            restrict_diagnostics(view)
            restrict_diagnostics(self.view_instance)
            if diagnostics_allowed():
                logger.exception("dj_activity: runtime deferred-event flush raised")
            else:
                logger.error("Protected deferred-event flush failed")

    async def _dispatch_single_event(
        self,
        target_view: Any,
        event_name: str,
        params: Dict[str, Any],
        event_ref: Optional[int] = None,
    ) -> None:
        """Replay one deferred ``dj_activity`` event as an ``event``/``deferred`` turn.

        ADR-044: the replay runs a handler, so it runs the turn chain. See
        :meth:`_dispatch_single_event_body` for the dispatch itself.
        """
        if target_view is None:
            return
        turn = Turn(
            "event",
            self.turn_transport,
            source="deferred",
            view=target_view,
            event=event_name if isinstance(event_name, str) else None,
            params=MappingProxyType(params) if isinstance(params, dict) else None,
            scope=self.scope,
            session_id=self._turn_session_id(),
            port=self,
            ref=event_ref,
        )
        turn._locked = True  # the flush runs inside the event's event_context
        await run_turn(
            turn,
            lambda _turn: self._dispatch_single_event_body(
                target_view, event_name, params, event_ref
            ),
        )

    @_runtime_diagnostic_scope
    async def _dispatch_single_event_body(
        self,
        target_view: Any,
        event_name: str,
        params: Dict[str, Any],
        event_ref: Optional[int] = None,
    ) -> None:
        """Lock-free single queued-event re-dispatch (the WS ``_dispatch_single_event`` twin).

        Re-runs validate → handler → render for ONE deferred ``(event_name, params)``
        pair WITHOUT acquiring a lock and WITHOUT re-entering ``event_context`` —
        the caller (``_flush_deferred_activity_events`` → the ActivityMixin flush)
        already runs inside ``event_context`` (which on WS holds the borrowed
        ``_render_lock``). Invariants mirror the WS path (websocket.py:1456-1626):

        * The activity gate is NOT re-run — events reach this path only because
          the flush already decided they should dispatch (re-gating would re-queue
          them forever). ``params`` arrives with ``_activity`` stripped by the
          flush.
        * Still runs the FULL security pipeline (unsafe name, missing handler,
          decorator allowlist, per-handler rate limit, permission check), so a
          denied queued event is dropped exactly as a live one would be.
        * Exceptions are logged + swallowed so one bad queued event cannot break
          the rest of the drain (the flush also catches, defense-in-depth).
        """
        from .websocket import _compute_changed_keys, _resolve_skip_render, _snapshot_assigns

        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

        restrict_diagnostics(target_view)

        # --- security / validation (shared with the live path) -------------
        handler = await _validate_event_security(
            self.transport,
            event_name,
            target_view,
            self._rate_limiter,
            root_view=self.view_instance,
        )
        if handler is None:
            return

        positional_args = (params or {}).pop("_args", []) if isinstance(params, dict) else []
        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler,
            params or {},
            event_name,
            coerce=coerce,
            positional_args=positional_args,
        )
        if not validation["valid"]:
            logger.warning(
                "Runtime deferred-activity event %r failed param validation: %s",
                sanitize_for_log(event_name or ""),
                sanitize_for_log(validation["error"]),
            )
            return
        coerced_params = validation.get("coerced_params", params)
        call_args, call_kwargs = validated_call_arguments(validation)

        # --- handler invocation -------------------------------------------
        pre_assigns = _snapshot_assigns(self.view_instance)
        try:
            await _call_handler(handler, call_kwargs or None, positional_args=call_args)
            restrict_diagnostics(target_view)
        except Exception:  # noqa: BLE001 — never break the flush
            restrict_diagnostics(target_view)
            if diagnostics_allowed():
                logger.exception(
                    "Runtime deferred-activity event %r on %s raised during dispatch",
                    sanitize_for_log(event_name or ""),
                    type(target_view).__name__,
                )
            else:
                logger.error("Protected deferred event failed")
            return

        # Waiter notification (ADR-002) — same posture as the live path.
        self._notify_waiters_safely(
            target_view,
            event_name,
            coerced_params or {},
            log_message="Waiter notification for deferred %r failed: %s",
            log_args=(event_name or "",),
        )

        # --- render + emit one frame (mirrors the live skip/render split) --
        # Bind the mounted view to a non-None local for the direct-attribute
        # writes below (caller guarantees a mounted view on this re-dispatch path;
        # the guard narrows Optional[Any] and is behavior-equivalent).
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            return
        # Deferred events have the same commit-before-ack contract as live
        # events, including handlers that explicitly request no render (#3253).
        from ._exposure import uses_legacy_exposure

        legacy = uses_legacy_exposure(view)
        snapshot_fields: Dict[str, Any] = {}
        if not legacy:
            if not await self.commit_explicit_turn(view, source="event", children=False):
                return
            snapshot_fields = await self._explicit_event_snapshot(view)

        # _resolve_skip_render owns the skip decision (#2834/#2847) — the same
        # resolution as dispatch_event / server_push / db_notify / tick. This
        # twin previously read the two flags inline and resolved the collision
        # the pre-#2834 way: with BOTH flags set the noop branch won, silently
        # dropping the forced full render AND leaking ``_force_full_html``
        # into a later unrelated turn (the #1646 "hatch dropped" class). The
        # helper consumes ``_skip_render`` whenever set (a stale True never
        # leaks) and lets ``_force_full_html`` win.
        skip_render = _resolve_skip_render(view)
        force_html = getattr(view, "_force_full_html", False)
        if not skip_render and not force_html:
            post_assigns = _snapshot_assigns(view)
            if pre_assigns == post_assigns:
                skip_render = True
            else:
                view._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        from ._async_batch import AsyncBatch

        async_batch = AsyncBatch(view)
        has_async = bool(async_batch.token)

        if skip_render:
            if not await self._persist_explicit_children_after_event(view):
                return
            # (_skip_render was already consumed by _resolve_skip_render —
            # it is the single owner of that reset, #2834/#2847.)
            self._flush_push_events()
            noop_msg: Dict[str, Any] = {
                "type": "noop",
                "source": "event",
                "event_name": event_name,
            }
            if event_ref is not None:
                noop_msg["ref"] = event_ref
            if has_async:
                noop_msg["async_pending"] = True
            noop_msg.update(async_batch.fields())
            noop_msg.update(snapshot_fields)
            await self.transport.send(noop_msg)
            self._dispatch_async_work(event_name, async_batch)
            return

        await self._render_and_send(
            event_name=event_name,
            has_async=has_async,
            async_batch=async_batch,
            force_html=force_html,
            event_ref=event_ref,
            scoped_component=_scoped_component_for(view, getattr(view, "_changed_keys", None)),
            snapshot_fields=snapshot_fields,
        )
        self._dispatch_async_work(event_name, async_batch)

    # ------------------------------------------------------------------ #
    # Time-travel push hook (ADR-022 Iter 2 Phase 2.2)
    # ------------------------------------------------------------------ #

    def _notify_waiters_safely(
        self,
        view: Any,
        event_name: str,
        params: Dict[str, Any],
        *,
        log_message: str,
        log_args: Tuple[Any, ...],
    ) -> None:
        """Notify once, preserving owner restrictions and legacy warning text."""
        from ._exposure_diagnostics import (
            diagnostic_scope,
            diagnostics_allowed,
            restrict_diagnostics,
            watch_diagnostic_owner,
        )

        with diagnostic_scope():
            watch_diagnostic_owner(self, "view_instance")
            restrict_diagnostics(view)
            if not hasattr(view, "_notify_waiters"):
                return
            try:
                view._notify_waiters(event_name, params)
            except Exception as exc:  # noqa: BLE001 — waiter bugs must not break events
                restrict_diagnostics(view)
                restrict_diagnostics(self.view_instance)
                if diagnostics_allowed():
                    logger.warning(
                        log_message,
                        *(sanitize_for_log(str(arg)) for arg in log_args),
                        exc,
                    )
                else:
                    logger.warning("Protected waiter notification failed")

    @_runtime_diagnostic_scope
    async def _push_tt_event(self, view: Any, snapshot: Any) -> None:
        """Invoke the transport's ``on_event_recorded`` hook after a record.

        Replaces the WS ``_maybe_push_tt_event`` direct send (websocket.py:5267)
        with a transport-blind dispatch: WS emits the DEBUG-gated
        ``time_travel_event`` frame, SSE no-ops. Forward-compat: transports
        predating the hook (the ``on_view_mounted`` getattr precedent) are
        tolerated. Best-effort — a debug-frame failure must never break the
        event turn."""
        if snapshot is None:
            return
        hook = getattr(self.transport, "on_event_recorded", None)
        if hook is None:
            return
        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

        restrict_diagnostics(view)
        try:
            await hook(view, snapshot)
        except Exception:  # noqa: BLE001 — dev-only time-travel push; degrade silently
            restrict_diagnostics(view)
            restrict_diagnostics(self.view_instance)
            if diagnostics_allowed():
                logger.exception("Runtime: time_travel on_event_recorded hook failed")
            else:
                logger.error("Protected time-travel notification failed")

    # ------------------------------------------------------------------ #
    # Embedded-child routing (ADR-022 Iter 2 Phase 2.1)
    #
    # Two transport-agnostic child-routing subsystems, ported from the WS
    # ``_handle_event_inner`` (websocket.py) verbatim for the security-critical
    # bits. WS keeps its own copy until Phase 2.3; these are the runtime ADDs.
    # ------------------------------------------------------------------ #

    async def _dispatch_sticky_child_event(
        self,
        data: Dict[str, Any],
        event_name: str,
        params: Dict[str, Any],
        positional_args: List[Any],
        event_ref: Optional[int],
    ) -> bool:
        """Route a ``view_id``-targeted event to a sticky/embedded child view.

        Mirrors the WS ``_handle_event_inner`` sticky-child block
        (websocket.py:3176-3198 + the embedded framing at ~4010-4019):

        * resolve the child via ``view_instance._get_all_child_views()``,
        * validate the handler against the CHILD (``target_view``), call it,
        * notify the child's waiters (ADR-002), render the child's template via
          the single-sourced :func:`~djust.websocket.render_embedded_child_html`,
        * emit a scoped ``embedded_update {view_id, html, event_name}`` frame.

        Security: the client-supplied ``view_id`` is NEVER echoed into the
        user-facing error string — only logged sanitized via
        ``sanitize_for_log(view_id)`` in the structured ``extra`` (verbatim from
        the WS path, websocket.py:3191-3197).

        Returns ``True`` if the event was routed to a child (caller returns),
        ``False`` to fall through to the single-view path (the SSE no-op case:
        no ``view_id``, or no child registry, or the id is the top-level view).
        """
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            return False
        view_id = params.pop("view_id", None)
        if not view_id or view_id == getattr(view, "_view_id", None):
            # No child routing requested (or the id IS the top-level view):
            # fall through to the single-view path.
            return False

        all_children = view._get_all_child_views() if hasattr(view, "_get_all_child_views") else {}
        target_view = all_children.get(view_id)
        if target_view is None:
            target_view = _explicit_descendant(view, view_id)
        if target_view is None:
            # Security: don't echo a client-supplied view_id into the
            # user-facing error string. The id is already logged via the
            # structured event for callers that need to trace it.
            await self.transport.send_error(
                "Embedded view not found",
                extra={"view_id": sanitize_for_log(view_id)},
            )
            return True

        from ._exposure import ExposureError, uses_legacy_exposure

        explicit_child = not uses_legacy_exposure(target_view)
        event_request = getattr(view, "_djust_event_request", None)

        def resolve_explicit_child() -> ChildStateSession | None:
            from ._exposure_children import child_event_adapter
            from ._exposure_sessions import request_binding
            from .auth.core import check_view_auth

            if (
                uses_legacy_exposure(view)
                or event_request is None
                or request_binding(event_request) != self._explicit_mount_binding
            ):
                raise ExposureError("Missing current child event authorization")
            adapter = child_event_adapter(target_view, view, event_request, require_delivered=True)
            target_view.request = event_request
            if check_view_auth(target_view, event_request, event=True) is not None:
                raise ExposureError("Child event authorization denied")
            return adapter

        if explicit_child:
            try:
                await sync_to_async(resolve_explicit_child)()
            except Exception:  # noqa: BLE001 — fail closed without provider values
                await self.transport.send_error(
                    "Child event authorization failed. Please reload the page.",
                    code="permission_denied",
                )
                return True

        # Validate the handler against the CHILD (not the parent) — mirrors WS
        # using ``target_view`` for handler lookup (websocket.py:3498).
        handler = await _validate_event_security(
            self.transport,
            event_name,
            target_view,
            self._rate_limiter,
            root_view=self.view_instance,
        )
        if handler is None:
            return True

        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler, params, event_name, coerce=coerce, positional_args=positional_args
        )
        if not validation["valid"]:
            logger.error(
                "Runtime: parameter validation failed (embedded child): %s",
                sanitize_for_log(validation["error"]),
            )
            await self.transport.send_error(
                validation["error"],
                validation_details={
                    "expected_params": validation["expected"],
                    "provided_params": validation["provided"],
                    "type_errors": validation["type_errors"],
                },
            )
            return True

        coerced_params = validation.get("coerced_params", params)
        call_args, call_kwargs = validated_call_arguments(validation)

        # Time-travel record (ADR-022 Iter 2 Phase 2.2). For a sticky-child event
        # the snapshot records against the CHILD (``target_view``) — the child is
        # a full LiveView with its own ``_time_travel_buffer`` (#1467). Mirrors
        # the WS path's per-target ``_tt_start(target_view, ...)`` /
        # ``_tt_end(target_view, ...)`` (websocket.py:3541/3634). No-op unless the
        # child opted into time-travel.
        from .time_travel import record_event_end, record_event_start

        _tt_snapshot = record_event_start(target_view, event_name, params, event_ref)
        _tt_error: Optional[str] = None

        try:
            try:
                await _call_handler(handler, call_kwargs or None, positional_args=call_args)
            except Exception as exc:
                if explicit_child:
                    _tt_error = "Child event failed"
                    await self.transport.send_error("Child event failed.", code="event_error")
                    return True
                _tt_error = str(exc)[:200]
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name=event_name,
                    view_class=target_view.__class__.__name__,
                    logger=logger,
                    log_message=(
                        f"Error in embedded child {target_view.__class__.__name__}."
                        f"{sanitize_for_log(event_name)}()"
                    ),
                )
                await self.transport.send(response)
                return True
        finally:
            record_event_end(target_view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(target_view, _tt_snapshot)

        # Waiter notification (ADR-002 Phase 1b) — resolve waiters on the CHILD
        # view. Best-effort: a waiter bug must never break the event.
        self._notify_waiters_safely(
            target_view,
            event_name,
            coerced_params or {},
            log_message="Waiter notification for embedded child %r failed: %s",
            log_args=(event_name,),
        )

        # An explicit child's declared state is saved with the page (below);
        # a legacy child keeps nothing across a reconnect.
        if explicit_child:
            try:
                from .auth.core import enforce_object_permission

                await sync_to_async(resolve_explicit_child)()
                await sync_to_async(enforce_object_permission)(target_view, event_request)
                # The shared post-render batch saves the owner and reconciles
                # nested slots together; no separate pre-render child flush.
            except Exception:  # noqa: BLE001 — no legacy save or success frame on failure
                await self.transport.send_error(
                    "Child state unavailable. Please reload the page.", code="state_error"
                )
                return True

        # Render just the child's subtree (single-sourced render core) and emit
        # the scoped full-HTML ``embedded_update`` frame. Mirrors the WS
        # ``_render_embedded_child`` + ``_emit_full_html_update("embedded_child")``
        # + the ``embedded_update`` send (websocket.py:3905-3920 / 4010-4018).
        from .websocket import _emit_full_html_update, render_embedded_child_html

        try:
            html = await sync_to_async(render_embedded_child_html)(target_view)
        except Exception:
            if not explicit_child:
                raise
            await self.transport.send_error("Child rendering unavailable.", code="render_error")
            return True
        if explicit_child and not await self._persist_explicit_children_after_event(view):
            return True
        _emit_full_html_update(target_view, "embedded_child", event_name, html, 0)

        msg: Dict[str, Any] = {
            "type": "embedded_update",
            "view_id": view_id,
            "html": html,
            "event_name": event_name,
        }
        if event_ref is not None:
            msg["ref"] = event_ref
        from ._async_batch import AsyncBatch

        child_batch = AsyncBatch(target_view)
        msg.update(child_batch.fields())
        await self._send_render_frame(msg)
        await self._flush_all_pending()

        # Child side effects carry their own audio scope; drain the child's queue.
        self._flush_push_events(target_view)
        # Dispatch any background work the child handler scheduled (WS parity).
        if explicit_child:
            from ._child_async import dispatch_child_work

            assert child_batch is not None
            dispatch_child_work(self, target_view, event_name, child_batch)
            # Work the handler queued on another explicit child (a sibling or
            # descendant) runs under that child's owner too, not at its next
            # event (ADR-038 E3-3, decided 2026-09-22).
            self._dispatch_explicit_child_queues(event_name)
        else:
            from ._child_async import dispatch_legacy_child_work

            dispatch_legacy_child_work(self, target_view, view_id, event_name, child_batch)
        return True

    async def _dispatch_component_event(
        self,
        event_name: str,
        params: Dict[str, Any],
        positional_args: List[Any],
        event_ref: Optional[int],
    ) -> bool:
        """Route a ``component_id``-targeted event to a child LiveComponent.

        Mirrors the WS ``_handle_event_inner`` component block
        (websocket.py:3336/3354-3479 + the ``component_event`` framing at
        ~4024-4032). Per #1467 canon, ``component_id`` routing does NOT reassign
        ``target_view`` — the handler runs on the COMPONENT but waiters and the
        emitted frame are scoped to the PARENT view (LiveComponents have no
        separate VDOM/waiter buffer in Phase 1).

        Key invariants carried verbatim from WS:

        * the handler is validated against the COMPONENT, not the parent;
        * ``_notify_waiters`` is called on the PARENT view with ``component_id``
          injected into the kwargs so predicates can disambiguate which
          component fired (websocket.py:3468-3469);
        * the parent re-renders to full HTML (``component_event`` frame) because
          component VDOM is separate from the parent's.

        Returns ``True`` if a ``component_id`` was present (handled here),
        ``False`` to fall through (the SSE no-op case + every plain event).
        """
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            return False
        component_id = params.get("component_id")
        if not component_id:
            return False

        component = view._components.get(component_id) if hasattr(view, "_components") else None
        if not component:
            # A descriptor's `BoundComponent` is registered in
            # `view._components` by `LiveComponent.__get__` (ADR-031 D2), so
            # the lookup above finds it and this branch is normally not
            # reached — descriptor events now take the validated
            # component-dispatch path below, as events on an instance-assigned
            # component always have.
            #
            # It remains the right fall-through for a descriptor nothing has
            # materialised yet: the name is in `_component_descriptors`, but
            # `__get__` has not run, so `_components` has no entry. Such a
            # descriptor still takes a `component_id` — `__set_name__` wires
            # its `Meta.event` onto the view, and `_make_event_handler`'s
            # view-level alias resolves the instance from that parameter
            # itself. Erroring here would make it unreachable.
            if component_id in getattr(type(view), "_component_descriptors", {}):
                return False

            # Verbatim WS shape (websocket.py:3358-3362): the component_id is
            # server-assigned (not free-form client text), so echoing it in the
            # error is the existing behavior.
            error_msg = f"Component not found: {component_id}"
            logger.error("Runtime: %s", sanitize_for_log(error_msg))
            await self.transport.send_error(error_msg)
            return True

        # Validate the handler against the COMPONENT (websocket.py:3380-3382).
        handler = await _validate_event_security(
            self.transport, event_name, component, self._rate_limiter, root_view=view
        )
        if handler is None:
            return True

        # Strip component_id from the params passed to the handler (the handler
        # signature never expects it). Mirrors websocket.py:3388-3389.
        event_data = dict(params)
        event_data.pop("component_id", None)

        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler, event_data, event_name, coerce=coerce, positional_args=positional_args
        )
        if not validation["valid"]:
            logger.error(
                "Runtime: parameter validation failed (component): %s",
                sanitize_for_log(validation["error"]),
            )
            await self.transport.send_error(
                validation["error"],
                validation_details={
                    "expected_params": validation["expected"],
                    "provided_params": validation["provided"],
                    "type_errors": validation["type_errors"],
                },
            )
            return True

        coerced_event_data = validation.get("coerced_params", event_data)
        call_args, call_kwargs = validated_call_arguments(validation)

        # Time-travel record (ADR-022 Iter 2 Phase 2.2). Per #1467 canon a
        # LiveComponent has NO separate time-travel buffer in Phase 1, so the
        # snapshot records against the PARENT ``view`` (not the component) —
        # state the component pushes into the parent via ``send_parent`` shows up
        # in state_before/state_after. Mirrors WS ``_tt_start_c(self.view_instance,
        # ...)`` / ``_tt_end_c(self.view_instance, ...)`` (websocket.py:3424/3489).
        # Recorded against the pre-strip ``params`` to match WS. No-op unless the
        # parent opted into time-travel.
        from .time_travel import record_event_end, record_event_start

        _tt_snapshot = record_event_start(view, event_name, params, event_ref)
        _tt_error: Optional[str] = None

        # ADR-032: a bound component's event may patch only its own subtree
        # when the handler changed nothing else — decided from the snapshot,
        # not from the template alone (decision driver 2).
        from .components.base import BoundComponent
        from .websocket import _compute_changed_keys, _snapshot_assigns

        from ._component_subscriptions import ComponentDeclaration

        pre_assigns = (
            _snapshot_assigns(view)
            if isinstance(component, (BoundComponent, ComponentDeclaration))
            else None
        )
        try:
            try:
                await _call_handler(handler, call_kwargs or None, positional_args=call_args)
            except Exception as exc:
                _tt_error = str(exc)[:200]
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name=event_name,
                    view_class=view.__class__.__name__,
                    logger=logger,
                    log_message=(
                        f"Error in {view.__class__.__name__}."
                        f"{sanitize_for_log(event_name)}() (component event)"
                    ),
                )
                await self.transport.send(response)
                return True
        finally:
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)

        # Propagate the component event to the PARENT view's waiters with the
        # component_id injected (ADR-002 Phase 1b/1c, websocket.py:3456-3479).
        # Before the save, as the view route notifies before it commits: a
        # failed or deferred save must not hide the event from its waiters.
        notify_kwargs = dict(coerced_event_data or {})
        notify_kwargs.setdefault("component_id", component_id)
        self._notify_waiters_safely(
            view,
            event_name,
            notify_kwargs,
            log_message="Waiter notification for component event %r on %s failed: %s",
            log_args=(event_name, component_id),
        )

        # Component events return before the ordinary view event commit below,
        # so commit here. An explicit view commits (E3) on EVERY component
        # event: a declaration's output handler, or a component's
        # ``send_parent``, can write the view's declared fields (#3211).
        #
        # After the commit, every component frame carries the refreshed signed
        # client snapshot, as the view route's frames do: the commit moved the
        # ``persist="server"`` fields to this turn, and a reconnect with the
        # client's older token would restore them next to ``persist="client"``
        # fields from an earlier turn (#3231).
        snapshot_fields: Dict[str, Any] = {}
        if view is self.view_instance:
            from ._exposure import uses_legacy_exposure

            if not uses_legacy_exposure(view):
                if not await self.commit_explicit_turn(view, source="event"):
                    return True
                snapshot_fields = await self._explicit_event_snapshot(view)

        from ._async_batch import AsyncBatch

        async_batch = AsyncBatch(view)

        # A handler that set ``self._view._skip_render`` asked for no render
        # this turn. ``_resolve_skip_render`` is the single owner of that flag
        # on every route (#2834): it consumes it, and ``_force_full_html`` still
        # wins. Without it here the flag rendered anyway and then leaked into
        # the next view event, which answered ``noop`` (#2924).
        from .websocket import _resolve_skip_render

        if _resolve_skip_render(view):
            await self._send_component_noop(event_name, event_ref, async_batch, snapshot_fields)
            return True

        # ADR-032 D5: the scoped path first. Same helper as the runtime event
        # path; the frame is the ``patch`` frame that path emits.
        changed = None
        if pre_assigns is not None and not getattr(view, "_force_full_html", False):
            changed = _compute_changed_keys(pre_assigns, _snapshot_assigns(view))
            # A click whose handler changed nothing (close on a closed sheet,
            # a second "accept", a push-only handler) is a ``noop``, as the
            # view route answers — not a page render shipped as a 30 KB
            # ``html_update`` (#2922). ``_flush_all_pending`` below drains any
            # push events before the noop goes out, as on the view route.
            if not changed:
                await self._send_component_noop(event_name, event_ref, async_batch, snapshot_fields)
                return True

        if changed and _scoped_component_for(view, changed) is component:
            _scoped_start = time.perf_counter()
            scoped = await self._render_scoped_component(view, component)
            if scoped is not None:
                await self._send_scoped_patch_frame(
                    view,
                    scoped,
                    (time.perf_counter() - _scoped_start) * 1000,
                    event_name=event_name,
                    event_ref=event_ref,
                    async_batch=async_batch,
                    snapshot_fields=snapshot_fields,
                )
                await self._flush_all_pending()
                self._dispatch_async_work(event_name, async_batch)
                return True

        # Component VDOM is separate from the parent's, so re-render the parent
        # to full HTML and emit a ``component_event`` frame (websocket.py:4024-4032).
        from .websocket import _emit_full_html_update

        try:
            html, _patches, version = await sync_to_async(view.render_with_diff)()
        finally:
            _drop_processor_replay(view)
        # The forced full HTML this render honoured is consumed here, as
        # ``_render_and_send`` consumes it on the view route; left set, every
        # later component_id event would bypass the noop and scoped branches
        # until a view-route turn reset it (#2923 review).
        if getattr(view, "_force_full_html", False):
            view._force_full_html = False
        if html and hasattr(view, "_strip_comments_and_whitespace"):
            html = view._strip_comments_and_whitespace(html)
        if html and hasattr(view, "_extract_liveview_content"):
            html = view._extract_liveview_content(html)
        _emit_full_html_update(view, "component_event", event_name, html, version)

        wire_version = self.transport.next_client_version(html, version)
        msg: Dict[str, Any] = {
            "type": "html_update",
            "html": html,
            "version": wire_version,
            "event_name": event_name,
            "source": "event",
        }
        if event_ref is not None:
            msg["ref"] = event_ref
        msg.update(async_batch.fields())
        msg.update(snapshot_fields)
        await self._send_render_frame(msg)
        await self._flush_all_pending()

        # Dispatch any background work the component handler scheduled (WS parity).
        self._dispatch_async_work(event_name, async_batch)
        return True

    async def _send_component_noop(
        self,
        event_name: str,
        event_ref: Optional[int],
        async_batch: "AsyncBatch",
        snapshot_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        """End a ``component_id`` turn that renders nothing: drain the queued
        side effects, answer ``noop`` (advertising the captured background
        batch) and start that batch — the view route's skip shape (#2922,
        #2924). An explicit view's noop carries the refreshed client snapshot,
        and its queued side effects follow it, as on the view route (#3231)."""
        if not snapshot_fields:
            await self._flush_all_pending()
        noop_msg: Dict[str, Any] = {
            "type": "noop",
            "source": "event",
            "event_name": event_name,
        }
        if event_ref is not None:
            noop_msg["ref"] = event_ref
        noop_msg.update(async_batch.fields())
        if snapshot_fields:
            noop_msg.update(snapshot_fields)
        await self.transport.send(noop_msg)
        if snapshot_fields:
            # Publish (or invalidate) navigation state before a queued
            # redirect triggers before-navigate, as the view route does.
            await self._flush_all_pending()
        self._dispatch_async_work(event_name, async_batch)
        await self._flush_deferred_activity_events()

    # ------------------------------------------------------------------ #
    # URL-change dispatch (shared between WS and SSE in this PR)
    # ------------------------------------------------------------------ #

    async def dispatch_url_change(self, data: Dict[str, Any]) -> None:
        """Handle a URL change frame (popstate or dj-patch click): a ``params`` turn.

        Calls ``handle_params(params, uri)`` then re-renders + sends patches,
        inside the turn-middleware chain (ADR-044). Mirrors the legacy
        ``LiveViewConsumer.handle_url_change`` (now a thin shim over this method).

        Wrapped in the tenant context (Finding #6) so handle_params + the
        object-permission re-check + render see the correct tenant.
        """
        view = self.view_instance
        if view is None:
            await self.transport.send_error("View not mounted")
            return
        params = data.get("params")
        await run_turn(
            Turn(
                "params",
                self.turn_transport,
                source="url_change",
                view=view,
                params=MappingProxyType(params) if isinstance(params, dict) else None,
                scope=self.scope,
                session_id=self._turn_session_id(),
                port=self,
            ),
            lambda turn: self._dispatch_url_change_turn(view, data),
        )

    async def _dispatch_url_change_turn(self, view: Any, data: Dict[str, Any]) -> None:
        """The body of a ``params`` turn (see :meth:`dispatch_url_change`)."""
        # URL renders participate in the same mutation order as events and
        # background results. Otherwise their HTML and owner contracts can
        # describe different turns. Navigation may replace the owner while
        # this task waits for the borrowed transport lock.
        from ._exposure import uses_legacy_exposure

        # An explicit route change mutates declared state with no inbound event
        # request: authorize it fresh, like background turns (ADR-038 D-l).
        explicit = not uses_legacy_exposure(view)
        explicit_lock = self._explicit_event_lock if explicit else contextlib.nullcontext()
        async with explicit_lock, self.transport.event_context(view):
            if self.view_instance is not view:
                await self.transport.send_error("View changed. Please reload the page.")
                return
            with _tenant_context(getattr(view, "_tenant", None)):
                if explicit:
                    try:
                        await self.authorize_explicit_turn(view)
                    except Exception:  # noqa: BLE001 — no auth provider values
                        if self.view_instance is view:
                            await self.deny_explicit_turn()
                        return
                try:
                    await self._dispatch_url_change_inner(data)
                finally:
                    if explicit:
                        view.__dict__.pop("_djust_event_request", None)

    async def _dispatch_url_change_inner(self, data: Dict[str, Any]) -> None:
        """URL-change body (see :meth:`dispatch_url_change` for the tenant wrapper)."""
        if not self.view_instance:
            await self.transport.send_error("View not mounted")
            return

        params = data.get("params", {})
        uri = data.get("uri", "")

        # #3125: a view whose object the route selects (ADR-035) never runs
        # handle_params for a URL naming another record; it remounts there, so
        # the object is resolved and authorized for the URL the user sees.
        route_changed = getattr(self.view_instance, "_djust_route_changed", None)
        if callable(route_changed) and isinstance(uri, str) and uri and route_changed(uri):
            from .mixins.navigation import same_origin_target

            self.view_instance.live_redirect(same_origin_target(uri), replace=True)
            await self._flush_navigation()
            return

        try:
            await run_handle_params(self.view_instance, params, uri)

            # Object-permission re-check (ADR-017) after the client-supplied URL
            # params may have changed the access-determining state. Without this,
            # url_change navigates to a denied object and re-renders it
            # (finding #10). No-op for views that don't override get_object.
            from django.core.exceptions import PermissionDenied

            from .auth.core import enforce_object_permission

            # An explicit turn re-checks against its freshly authorized request,
            # never the mount-time one.
            permission_request = getattr(
                self.view_instance,
                "_djust_event_request",
                getattr(self.view_instance, "request", None),
            )
            try:
                await sync_to_async(enforce_object_permission)(
                    self.view_instance, permission_request
                )
            except PermissionDenied:
                await self.transport.send_error(
                    "Access denied for this object.", code="permission_denied"
                )
                return

            # Declared server state changed by handle_params is saved before
            # the render frame; a failed save withholds it (ADR-038 E3).
            if not await self.commit_explicit_turn(self.view_instance, source="url_change"):
                return

            if hasattr(self.view_instance, "_sync_state_to_rust"):
                await sync_to_async(self.view_instance._sync_state_to_rust)()

            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

            # Allow views to force full HTML by setting _force_full_html = True
            if getattr(self.view_instance, "_force_full_html", False):
                self.view_instance._force_full_html = False
                patches = None

            # Stamp the transport's client-checked wire version (#1858, the #1788
            # parallel-path twin). On WS this is the consumer-owned monotonic counter
            # + recovery arming (so the url_change frame stays in sequence with the
            # mount baseline and a later request_html serves the matching version);
            # on SSE this returns the Rust ``version`` unchanged. ``html`` is the RAW
            # pre-strip render — pass it BEFORE strip/extract so WS arms recovery with
            # the full pre-strip HTML (see LiveViewConsumer._next_version_armed).
            wire_version = self.transport.next_client_version(html, version)

            if patches is not None:
                if isinstance(patches, str):
                    patches = fast_json_loads(patches)
                msg: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patches,
                    "version": wire_version,
                    "event_name": "url_change",
                }
                await self._send_render_frame(msg)
            else:
                if hasattr(self.view_instance, "_strip_comments_and_whitespace"):
                    html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(
                        html
                    )
                if hasattr(self.view_instance, "_extract_liveview_content"):
                    html = await sync_to_async(self.view_instance._extract_liveview_content)(html)
                msg = {
                    "type": "html_update",
                    "html": html,
                    "version": wire_version,
                    "event_name": "url_change",
                }
                await self._send_render_frame(msg)

            # Full flush-queue parity with WS (#1885 / #1646): the url_change
            # path is the runtime's one production user, so flash / page_metadata
            # / layout / a11y queued during handle_params() were being
            # silently dropped before this. Drain every queue in canonical order.
            await self._flush_all_pending()
            # start_async()/assign_async() work handle_params() queued runs
            # after the frame, as it does on the mount and event paths;
            # without this a Back/Forward read never happened.
            self._dispatch_async_work(None)
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="event",
                event_name="url_change",
                logger=logger,
                log_message="Error in handle_params()",
            )
            await self.transport.send(response)

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def _instantiate_view(self, view_path: str) -> Optional[Any]:
        """Import + instantiate the LiveView class. On failure, STASHES the error
        frame on ``self._instantiate_error_frame`` (sent by ``dispatch_mount``) and
        returns ``None``. Override-friendly for tests.

        #1919 (THE MOUNT FLIP): this previously fire-and-forgot the error send via
        ``asyncio.ensure_future`` (it is a sync method and cannot await). Under the
        ``mount_batch`` collector (``_mount_one`` swaps ``send_json`` per view), a
        deferred send from a FAILED view leaked into the NEXT view's collector — a
        bad view's error frame surfaced in a survivor's ``captured[]`` and flipped
        the survivor to ``failed[]``. Stashing the frame and letting the async
        ``dispatch_mount`` AWAIT it (mirroring the bespoke ``handle_mount``, which
        ``await self.send_json(...)``-ed the error inline) keeps the send inside the
        correct ``_mount_one`` window — no cross-entry contamination.
        """
        self._instantiate_error_frame = None

        # Security (F22 — unsafe reflection / arbitrary module import): resolve
        # through the single shared resolver (shape-check → allowlist-before-
        # import → import_module + vars() PEP 562-safe lookup → LiveView subclass
        # check). Fail-closed; defense in depth even if a caller forgot to gate.
        # Shared with the WebSocket/SSE paths (#1646). See djust.security.mount.
        from .security.mount import resolve_view_class

        resolution = resolve_view_class(view_path)
        if not resolution:
            logger.error(
                "Failed to load view %s: %s", sanitize_for_log(view_path), resolution.detail
            )
            self._instantiate_error_frame = {
                "type": "error",
                "error": _safe_error(resolution.detail, resolution.generic),
            }
            return None
        view_class = resolution.view_class
        if view_class is None:  # pragma: no cover — truthy resolution => view_class set
            # ViewResolution.__bool__ is `view_class is not None`, so a truthy
            # `resolution` (checked above) guarantees this; the guard narrows
            # Optional[type] for the type checker and is behavior-equivalent.
            return None

        try:
            return view_class()
        except Exception as exc:
            # No instance exists yet: the class owns the policy (ADR-038 D-a).
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Failed to instantiate {view_path}",
                expose_details=_diagnostics_policy_allows(view_class),
            )
            self._instantiate_error_frame = response
            return None

    async def _build_request(self, *, page_url: str, params: Dict[str, Any]) -> Any:
        """Build a Django request representing the mount target page.

        If the transport carries a REAL request (SSE — the HTTP request that
        established the stream, with its authenticated ``request.user`` /
        session / cookies), use it directly: auth + object-perm read off it, so
        synthesizing a userless ``RequestFactory`` request would deny every
        authenticated SSE view (#1887, ADR-022 Iter 1). Otherwise (WS) we
        synthesize one via RequestFactory, propagating the authenticated user +
        session + validated host from ``self.scope``.
        """
        # getattr-guarded so duck-typed test transport fakes that predate this
        # Protocol method fall through to the synthesized request (the WS shape).
        build_request = getattr(self.transport, "build_request", None)
        transport_request = build_request() if build_request is not None else None
        from .security.csrf import abind_csrf_cookie  # noqa: PLC0415

        if transport_request is not None:
            # #2998: normally a no-op — CsrfViewMiddleware already bound the real
            # SSE stream request. Binds it when the middleware did not run.
            await abind_csrf_cookie(transport_request)
            return transport_request

        from django.test import RequestFactory
        from urllib.parse import urlencode

        from .security.mount import validate_mount_url

        # F23 (#1819 / #1646): validate defensively here too, so the request is
        # never built from a traversed URL even if a future caller reaches
        # _build_request without going through dispatch_mount's validation.
        page_url = validate_mount_url(page_url)
        factory = RequestFactory()
        query_string = urlencode(params, doseq=True) if params else ""
        path_with_query = f"{page_url}?{query_string}" if query_string else page_url

        # Finding #26: propagate the validated client Host (and TLS scheme) into
        # the reconstructed request so host/subdomain TenantResolvers resolve the
        # SAME tenant the WS mount and the HTTP (SSR) path resolve. Without
        # HTTP_HOST the request defaults to RequestFactory's "testserver",
        # misresolving the tenant to None on runtime-rebuilt requests (url_change
        # etc.). Sourced from ``self.scope`` (set for WS-backed runtimes), routed
        # through the SAME shared helper the WS handle_mount path uses
        # (``websocket.validated_host_from_scope``) so the two reconstructed-
        # request paths cannot drift (#1646). SSE-backed runtimes have
        # ``scope=None`` and use the real HTTP request, so they are unaffected.
        from .websocket import validated_host_from_scope

        host, is_secure = validated_host_from_scope(self.scope)
        request_extra: Dict[str, Any] = {}
        if host:
            request_extra["HTTP_HOST"] = host
        if is_secure:
            request_extra["secure"] = True
            request_extra["HTTP_X_FORWARDED_PROTO"] = "https"
        request = factory.get(path_with_query, **request_extra)

        # Wire session if available from WS scope
        try:
            from .utils import build_session_for_request, presented_session_key  # noqa: PLC0415

            # The key the browser presented, even when the connect-time read of
            # a vanished session cleared the scope's copy (#4102).
            session_key = presented_session_key(self.scope)
            # Honours settings.SESSION_ENGINE (#2210). Hardcoding the DB store
            # here handed a cache-backed project an empty session — or, if it
            # never ran the sessions migration, an OperationalError on first
            # read.
            session = build_session_for_request(session_key)
            if session is not None:
                request.session = session
        except Exception:
            # Session backend not configured — leave request.session unset.
            pass

        if self.scope and "user" in self.scope:
            request.user = self.scope["user"]

        await sync_to_async(self._attach_socket_tenant)(request)
        # #2998: bind the browser's CSRF cookie so {% csrf_token %} rendered
        # over the socket matches it. After the session, for CSRF_USE_SESSIONS.
        await abind_csrf_cookie(request, self.scope)
        return request

    def _attach_socket_tenant(self, request: Any) -> None:  # noqa: dead-method-allowed (passed to sync_to_async)
        """Resolve the tenant for a synthesized socket request, as HTTP does.

        ``TenantMiddleware`` sets ``request.tenant`` on every HTTP request when
        tenancy is configured; the request synthesized for a WebSocket mount
        never had one. Explicit request binding refuses a configured tenancy
        that silently disappears, so explicit WebSocket mounts failed in every
        tenant-configured project. The resolver sees the handshake's headers
        on a probe copy, so header-based resolution works while the request's
        own ``META`` stays exactly as before. A resolver failure leaves
        ``tenant`` unset, which explicit binding refuses (fail closed).
        """
        from copy import copy

        from django.conf import settings

        config = getattr(settings, "DJUST_CONFIG", None) or {}
        if "TENANT_RESOLVER" not in config and not getattr(settings, "DJUST_TENANTS", None):
            return
        probe = copy(request)
        probe.META = dict(request.META)
        for raw_name, raw_value in (self.scope or {}).get("headers", ()) or ():
            name = raw_name.decode("latin-1").upper().replace("-", "_")
            if name in {"COOKIE", "HOST", "AUTHORIZATION"}:
                continue
            probe.META.setdefault("HTTP_" + name, raw_value.decode("latin-1"))
        try:
            from .tenants.resolvers import get_tenant_resolver

            resolved = get_tenant_resolver().resolve(probe)
        except Exception:  # noqa: BLE001 — unresolved tenancy stays visibly unresolved
            logger.warning("Socket tenant resolution failed")
            return
        tenants = getattr(settings, "DJUST_TENANTS", None) or {}
        if resolved is None and (
            tenants.get("REQUIRED", False) or config.get("TENANT_REQUIRED", False)
        ):
            # Required tenancy that resolves to nothing stays unset, so explicit
            # binding refuses it instead of reading it as "no tenant".
            return
        request.tenant = resolved

    async def _refused_view(self, reason: str, to: Optional[str] = None) -> bool:
        """A view beside the page view is refused: tell its container, not the page (#3252).

        Returns True when the view is one of those and the ``view_refused``
        frame went out; the caller then skips the page-level refusal (an error
        frame, or a ``navigate`` that would send the whole page to the login
        page). False for the page view.
        """
        send = getattr(self.transport, "send_view_refusal", None)
        if send is None:
            return False
        return bool(await send(reason, to))

    async def _check_auth(self, request: Any) -> Optional[bool]:
        """Run the shared pre-mount security sequence. Returns:
        - ``None`` if mount may proceed.
        - Truthy value if blocked (and a navigate/error/mount-error frame was sent).

        Routes through ``djust.auth.core.run_pre_mount_auth`` so the auth call,
        the ``_ensure_tenant`` resolve, the tenant ContextVar bind, and the
        "skip tenant on auth denial" rule are single-sourced with the WS + SSE
        mount paths (#1646 / #1853) — a future edit cannot reorder the steps or
        drop one on this path. The runtime-specific verdict→frame mapping stays
        here:

        * auth ``PermissionDenied`` (raised inside the helper) →
          ``{"type": "error", ...}`` (then abort) — matches the legacy auth
          inner-try.
        * auth redirect URL (returned by the helper) → ``{"type": "navigate", ...}``
          (then abort) — matches the legacy redirect branch.
        * any OTHER exception (a tenant-resolution failure such as ``Http404``
          from ``_ensure_tenant``, or a buggy custom ``check_permissions`` hook
          raising a non-``PermissionDenied``) → the ``handle_exception``
          mount-error envelope (then abort, fail-closed). This consolidates the
          legacy split — the dedicated tenant try/except that used to live in
          ``dispatch_mount`` AND the auth ``except Exception`` — into one
          fail-closed envelope, matching the WebSocket mount path which already
          aborts on any non-auth-verdict exception during this sequence.
        """
        from .auth import arun_pre_mount_auth, run_pre_mount_auth
        from .auth.core import has_async_check_permissions
        from django.core.exceptions import PermissionDenied

        auth_view = self.view_instance
        legacy_diagnostics = _diagnostics_policy_allows(auth_view)
        try:
            if has_async_check_permissions(auth_view):
                # ``async def check_permissions``: awaited on the event loop, in
                # this turn's context, never on the shared sync thread.
                redirect_url = await arun_pre_mount_auth(auth_view, request)
            else:
                redirect_url = await sync_to_async(run_pre_mount_auth)(auth_view, request)
        except PermissionDenied:
            if not await self._refused_view("permission_denied"):
                await self.transport.send(
                    {"type": "error", "error": "Permission denied", "code": "permission_denied"}
                )
            # ADR-022 Iter 3 Phase 3.3a (#1917, Finding E): the runtime already
            # sent the verdict frame; finalize_mount_auth adds ONLY the
            # transport-level close (WS close(4403) — unconditional for a
            # permission denial, matching websocket.py:2345; SSE no-op). It does
            # NOT re-send the frame. getattr-guarded for duck-typed fakes.
            await self._finalize_mount_auth("permission_denied")
            return True
        except Exception as exc:  # noqa: BLE001 — fail-closed (tenant / auth-hook error)
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=self.view_instance.__class__.__name__,
                logger=logger,
                log_message="Error in pre-mount security sequence for %s"
                % sanitize_for_log(self.view_instance.__class__.__name__),
                expose_details=legacy_diagnostics and _diagnostics_policy_allows(auth_view),
            )
            await self.transport.send(response)
            # No close: the WS bespoke path lets a non-auth-verdict exception
            # (tenant Http404 / buggy custom check_permissions) flow to the outer
            # mount-error envelope WITHOUT a socket close (websocket.py:2305-2313).
            # finalize_mount_auth is NOT called here — parity with WS.
            return True

        if redirect_url:
            if not await self._refused_view("login_required", redirect_url):
                await self.transport.send({"type": "navigate", "to": redirect_url})
            # Finding E: the redirect verdict gates the WS close on
            # ``not _mounting_in_batch`` (#291 / #1780) — a batched login-required
            # view reports as ``navigate[]`` and closing would kill sibling mounts.
            # finalize_mount_auth applies that gated close (SSE no-op).
            await self._finalize_mount_auth("redirect")
            return True

        return None

    async def _fail_mount(self, view_instance: Any, exc: BaseException, response: Any) -> None:
        """End a mount that raised after the view was admitted: nothing stays mounted.

        ``mount()`` is where an application checks what the user may see, so
        an exception there (``PermissionDenied``, ``Http404``, any other), in
        the state restore, ``handle_params()``, ``connected()`` or the first
        render refuses the view. ``PermissionDenied`` is answered as the
        pre-mount auth check answers it: a ``permission_denied`` error (a
        ``view_refused`` frame for a view beside the page) and the transport's
        refusal close (WebSocket 4403, not inside a ``mount_batch``). Any other
        exception keeps its error frame. Either way the transport undoes what
        the mount set up (``on_mount_failed``: tick task, channel groups,
        presence), the view is released and ``view_instance`` is cleared, so a
        later frame finds no mounted view.
        """
        from django.core.exceptions import PermissionDenied

        denied = isinstance(exc, PermissionDenied)
        if denied:
            if not await self._refused_view("permission_denied"):
                await self.transport.send(
                    {"type": "error", "error": "Permission denied", "code": "permission_denied"}
                )
        else:
            await self.transport.send(response)
        await self._on_mount_failed(view_instance)
        if denied:
            await self._finalize_mount_auth("permission_denied")
        if self.view_instance is view_instance:
            self.view_instance = None
        await _release_failed_view(view_instance)

    async def _on_mount_failed(self, view_instance: Any) -> None:
        """Tell the transport that a mount it already set up has failed (#3027).

        ``on_view_mounted`` runs before ``mount()``: on WS it starts the view's
        tick task. When ``mount()``, ``handle_params()`` or the
        initial render then raises, the error frame goes out but the
        half-mounted view stays on the runtime (later frames on the socket
        still see it, unchanged here), and the tick kept calling
        ``handle_tick`` on it every beat. ``on_mount_failed`` stops that work.
        getattr-guarded like the other mount hooks, so a transport without it
        (SSE has no tick) and duck-typed test fakes are unaffected. Never
        raises: a cleanup failure must not replace the error frame already
        sent.
        """
        hook = getattr(self.transport, "on_mount_failed", None)
        if hook is None:
            return
        try:
            result = hook(view_instance)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 — cleanup after an error already reported
            from ._exposure_diagnostics import log_failure_for

            log_failure_for(
                logger, (view_instance,), exc, "on_mount_failed hook raised", traceback=True
            )

    async def _finalize_mount_auth(self, verdict: str) -> None:
        """Apply the transport-level finalization of a blocking mount-auth verdict.

        ADR-022 Iter 3 Phase 3.3a (#1917, Finding E). The runtime's auth-block
        paths (``_check_auth`` / the ``run_on_mount_hooks`` redirect in
        ``dispatch_mount``) ALREADY send the verdict frame + clear
        ``view_instance``; this adds ONLY the transport-level close
        (``WSConsumerTransport.finalize_mount_auth`` → ``close(4403)``, gated on
        ``not mounting_in_batch`` for the redirect verdicts). SSE is a no-op.
        getattr-guarded so duck-typed test transport fakes (and the
        default-bearing Protocol) keep working.
        """
        finalize = getattr(self.transport, "finalize_mount_auth", None)
        if finalize is not None:
            await finalize(self.view_instance, verdict)

    def _resolve_url_kwargs(self, page_url: str) -> Dict[str, Any]:
        """Resolve URL-pattern kwargs (e.g. ``pk``, ``slug``) from
        ``page_url``. Returns ``{}`` for unresolvable paths so callers can
        unconditionally ``mount_kwargs.update(...)``.

        ``unquote`` first, and that is load-bearing. ``page_url`` is the
        browser's ``location.pathname``, which is percent-encoded, but
        ``django.urls.resolve`` expects a path that has ALREADY been decoded —
        Django decodes ``request.path`` before it ever reaches the resolver, so
        the two are not interchangeable. Resolving the raw form hands a view the
        encoded value: a category named "Core UI" arrives as ``"Core%20UI"``,
        misses its own lookup, and the page renders fine over HTTP while its
        WebSocket mount dies with ``Http404`` — so the page is visible and
        inert. Any kwarg with a space, a slash or a non-ASCII character is
        affected; ``unquote`` is idempotent for the ones that are not.
        """
        try:
            from urllib.parse import unquote

            from django.urls import resolve

            match = resolve(unquote(page_url))
            return dict(match.kwargs) if match.kwargs else {}
        except Exception:
            return {}

    def _own_route_kwargs(self, view_instance: Any, page_url: str) -> Optional[Dict[str, Any]]:
        """``page_url``'s resolved kwargs if that route serves this view class.

        ``None`` when the URL does not resolve, or resolves to another view: the
        client names both the view and the URL, so a URL routed elsewhere must
        not select this view's object.
        """
        from .mixins.navigation import own_route_kwargs

        return own_route_kwargs(view_instance, page_url)

    def _extract_cache_config(self, view_instance: Any) -> Optional[Dict[str, Any]]:
        """Extract @cache decorator metadata from the view's handlers.

        Sole live copy after the mount flip (#1919/#1920): the bespoke
        ``LiveViewConsumer`` twin was removed as dead code in #1921.
        """
        try:
            cache_config: Dict[str, Any] = {}
            for attr_name, decorators in _public_decorator_metadata(view_instance):
                cache_info = decorators.get("cache")
                if cache_info:
                    cache_config[attr_name] = cache_info
            return cache_config or None
        except Exception:
            return None

    #: Decorator keys the client rate-limit gate consumes (#2656). Kept
    #: narrow on purpose — the mount frame ships only what a client module
    #: actually reads, so a new metadata-only decorator cannot silently
    #: start costing every mount bytes it has no consumer for.
    _CLIENT_RATE_LIMIT_KEYS = ("debounce", "throttle")

    def _extract_handler_config(self, view_instance: Any) -> Optional[Dict[str, Any]]:
        """Extract @debounce / @throttle metadata from the view's handlers.

        Mirrors :meth:`_extract_cache_config`; ships on the mount frame as
        ``handler_config`` and is consumed by ``setHandlerConfig`` in
        ``static/djust/src/05-handler-rate-limit.js`` (#2656).
        """
        try:
            handler_config: Dict[str, Any] = {}
            for attr_name, decorators in _public_decorator_metadata(view_instance):
                entry = {
                    key: decorators[key]
                    for key in self._CLIENT_RATE_LIMIT_KEYS
                    if decorators.get(key)
                }
                if entry:
                    handler_config[attr_name] = entry
            return handler_config or None
        except Exception:
            return None

    def _extract_optimistic_rules(self, view_instance: Any) -> Dict[str, Any]:
        """Extract optimistic UI rules from descriptor components (DEP-002).

        Ported to the runtime for ADR-022 Iter 3 Phase 3.0 so the converged
        mount frame carries the optimistic-UI rules. Sole live copy after the
        mount flip (#1919/#1920): the bespoke ``LiveViewConsumer`` twin was
        removed as dead code in #1921. Only components with
        ``Meta.tier == "optimistic"`` contribute a rule; the first event wins
        (the ``event not in rules`` guard).
        """
        if not view_instance:
            return {}

        descriptors = getattr(type(view_instance), "_component_descriptors", None)
        if not descriptors:
            return {}

        rules: Dict[str, Any] = {}
        for _name, descriptor in descriptors.items():
            meta = getattr(type(descriptor), "Meta", None)
            if meta is None:
                continue
            tier = getattr(meta, "tier", "server")
            if tier != "optimistic":
                continue
            event = getattr(meta, "event", None)
            rule = getattr(meta, "optimistic_rule", None)
            if event and rule and event not in rules:
                rules[event] = rule
        return rules

    async def _explicit_event_snapshot(self, view: Any) -> Dict[str, Any]:
        """Refresh only declared client persistence after an authorized turn.

        Null explicitly invalidates a previously cached token. Omission is for
        legacy callers, not a fallback to the last successfully captured state.
        Called for the root view only, after a committed foreground event or a
        committed server-originated turn (background result, tick, push,
        NOTIFY), while the turn's authorized request is still attached. Never
        for child-view rendering: the client stores tokens for the primary view.
        """
        from django.conf import settings

        from ._exposure import ExposureError, uses_legacy_exposure
        from ._exposure_sessions import request_binding
        from ._exposure_snapshots import snapshot_codec

        if uses_legacy_exposure(view) or getattr(view, "_djust_slot_target", None):
            return {}  # a view beside the page view mints none (#3252)
        fields: Dict[str, Any] = {
            "view": view._djust_mount_view_path,
            "state_snapshot_signed": None,
        }
        # Read once: only the commit that ran in THIS turn may vouch for it.
        verified, self._explicit_binding_verified = self._explicit_binding_verified, None
        request = getattr(view, "_djust_event_request", None)
        if not _per_turn.on("snapshot_single_hop"):
            try:
                if request is None or (
                    await sync_to_async(request_binding)(request) != self._explicit_mount_binding
                ):
                    raise ExposureError("Explicit snapshot requires unchanged authorized identity")
                if getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True):
                    codec = await sync_to_async(snapshot_codec)(view, request)
                    if codec is not None:
                        fields["state_snapshot_signed"] = await sync_to_async(codec.capture)(view)
            except Exception:
                # Factories and codec errors may contain secrets. Never stringify
                # them or fall back to reflective legacy state.
                logger.warning("Explicit event snapshot unavailable; cached snapshot invalidated")
            return fields

        # No client-persisted field means no token can be minted: the answer is
        # the invalidation above whatever the binding says, so the only thing
        # the binding check adds is its warning. The root save of this turn
        # checked the same request object a moment ago (``_save_explicit_root``);
        # when it did, that warning cannot fire and the thread hops are skipped
        # (#3997). Anything else takes the checked path below.
        if request is not None and verified is request and _mints_no_client_snapshot(view):
            return fields

        def capture() -> Optional[str]:
            # The binding check, the codec and the signature, in the order and
            # on the thread they always ran, in ONE hop instead of three.
            if request_binding(request) != self._explicit_mount_binding:
                raise ExposureError("Explicit snapshot requires unchanged authorized identity")
            if getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True):
                codec = snapshot_codec(view, request)
                if codec is not None:
                    token: str = codec.capture(view)
                    return token
            return None

        try:
            if request is None:
                raise ExposureError("Explicit snapshot requires unchanged authorized identity")
            fields["state_snapshot_signed"] = await sync_to_async(capture)()
        except Exception:
            # Factories and codec errors may contain secrets. Never stringify
            # them or fall back to reflective legacy state.
            logger.warning("Explicit event snapshot unavailable; cached snapshot invalidated")
        return fields

    async def authorize_explicit_turn(self, view: Any) -> Any:
        """Fresh authority for an explicit turn with no inbound event request.

        Background completion, ``url_change`` and server-originated consumer
        turns carry no current POST. Like the child background path, reload
        the supported server session and Django auth instead of trusting the
        mount's principal (ADR-038 D-k/D-l), then rerun object permission. The
        authorized request is stashed where :meth:`commit_explicit_turn` reads
        it; callers pop it when the turn ends. Raises on any failure — callers
        deny with :meth:`deny_explicit_turn` and never stringify the exception.
        """
        from ._exposure import ExposureError
        from ._exposure_auth import aauthorize_event, authorize_event, fresh_socket_request
        from .auth.core import enforce_object_permission, has_async_check_permissions

        binding = self._explicit_mount_binding
        if view is None or view is not self.view_instance or binding is None:
            raise ExposureError("Explicit turn has no mounted owner")

        def _authorize() -> Any:
            request = authorize_event(view, fresh_socket_request(view), binding)
            enforce_object_permission(view, request)
            return request

        if has_async_check_permissions(view):
            # ``async def check_permissions``: awaited on the loop, not the sync thread.
            request = await aauthorize_event(
                view, await sync_to_async(fresh_socket_request)(view), binding
            )
            await sync_to_async(enforce_object_permission)(view, request)
        else:
            request = await sync_to_async(_authorize)()
        # Authorization hooks are application code; their success does not
        # prove the owner was left in place.
        if view is not self.view_instance:
            raise ExposureError("Explicit turn owner replaced during authorization")
        view._djust_event_request = request
        return request

    async def deny_explicit_turn(self) -> None:
        """The foreground event denial, for a turn whose authority was revoked.

        The view is torn down with the shared teardown once the socket is
        closed (#3250 review L1): nulling it alone left its background work,
        waiters and live handles to a disconnect that no longer saw it.
        """
        from ._child_lifecycle import arelease_root_view, fire_view_disconnected

        view = self.view_instance
        self.view_instance = None
        await self.transport.send_error(
            "Event authorization failed. Please reload the page.",
            code="permission_denied",
        )
        await self.transport.close(code=4403)
        if view is not None:
            try:
                await fire_view_disconnected(view)
            finally:
                await arelease_root_view(view, navigation=False, reason="view_disconnect")

    def _save_explicit_root(self, view: Any, request: Any) -> None:
        """Sync body of the root save: the binding check, projection and write.

        One Django-thread hop (see :func:`_run_explicit_save`). A binding that
        no longer matches the mount is a refusal, raised before any write.
        """
        from ._exposure import ExposureError
        from ._exposure_sessions import request_binding, save_server_state

        if request_binding(request) != self._explicit_mount_binding:
            raise ExposureError("Explicit persistence requires current authorization")
        save_server_state(view, request)
        # Set only after the check passed and the save returned (#3997).
        self._explicit_binding_verified = request

    def _save_explicit_children(self, view: Any, request: Any) -> None:
        """Sync body of the child-tree save; same shape as the root's."""
        from ._exposure import ExposureError
        from ._exposure_child_persistence import save_child_states
        from ._exposure_sessions import request_binding

        if request_binding(request) != self._explicit_mount_binding:
            raise ExposureError("Child persistence requires current authorization")
        save_child_states(view, request)

    async def commit_explicit_turn(
        self,
        view: Any,
        *,
        source: str,
        children: bool = True,
        async_batch: Optional[str] = None,
    ) -> bool:
        """Persist an authorized explicit turn before any success frame.

        Saves the root's declared server fields (and, unless the caller saves
        them itself, the child tree) under the turn's authorized request. A
        failed or timed-out save withholds the success frame: the client gets a
        static ``state_error`` and the next update is full HTML, the child
        path's contract (ADR-038 E3, "failed storage without stale delivery").
        Legacy views return True untouched; their saves stay best-effort.

        What withholding guarantees: the browser never shows, and never holds a
        signed snapshot token for, a state that storage does not have. So a
        reconnect (another process, a restart) can only restore what the user
        last saw or something newer, never something older.

        The turn waits for its save while holding the runtime's event lock,
        so the page's next event starts only once this one is stored (#4098):
        a burst of events is applied, saved and acknowledged one by one, in
        order. The wait is ``DJUST_EXPLICIT_STATE_SAVE_TIMEOUT`` (2 s by
        default).

        A save that outruns that deadline (#3200) keeps the guarantee but is
        not a failure: the values stay on the view, the error is ``transient`` and
        does not tell the user to reload, and one catch-up turn
        (:meth:`_schedule_explicit_catch_up`) later commits them and sends
        full HTML. Storage that keeps missing the deadline, or a catch-up that
        cannot finish, ends in the terminal reload error. A refused save
        (identity changed) or a storage exception is terminal too.
        """
        from ._exposure import ExposureError, uses_legacy_exposure
        from ._exposure_sessions import StateLimitExceeded

        if uses_legacy_exposure(view):
            return True
        self._explicit_binding_verified = None
        extra = self._explicit_error_fields(view, source=source, async_batch=async_batch)
        try:
            request = getattr(view, "_djust_event_request", None)
            if request is None:
                raise ExposureError("Explicit persistence requires current authorization")
            await _run_explicit_save(
                self,
                lambda: self._save_explicit_root(view, request),
                deadline=self._explicit_save_deadline(),
            )
        except ExplicitSaveDeferred as deferred:
            # The turn's values are on the view; the store has them only once
            # this save lands (#4095 reads it).
            self._explicit_unlanded = (
                deferred.work if deferred.work is not None else _SAVE_NEVER_STARTED
            )
            await self._explicit_save_deferred(view, extra, child=False, waited=deferred.waited)
            return False
        except LateSaveDropped:
            # Another request logged the session out while this save ran: the
            # save was dropped before writing (#3247). The page's session is
            # gone, so this is the reload error, without a failure traceback.
            view._force_full_html = True
            await self.transport.send_error(
                "State unavailable. Please reload the page.", code="state_error", **extra
            )
            return False
        except StateLimitExceeded as refused:
            return await self._refuse_over_limit_turn(view, refused, extra)
        except Exception as exc:  # noqa: BLE001 — storage errors can carry server-only values
            from ._exposure_diagnostics import log_failure_for

            view._force_full_html = True
            log_failure_for(
                logger,
                (view,),
                exc,
                "Explicit state save failed; success frame withheld: %s",
                exc,
                level="warning",
                traceback=True,
            )
            await self.transport.send_error(
                "State unavailable. Please reload the page.", code="state_error", **extra
            )
            return False
        self._explicit_save_succeeded()
        if children:
            return await self._persist_explicit_children_after_event(view, async_batch=async_batch)
        return True

    async def _refuse_over_limit_turn(self, view: Any, refused: Any, extra: Dict[str, Any]) -> bool:
        """A turn whose declared server state is over its limits (#4095).

        Nothing was written: the store still holds the last state that fit.
        The view goes back to that stored state (:func:`apply_restored_state`,
        which also runs ``handle_restore``), so the view, the store and the
        page (whose success frame is withheld) agree, and the next event starts
        from what is saved. Keeping the oversized value in memory only would
        fail every later save and show the user a value a reconnect silently
        loses. What the refused handler queued for after its turn (background
        work, push events, flash, page metadata, a layout swap, deferred
        callbacks, navigation) is discarded with its state: its effects were
        refused. The user gets an error naming the field and the limit; the
        next update is full HTML. Values the handler set on fields that are
        not persisted stay, as after any turn.

        The reload error instead, when going back to the store would lose
        something: when an earlier turn's save was deferred and has not landed
        (:meth:`_explicit_store_behind`), the store is behind the view and the
        user was told that turn's change would appear; rolling back to the
        store would drop it silently. The refused turn's change cannot be told
        apart from it in memory, so the page is asked to reload, which says so,
        and the pending catch-up, which would only try to save the oversized
        value, is withdrawn. Also when there is no stored state to go back to,
        or reading or restoring it fails.
        """
        from ._exposure_sessions import load_server_state

        view._force_full_html = True
        request = getattr(view, "_djust_event_request", None)
        restored = None
        if self._explicit_store_behind():
            logger.warning(
                "Explicit state over its limits while an earlier save has not landed; "
                "asking for a reload"
            )
            self._withdraw_explicit_catch_up()
        else:
            try:
                restored = await sync_to_async(load_server_state)(view, request)
                if restored is not None:
                    _discard_turn_effects(view)
                    await apply_restored_state(view, restored, request)
            except Exception as exc:  # noqa: BLE001 — storage/hook errors can carry values
                from ._exposure_diagnostics import log_failure_for

                log_failure_for(logger, (view,), exc, "Restoring saved state failed: %s", exc)
                restored = None
            if restored is None:
                logger.warning("Explicit state over its limits and no saved state to go back to")
        if restored is None:
            await self.transport.send_error(
                "State unavailable. Please reload the page.", code="state_error", **extra
            )
            return False
        logger.warning(
            "Explicit state save refused: field %s over its %s limit; the saved state was kept",
            sanitize_for_log(refused.field),
            refused.limit,
        )
        await self.transport.send_error(refused.user_message, code="state_too_large", **extra)
        # Work ``handle_restore`` queued belongs to the restored state.
        from .mixins.async_work import has_pending_async_work

        if has_pending_async_work(view):
            self._dispatch_async_work(None)
        return False

    def _withdraw_explicit_catch_up(self) -> None:
        """Cancel a scheduled catch-up turn that has not started its own turn."""
        catch_up = self._explicit_catch_up
        if (
            catch_up is not None
            and not catch_up.done()
            and not self._in_explicit_catch_up
            and catch_up is not asyncio.current_task()
        ):
            catch_up.cancel()

    @staticmethod
    def _explicit_error_fields(
        view: Any, *, source: Optional[str], async_batch: Optional[str] = None
    ) -> Dict[str, Any]:
        """The static fields of an explicit root's ``state_error`` frame."""
        extra: Dict[str, Any] = {"source": source} if source else {}
        if async_batch:
            extra["async_batch"] = async_batch
        view_path = getattr(view, "_djust_mount_view_path", None)
        if (
            isinstance(view_path, str)
            and view_path
            and not getattr(view, "_djust_slot_target", None)
        ):
            # The withheld success frame would have refreshed or revoked the
            # client's signed snapshot; the error revokes it instead, so a
            # token captured before this turn cannot outlive the failed save.
            extra["view"] = view_path
            extra["state_snapshot_signed"] = None
        return extra

    def _explicit_save_deadline(self) -> Optional[float]:
        """The catch-up's save is not interactive: it gets the 10 s cap."""
        from ._exposure_sessions import MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S

        return MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S if self._in_explicit_catch_up else None

    def _explicit_save_succeeded(self) -> None:
        self._explicit_commits += 1
        self._explicit_deferrals = 0
        self._explicit_unlanded = None

    def _explicit_store_behind(self) -> bool:
        """Whether the store may lack values the view holds (#4095).

        True when the last deferred root save never started or did not land
        (failed, cancelled or still running) and no save has succeeded since:
        those values are in memory only, and the catch-up turn would save
        them. A deferred save that landed carried every value up to it.
        """
        unlanded = self._explicit_unlanded
        if unlanded is None:
            return False
        if unlanded is _SAVE_NEVER_STARTED or not unlanded.done() or unlanded.cancelled():
            return True
        return unlanded.exception() is not None

    async def _explicit_save_deferred(
        self, view: Any, extra: Dict[str, Any], *, child: bool, waited: Optional[float] = None
    ) -> None:
        """Answer a turn whose save missed its deadline (#3200).

        Normally a transient error plus one catch-up turn. It escalates to the
        terminal reload error when the save is the catch-up's own, after
        ``_EXPLICIT_MAX_DEFERRALS`` consecutive deferrals, when the pending
        save has been running for the 10 s cap, or when the turn itself
        ``waited`` that cap for storage (a deadline set to 10 s, #4098): storage is not
        recovering, and promising an update that will not come is worse than
        asking for a reload (review I-a).
        """
        from ._exposure_sessions import MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S

        view._force_full_html = True
        self._explicit_deferrals += 1
        pending = self._explicit_save_pending
        # Stuck counts from when the save started RUNNING: one that is only
        # queued for a thread has not used any storage time (#3229 R1).
        stuck = _save_running_for(pending) >= MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S or (
            waited is not None and waited >= MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S
        )
        if (
            self._in_explicit_catch_up
            or self._explicit_deferrals >= _EXPLICIT_MAX_DEFERRALS
            or stuck
        ):
            logger.warning("Explicit state save keeps missing its deadline; asking for a reload")
            await self.transport.send_error(
                "Child state unavailable. Please reload the page."
                if child
                else "State unavailable. Please reload the page.",
                code="state_error",
                **extra,
            )
            return
        logger.warning(
            "Explicit state save exceeded its storage deadline; success frame "
            "withheld, a catch-up turn saves again"
        )
        await self.transport.send_error(
            _EXPLICIT_SAVE_DEFERRED_MESSAGE, code="state_error", transient=True, **extra
        )
        self._schedule_explicit_catch_up(view)

    def _schedule_explicit_catch_up(self, view: Any) -> None:
        """Bring the browser up to date after a deferred explicit save (#3200).

        The deferred turn's values are on the view but the browser shows the
        previous state, and its next event would be built from that stale DOM.
        Once the pending save settles (waited for up to the 10 s cap), and
        unless a later turn has committed in the meantime, this runs ONE
        server-originated turn under the same lock and fresh authorization as a
        background result: it commits the current values with the 10 s cap as
        its deadline (the ordering makes that the newest write) and sends full
        HTML with ``source="async"``.

        It never schedules another catch-up. If storage still has not
        answered, if authorization is refused, or if its own save or render
        fails, it sends the terminal reload error instead, so the transient
        message's promise is always either kept or withdrawn.

        The deferred turn returned before dispatching any ``start_async`` work
        its handler queued; the catch-up dispatches it once it has sent its
        frame (#3945). A later turn that commits first dispatches it instead,
        since it drains the same queue.
        """
        if self._explicit_catch_up is not None and not self._explicit_catch_up.done():
            return
        commits = self._explicit_commits
        pending = self._explicit_save_pending

        async def catch_up() -> None:
            from ._exposure_sessions import MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S

            _detach_from_finished_request()
            if pending is not None and not pending.done():
                # Waits without raising: a still-running save is handled under
                # the lock below, and a failed one is logged by its callback.
                await asyncio.wait({pending}, timeout=MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S)
            if self.view_instance is not view or self._explicit_commits != commits:
                return
            async with self._explicit_event_lock, self.transport.event_context(view):

                async def complete() -> None:
                    if self.view_instance is not view or self._explicit_commits != commits:
                        return
                    self._in_explicit_catch_up = True
                    try:
                        if pending is not None and not pending.done():
                            await self._explicit_catch_up_failed(view, "storage is not answering")
                            return
                        try:
                            await self.authorize_explicit_turn(view)
                        except Exception:  # noqa: BLE001 — no auth provider values on the wire/log
                            await self._explicit_catch_up_failed(view, "not authorized")
                            return
                        if not await self.commit_explicit_turn(view, source="async"):
                            return  # it sent the terminal error (a catch-up never defers twice)
                        snapshot_fields = await self._explicit_event_snapshot(view)
                        # Background work the deferred turn queued with
                        # start_async() (#3945). That turn returned before its
                        # acknowledgement, and so before dispatching it; the
                        # work waited on the view, and the page waited for its
                        # result forever. The catch-up frame announces it
                        # (async_pending + async_batch) and it starts after the
                        # frame, as it would have after the turn's own.
                        from ._async_batch import AsyncBatch

                        batch = AsyncBatch(view)
                        await self._render_async_result(
                            None,
                            snapshot_fields={**snapshot_fields, **batch.fields()},
                            force_html=True,
                        )
                        self._dispatch_async_work(None, batch)
                    except Exception:  # noqa: BLE001 — render/provider errors may carry values
                        await self._explicit_catch_up_failed(view, "render failed")
                    finally:
                        self._in_explicit_catch_up = False
                        view.__dict__.pop("_djust_event_request", None)

                await self._run_async_result_turn(view, None, complete, source="catch_up")

        self._explicit_catch_up = asyncio.ensure_future(catch_up())

    async def _explicit_catch_up_failed(self, view: Any, reason: str) -> None:
        """Withdraw the transient promise: the catch-up could not deliver."""
        logger.warning("Explicit catch-up turn could not finish (%s); asking for a reload", reason)
        try:
            await self.transport.send_error(
                "State unavailable. Please reload the page.",
                code="state_error",
                **self._explicit_error_fields(view, source="async"),
            )
        except Exception:  # noqa: BLE001 — a closed transport has nobody to tell
            logger.debug("Explicit catch-up failure could not be delivered")

    async def _persist_explicit_children_after_event(
        self,
        view: Any,
        *,
        request: Any = None,
        async_batch: Optional[str] = None,
        mounted: bool = True,
    ) -> bool:
        """Save the authorized child tree before acknowledging a parent event.

        ``mounted=False`` is the mount path: the browser has no page yet, so a
        deferred save there cannot be caught up and stays the terminal
        reload error it was before #3200.
        """
        from ._exposure import ExposureError, uses_legacy_exposure

        if uses_legacy_exposure(view):
            return True
        if (
            request is None
            and not getattr(view, "_child_views", None)
            and not getattr(view, "_explicit_child_state_tracked", False)
        ):
            # Mount scans for an existing index under fresh authorization.
            # A view with no children/index has no child persistence work;
            # retain the independent snapshot-invalidation acknowledgement.
            return True
        child_extra: Dict[str, Any] = (
            {"source": "async", "async_batch": async_batch} if async_batch else {}
        )
        try:
            if request is None:
                request = getattr(view, "_djust_event_request", None)
            if request is None:
                raise ExposureError("Child persistence requires current authorization")
            await _run_explicit_save(
                self,
                lambda: self._save_explicit_children(view, request),
                deadline=self._explicit_save_deadline(),
            )
        except ExplicitSaveDeferred as deferred:
            if not mounted:
                # No mount frame will be sent, so there is nothing to catch up.
                logger.warning("Explicit child state save at mount exceeded its storage deadline")
                await self.transport.send_error(
                    "Child state unavailable. Please reload the page.", code="state_error"
                )
                return False
            # Same contract as the root (#3200): withheld, not failed.
            await self._explicit_save_deferred(
                view, child_extra, child=True, waited=deferred.waited
            )
            return False
        except Exception:  # noqa: BLE001 — no provider values or success frame on failure
            # Rendering may already have advanced the server VDOM. The browser
            # received no matching update, so the next success must send HTML.
            view._force_full_html = True
            await self.transport.send_error(
                "Child state unavailable. Please reload the page.",
                code="state_error",
                **child_extra,
            )
            return False
        self._explicit_save_succeeded()
        return True

    @_runtime_diagnostic_scope
    async def _render_and_send(
        self,
        *,
        event_name: str,
        cache_request_id: Optional[str] = None,
        has_async: bool = False,
        force_html: bool = False,
        event_ref: Optional[int] = None,
        scoped_component: Optional[Any] = None,
        snapshot_fields: Optional[Dict[str, Any]] = None,
        async_batch: Optional["AsyncBatch"] = None,
    ) -> None:
        """Re-render after an event handler and emit the appropriate frame.

        ``scoped_component`` (ADR-032 D5): the bound component the event
        resolved to — the ONE assign that changed, per
        :func:`_scoped_component_for`. When the remaining gates hold
        (:meth:`_render_scoped_component`) only its subtree is diffed and the
        same ``patch`` frame goes out with ``timing.scope == "component"``;
        otherwise this is today's page render, unchanged.

        Decides between ``patch`` (VDOM diff available) and ``html_update``
        (no diff or compression fallback). Mirrors the legacy
        ``_sse_handle_event`` render branch.

        ``force_html`` (the view's ``_force_full_html`` flag, read by the
        caller): when True, discard the VDOM patches and send a full
        ``html_update`` instead — mirrors WS handle_event
        (websocket.py:4039-4040). The flag is consumed (reset to False) here so
        a single ``self._force_full_html = True`` in a handler forces exactly
        one full render.

        ``event_ref`` (#560): echoed back on every emitted frame so the client
        can match the response to its pending event request.
        """
        # Bind the mounted view to a non-None local. _render_and_send is only
        # entered after a view-mounted check (and inside event_context, which holds
        # the render lock), so this guard never fires in practice — it narrows
        # Optional[Any] for the type checker and is behavior-equivalent.
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            await self.transport.send_error("View not mounted. Please reload the page.")
            return

        # Consume the explicit full-HTML request (#560/#700 sibling): reset the
        # flag so it forces exactly one render, mirroring WS
        # (websocket.py:4039-4040 / _dispatch_single_event websocket.py:1489).
        if force_html and getattr(view, "_force_full_html", False):
            view._force_full_html = False
        # Measure render duration for the DEBUG-only top-level ``timing`` field the
        # bespoke ``_send_update`` carried (#1908 item 1). Inert in production: the
        # value is stamped onto the frame as ``_timing_render_ms`` and ``on_event_frame``
        # pops it, only re-attaching a top-level ``timing`` when ``_should_expose_timing()``
        # is true. Cheap (one ``perf_counter`` pair) so it stays unconditional.
        _render_start = time.perf_counter()
        scoped: Optional[Tuple[str, str, int]] = None
        if scoped_component is not None and not force_html:
            scoped = await self._render_scoped_component(view, scoped_component)
        html: str
        patches: Optional[str]
        version: int
        if scoped is not None:
            html, patches, version = scoped
        else:
            try:
                from ._child_rendering import render_view_with_diff

                html, patches, version = await sync_to_async(render_view_with_diff)(view)
            except Exception as exc:
                from ._exposure_diagnostics import restrict_diagnostics

                restrict_diagnostics(view)
                response = handle_exception(
                    exc,
                    error_type="render",
                    view_class=view.__class__.__name__,
                    logger=logger,
                    log_message="Runtime: render error",
                )
                await self.transport.send(response)
                return
            finally:
                _drop_processor_replay(view)
        from ._exposure_diagnostics import restrict_diagnostics

        restrict_diagnostics(view)
        _render_ms = (time.perf_counter() - _render_start) * 1000

        if not await self._persist_explicit_children_after_event(view):
            return

        def _send_event_frame(frame: Dict[str, Any]) -> Dict[str, Any]:
            """Stamp the render duration + invoke the DEBUG ``on_event_frame`` fold
            (#1908) in-place, then return the frame for ``transport.send``. The
            internal ``_timing_render_ms`` marker is ALWAYS popped before the frame
            leaves this helper (the WS hook consumes it; SSE / partial test transports
            have a no-op or no hook), so it can never leak onto the wire."""
            if snapshot_fields is not None:
                frame.update(snapshot_fields)
            if async_batch is not None:
                frame.update(async_batch.fields())
            return self._stamp_event_frame(
                view,
                frame,
                event_name=event_name,
                event_ref=event_ref,
                render_ms=_render_ms,
                scope="component" if scoped is not None else None,
            )

        should_reset_form = getattr(view, "_should_reset_form", False)
        if should_reset_form:
            view._should_reset_form = False

        # Honor the explicit full-HTML request: discard the VDOM patches so the
        # render falls into the no-diff ``html_update`` branch below (mirrors WS
        # handle_event websocket.py:4039-4040, which sets ``patches = None`` when
        # ``_force_full_html`` is set). The flag itself was already consumed at
        # the top of this method.
        if force_html:
            patches = None

        # Stamp the transport's client-checked wire version (#1858, the #1788
        # parallel-path twin) from the RAW pre-strip render. WS → consumer-owned
        # counter + recovery arming; SSE → Rust ``version`` unchanged. Computed once
        # here so every render branch below (patch / compression-fallback html_update /
        # no-diff html_update) stamps the same wire version. (Reached by SSE today and
        # by any future WS migration of dispatch_event off the bespoke consumer path.)
        wire_version = self.transport.next_client_version(html, version)

        if patches is not None:
            patch_list: Optional[List] = (
                fast_json_loads(patches) if isinstance(patches, str) else patches
            )

            # Patch compression (mirror sse.py legacy). A scoped patch is
            # never compressed: its patches are a component's worth, and the
            # fallback would ship the whole page for a subtree change.
            PATCH_THRESHOLD = 100
            _compressed_patch_count: Optional[int] = None
            if scoped is None and patch_list and len(patch_list) > PATCH_THRESHOLD:
                patches_size = len(patches.encode("utf-8")) if isinstance(patches, str) else 0
                html_size = len(html.encode("utf-8")) if html else 0
                if patches_size and html_size < patches_size * 0.7:
                    if hasattr(view, "_rust_view") and view._rust_view:
                        view._rust_view.reset()
                    _compressed_patch_count = len(patch_list)
                    patch_list = None

            if patch_list is not None:
                msg: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patch_list,
                    "version": wire_version,
                    "event_name": event_name,
                    "source": "event",
                }
                if cache_request_id:
                    msg["cache_request_id"] = cache_request_id
                if should_reset_form:
                    msg["reset_form"] = True
                if has_async:
                    msg["async_pending"] = True
                if event_ref is not None:
                    msg["ref"] = event_ref
                await self._send_render_frame(_send_event_frame(msg))
            else:
                # Compression fallback — send full HTML.
                html_stripped = view._strip_comments_and_whitespace(html)
                html_content = view._extract_liveview_content(html_stripped)
                # Observability fold (#1907): the WS bespoke path emitted a
                # ``patch_compression`` ``_emit_full_html_update`` signal here
                # (websocket.py:4129). DJE-053 does NOT fire on this branch (it had
                # patches; compression chose HTML), matching the WS bespoke gate.
                # Called defensively (partial test transports are a no-op).
                _on_render = getattr(self.transport, "on_render_emitted", None)
                if _on_render is not None:
                    _on_render(
                        view,
                        reason="patch_compression",
                        version=version,
                        event_name=event_name,
                        html=html,
                        patch_count=_compressed_patch_count,
                    )
                msg = {
                    "type": "html_update",
                    "html": html_content,
                    "version": wire_version,
                    "event_name": event_name,
                    "source": "event",
                }
                if cache_request_id:
                    msg["cache_request_id"] = cache_request_id
                if should_reset_form:
                    msg["reset_form"] = True
                if has_async:
                    msg["async_pending"] = True
                if event_ref is not None:
                    msg["ref"] = event_ref
                await self._send_render_frame(_send_event_frame(msg))
        else:
            # No VDOM diff available — send HTML directly.
            if html and hasattr(view, "_strip_comments_and_whitespace"):
                html = view._strip_comments_and_whitespace(html)
            if html and hasattr(view, "_extract_liveview_content"):
                html = view._extract_liveview_content(html)
            # Observability fold (#1907): the WS bespoke path emitted the DJE-053
            # warning + an ``_emit_full_html_update`` signal on this no-patch branch
            # (websocket.py:4215-4269 / 4091 for force). ``force_full_html`` is the
            # reason when the handler explicitly forced HTML (``patches`` was nulled
            # by the ``force_html`` guard above); otherwise it is ``first_render``
            # (version <= 1, a benign first render → DEBUG log) or ``no_patches``
            # (version > 1, a real VDOM diff failure → the DJE-053 WARNING). The hook
            # keys DJE-053 on ``reason == "no_patches" and version > 1``. A
            # ``static_render`` view keeps no baseline by design (#3626): its full
            # frame is ``static_render``, not a diff failure, and warns nothing.
            from ._static_render import is_static_render

            _reason = (
                "force_full_html"
                if force_html
                else (
                    "first_render"
                    if version <= 1
                    else ("static_render" if is_static_render(view) else "no_patches")
                )
            )
            # #1908 item 2: the bespoke ``no_patches`` ``_emit_full_html_update`` carried
            # a ``context_snapshot`` built from the ``get_context_data()`` dict it had in
            # hand (it called ``get_context_data()`` once and reused the result for both
            # render + snapshot). The runtime render path computes context INSIDE
            # ``render_with_diff`` and does not surface it, so the snapshot was None.
            #
            # Re-capture it ONLY when DEBUG (the only consumer of the signal's snapshot is
            # dev tooling) AND only on the ``no_patches`` reason (the one the bespoke path
            # snapshotted). The DEBUG gate is load-bearing for the PRODUCTION
            # byte-identical guarantee: it confines the extra ``get_context_data()`` call
            # to dev, so production never double-calls it (no behavior change, no cost).
            # Thread it to ``on_render_emitted`` via ``context``.
            from django.conf import settings as _dj_settings

            _ctx_snapshot: Optional[Dict[str, Any]] = None
            if (
                _reason == "no_patches"
                and getattr(_dj_settings, "DEBUG", False)
                and hasattr(view, "get_context_data")
            ):
                try:
                    from .sync_turn import sync_step

                    _ctx_snapshot = await sync_to_async(
                        sync_step(view, "render", view.get_context_data)
                    )()
                except Exception:  # noqa: BLE001 — snapshot is best-effort DEBUG metadata
                    _ctx_snapshot = None
            _on_render = getattr(self.transport, "on_render_emitted", None)
            if _on_render is not None:
                _on_render(
                    view,
                    reason=_reason,
                    version=version,
                    event_name=event_name,
                    html=html,
                    context=_ctx_snapshot,
                )
            msg = {
                "type": "html_update",
                "html": html,
                "version": wire_version,
                "event_name": event_name,
                "source": "event",
            }
            if cache_request_id:
                msg["cache_request_id"] = cache_request_id
            if should_reset_form:
                msg["reset_form"] = True
            if has_async:
                msg["async_pending"] = True
            if event_ref is not None:
                msg["ref"] = event_ref
            await self._send_render_frame(_send_event_frame(msg))

        # Full flush-queue parity with WS (#1885 / #1646): drain ALL 8 queues
        # in canonical order, not just push_events/navigation/deferred.
        await self._flush_all_pending()

    async def _send_render_frame(self, frame: Dict[str, Any]) -> None:
        """Attach a fresh public owner snapshot to this render, not a side frame.

        Receivers must install it only when applying the associated DOM update.
        Mount-path identity is separate from an embedded frame's child view_id.
        No owner objects or mutable manifests are cached between renders.
        """
        from ._parameter_metadata import parameter_contract_manifest

        try:
            manifest = parameter_contract_manifest(self.view_instance)
        except Exception:  # noqa: BLE001 — never send a DOM update with invalid contracts
            logger.warning("Render parameter contracts unavailable")
            await self.transport.send_error(
                "Render parameter contracts unavailable.", code="render_error"
            )
            return
        if manifest is not None or self._parameter_contracts_active:
            frame["parameter_contracts"] = manifest
            frame["parameter_contract_view"] = self._parameter_contract_view
            # Keep emitting explicit clears until remount: omission must not
            # revive a client's pre-clear contracts during deferred delivery.
            self._parameter_contracts_active = True
        await self.transport.send(frame)

    def _stamp_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: str,
        event_ref: Optional[int],
        render_ms: float,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stamp the render duration (+ ADR-032 ``scope``) and invoke the DEBUG
        ``on_event_frame`` fold (#1908) in place, then return the frame for
        ``transport.send``. The internal markers are ALWAYS popped before the
        frame leaves (the WS hook consumes them; SSE / partial test transports
        have a no-op or no hook), so they can never leak onto the wire."""
        frame["_timing_render_ms"] = render_ms
        if scope is not None:
            frame["_timing_scope"] = scope
        _hook = getattr(self.transport, "on_event_frame", None)
        if _hook is not None:
            _hook(
                view,
                frame,
                event_name=event_name,
                event_ref=event_ref,
            )
        frame.pop("_timing_render_ms", None)
        frame.pop("_timing_scope", None)
        return frame

    async def _render_scoped_component(
        self, view: Any, component: Any
    ) -> Optional[Tuple[str, str, int]]:
        """ADR-032 D1(d)(e) + D3: render ONE bound component and splice its
        subtree into the view's VDOM.

        Returns the ``render_with_diff`` triple ``(html, patches_json,
        version)`` — ``html`` is the page as the patched tree serialises, so
        recovery is armed with current markup (D4) — or ``None`` when any
        gate fails, in which case the caller takes today's page render in the
        same event (D6: not an error, DEBUG-logged).

        Gates beyond the caller's "only this slot changed":
        * the view's template is component-opaque for it (D2, cached);
        * no pending push events, the default HTML renderer (ADR-019:
          another renderer owns its own frame shape) — the callers have
          already read ``_force_full_html``;
        * the Rust view exists and finds exactly one node for the component.
        """
        rust_view = getattr(view, "_rust_view", None)
        patch = getattr(rust_view, "patch_component_subtree", None)
        if patch is None:
            return None
        # (``_force_full_html`` is read by both callers before they get here.)
        if getattr(view, "_pending_push_events", None):
            return None
        if getattr(view, "_djust_renderer", None) is not None:
            return None
        name = component.component_id
        if not _view_is_component_opaque(view, name):
            return None
        try:
            # Verify, do not predict: the page is exact only if nothing the
            # template can read changed besides the component — including
            # values DERIVED from it elsewhere on the view.
            from .sync_turn import sync_step

            from .mixins.rust_bridge import request_scoped_data_changed

            # The producer runs inside ``sync_step`` below, with the processor check
            # (#3996), so the sync-turn wrapper scopes both (the one-producer pin).
            produce_context = view.get_context_data

            def changed_besides() -> Tuple[Dict[str, Any], Optional[str]]:
                context = produce_context()
                reason = _context_changed_besides(view, context, name)
                if reason is None:
                    # A context processor's value is not in ``context`` and is
                    # not view state, so the check above did not compare it
                    # (#3996). A changed one must render: the scoped patch
                    # would leave the node that reads it stale. Processors can
                    # query, so this runs in the same step as the context.
                    reason = request_scoped_data_changed(view, context)
                return context, reason

            context, reason = await sync_to_async(sync_step(view, "render", changed_besides))()
            if reason is not None:
                logger.debug("Scoped render of %r: %r changed too; full render", name, reason)
                return None
            html = await sync_to_async(sync_step(component, "render", component.render))()
            # Component-sized parse + diff; runs inline on the event loop like
            # the JSON decode of the patches. Route a large component through
            # ``sync_to_async`` before making it larger.
            result = patch(name, html)
        except Exception as exc:  # noqa: BLE001 — D6: fall back to the full render
            from ._exposure_diagnostics import log_failure

            # get_context_data and the component's template are application code.
            log_failure(
                logger,
                exc,
                "Scoped render of component %r failed; full render",
                name,
                level="debug",
                traceback=True,
            )
            return None
        if result is None:
            logger.debug("Scoped render of component %r not exact; full render", name)
            return None
        # The slot is now in step with the Rust state (D3 sets it), so the
        # next sync starts from the same baseline a full render leaves — the
        # component's fingerprint included, so it is not re-sent unchanged.
        view._changed_keys = None
        try:
            from .change_detection import deep_fingerprint

            fp, truncated = deep_fingerprint(context[name])
            view._prev_context_fingerprints[name] = object() if truncated else fp
        except Exception:  # noqa: BLE001 — bookkeeping; a miss only costs a re-sync
            pass
        timing = getattr(rust_view, "get_render_timing", None)
        if timing is not None:
            view._rust_render_timing = timing()
        html_out, patches_json, version = result
        # No page render follows a scoped patch: nothing will take the
        # processor outputs the check recorded, so they are dropped here.
        _drop_processor_replay(view)
        return (str(html_out), str(patches_json), int(version))

    async def _send_scoped_patch_frame(
        self,
        view: Any,
        triple: Tuple[str, str, int],
        render_ms: float,
        *,
        event_name: str,
        event_ref: Optional[int],
        async_batch: Optional["AsyncBatch"] = None,
        snapshot_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit the ``patch`` frame for a scoped render on the component
        dispatch path — the same shape ``_render_and_send`` emits (D4), with
        the wire version stamped from the page markup so recovery is current.
        ``snapshot_fields`` is an explicit view's refreshed client snapshot
        (#3231)."""
        html, patches, version = triple
        wire_version = self.transport.next_client_version(html, version)
        msg: Dict[str, Any] = {
            "type": "patch",
            "patches": fast_json_loads(patches) if isinstance(patches, str) else patches,
            "version": wire_version,
            "event_name": event_name,
            "source": "event",
        }
        if event_ref is not None:
            msg["ref"] = event_ref
        if async_batch is not None:
            msg.update(async_batch.fields())
        if snapshot_fields:
            msg.update(snapshot_fields)
        await self._send_render_frame(
            self._stamp_event_frame(
                view,
                msg,
                event_name=event_name,
                event_ref=event_ref,
                render_ms=render_ms,
                scope="component",
            )
        )

    def _flush_push_events(self, view: Optional[Any] = None) -> None:
        """Drain push_events and send via the transport (sync-safe — pushes
        get queued but the actual send is fire-and-forget to keep the
        flush points cheap)."""
        view = self.view_instance if view is None else view
        if not view or not hasattr(view, "_drain_push_events"):
            return
        for event_name, payload in view._drain_push_events():
            asyncio.ensure_future(
                self.transport.send({"type": "push_event", "event": event_name, "payload": payload})
            )

    async def _flush_navigation(self) -> None:
        """Drain navigation commands and AWAIT the send via the transport.

        Awaited (not fire-and-forget) to match the WS bespoke turn-end drain
        (websocket.py ``_flush_navigation`` is ``async`` + awaited at
        ``_flush_all_pending``). Before #1907 THE FLIP, WS events ran on the
        bespoke path which awaited this flush WITHIN the event turn, so a
        ``live_redirect()`` from a handler emitted its ``navigation`` frame before
        ``handle_event`` returned. The runtime previously fired-and-forgot the send
        (``asyncio.ensure_future``) — fine for SSE's async stream, but once WS
        events route here that dropped the navigation frame from the event turn's
        observable output (a real regression caught by
        ``test_state_changing_handler_with_live_redirect_still_navigates``).
        Awaiting restores WS parity (#1646: converge on the WS turn-end semantics)."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_navigation"):
            return
        for cmd in view._drain_navigation():
            action = cmd.get("type")
            payload = {k: v for k, v in cmd.items() if k != "type"}
            await self.transport.send({"type": "navigation", "action": action, **payload})

    async def _flush_deferred(self) -> None:
        """Run ``self.defer(...)`` callbacks. Errors are logged and
        suppressed so deferred-callback bugs cannot break the wire.
        """
        view = self.view_instance
        if not view or not hasattr(view, "_drain_deferred"):
            return
        callbacks = view._drain_deferred()
        if not isinstance(callbacks, list) or not callbacks:
            return
        for callback, args, kwargs in callbacks:
            try:
                result = callback(*args, **kwargs)
                if inspect.iscoroutine(result):
                    await result
            except Exception as exc:
                from ._exposure_diagnostics import log_failure

                log_failure(
                    logger,
                    exc,
                    "[djust runtime] Deferred callback %s on %s raised; continuing",
                    getattr(callback, "__qualname__", repr(callback)),
                    view.__class__.__name__,
                    level="warning",
                    traceback=True,
                )

    # ------------------------------------------------------------------ #
    # Iter 0 / #1885 — full flush-queue parity with WS ``_flush_all_pending``.
    #
    # Before Iter 0 the runtime drained only 3 of WS's 8 queues
    # (push_events / navigation / deferred), so the runtime's one production
    # user (``url_change`` — dj-patch / popstate) silently dropped flash
    # messages, page-metadata updates, layout swaps, accessibility
    # announcements, and i18n commands queued during ``handle_params``
    # (live #1646 instance INSIDE the convergence target). The methods
    # below + ``_flush_all_pending`` mirror WS ``_flush_all_pending`` exactly:
    # same queue set, same canonical drain order, same per-queue frame shape
    # and ``_drain_*`` method names. The i18n flush went in #3502 and the
    # accessibility flush in 2.0 (#3500): nothing defined the
    # ``_drain_i18n_commands`` / ``_drain_announcements`` / ``_drain_focus``
    # they called. ``_flush_all_pending``
    # is the single source of the turn-end drain so a future queue addition
    # can never again be wired on one path and not the other.
    # ------------------------------------------------------------------ #

    async def _flush_flash(self) -> None:
        """Drain pending flash messages and emit ``flash`` frames (WS parity)."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_flash"):
            return
        commands = view._drain_flash()
        if not isinstance(commands, list):
            return
        for cmd in commands:
            await self.transport.send({"type": "flash", **cmd})

    async def _flush_page_metadata(self) -> None:
        """Drain pending page-metadata commands and emit ``page_metadata`` frames."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_page_metadata"):
            return
        commands = view._drain_page_metadata()
        if not isinstance(commands, list):
            return
        for cmd in commands:
            await self.transport.send({"type": "page_metadata", **cmd})

    async def _flush_pending_layout(self) -> None:
        """Render + emit a pending ``set_layout`` swap (WS parity, v0.6.0).

        Mirrors ``websocket.py:_flush_pending_layout``: render the queued
        layout template with the view's current context and emit a
        ``{"type": "layout", "path": ..., "html": ...}`` frame. Layout
        template errors must never kill the live connection — ``TemplateDoesNotExist``
        warns + skips; any other error logs (and re-raises in DEBUG so
        programmer errors surface during development).
        """
        view = self.view_instance
        if not view or not hasattr(view, "_drain_pending_layout"):
            return
        layout_path = view._drain_pending_layout()
        if not layout_path:
            return
        from django.conf import settings as django_settings
        from django.template.exceptions import TemplateDoesNotExist

        from .mixins.layout import render_pending_layout

        try:
            # #3178: context + render in one hop (was: context on the loop).
            layout_html = await sync_to_async(render_pending_layout)(view, layout_path)
        except TemplateDoesNotExist:
            logger.warning(
                "set_layout(%r) — template not found; ignoring swap request", layout_path
            )
            return
        except Exception as exc:  # noqa: BLE001 — layout errors must not kill the wire
            from ._exposure_diagnostics import log_failure

            log_failure(
                logger,
                exc,
                "set_layout(%r) — template rendering raised; ignoring swap request",
                layout_path,
                traceback=True,
            )
            if getattr(django_settings, "DEBUG", False):
                raise
            return
        await self.transport.send({"type": "layout", "path": layout_path, "html": layout_html})

    async def _flush_all_pending(self) -> None:
        """Drain every queued client side-effect at the end of a runtime turn,
        in WS's canonical order. Single source of truth for the turn-end drain
        on the runtime path (mirrors ``websocket.py:_flush_all_pending``): every
        turn-end site (event render, url_change) calls THIS so no path can drop a
        queued command (#1646). Each ``_flush_*`` drains + clears its own queue,
        so calling twice in one turn is a harmless no-op.

        Order matches WS exactly: push_events → flash → page_metadata →
        pending_layout → deferred → navigation. Layout
        (replaces ``<body>``) goes after page_metadata so head mutations land
        first and survive the swap; deferred callbacks run after the visible
        side-effects so they observe post-patch state.
        """
        self._flush_push_events()
        await self._flush_flash()
        await self._flush_page_metadata()
        await self._flush_pending_layout()
        await self._flush_deferred()
        await self._flush_navigation()

    # ------------------------------------------------------------------ #
    # Background work (start_async / @background) — runtime parity (#1887).
    #
    # The legacy SSE event path dispatched ``start_async`` callbacks via
    # ``_sse_run_async_work`` after the event render/noop. The runtime's
    # ``dispatch_event`` set the ``async_pending`` wire flag but had no
    # dispatcher, so converging SSE onto it without this would silently
    # break ``start_async`` / ``@background`` for SSE consumers (a #1646
    # gap inside the convergence target). These mirror the legacy SSE
    # helpers (``sse.py:_sse_run_async_work`` / ``_sse_execute_async_task``)
    # and the WS ``_dispatch_async_work`` / ``_run_async_work`` shape, but
    # push through ``self.transport.send`` so they are wire-blind.
    #
    # Reached via ``dispatch_event`` for BOTH transports now: SSE since Iter 1,
    # and WS since #1907 THE FLIP ("event" joined ``RUNTIME_OWNED_VERBS``).
    # A WS event's ``start_async`` / ``@background`` work therefore dispatches
    # through THIS runtime helper (which pushes via ``self.transport.send`` →
    # ``consumer.send_json``, wire-blind) rather than the consumer's own
    # ``_dispatch_async_work``. The consumer helper is still reached by the
    # WS-only non-event paths (ticks / server_push / db_notify / the bespoke
    # deferred-activity re-dispatcher). The two are behaviorally equivalent for
    # the event turn (both flush start_async + @background callbacks off-thread).
    # ------------------------------------------------------------------ #

    def _dispatch_explicit_child_queues(self, event_name: Optional[str]) -> None:
        """Run explicit child work queued at mount or by a parent turn (E3-3).

        A routed child event drains its own child's queue. Work a child queued
        in ``mount()`` (during the parent's render) or that a parent handler
        queued on a child is otherwise stranded until that child's next routed
        event. Each owner gets its own batch and the owned, re-authorized
        completion path of :func:`djust._child_async.dispatch_child_work`; no
        parent acknowledgement advertises or completes it. Legacy roots and
        legacy children keep their existing behavior.
        """
        from ._exposure import uses_legacy_exposure

        root = self.view_instance
        if (
            root is None
            or uses_legacy_exposure(root)
            or self._explicit_mount_binding is None
            or getattr(root, "_djust_child_disposed", False)
        ):
            return
        # Imported past the legacy early return: this runs after every turn,
        # on the event loop (#3095).
        from ._async_batch import AsyncBatch
        from ._child_async import dispatch_child_work

        pending = [root]
        seen: set = set()
        owners = []
        while pending and len(seen) < 257:
            parent = pending.pop()
            if id(parent) in seen:
                continue
            seen.add(id(parent))
            registry = getattr(parent, "_child_views", None)
            if type(registry) is not dict:
                continue
            for slot, child in tuple(registry.items()):
                if (
                    getattr(child, "_parent_view", None) is not parent
                    or getattr(child, "_view_id", None) != slot
                    or getattr(child, "_djust_child_disposed", False)
                    or uses_legacy_exposure(child)
                ):
                    continue
                owners.append(child)
                pending.append(child)
        for child in owners:
            if not getattr(child, "_async_tasks", None):
                continue
            batch = AsyncBatch(child)
            if batch.token:
                dispatch_child_work(self, child, event_name, batch)

    def _dispatch_async_work(
        self, event_name: Optional[str], batch: Optional["AsyncBatch"] = None
    ) -> None:
        """Schedule any ``start_async`` callbacks queued during the handler.

        Drains the named-task dict (``_async_tasks``), matching
        ``LiveViewConsumer._dispatch_async_work``. Fire-and-forget: each task
        runs in its own ``ensure_future`` so the event POST returns promptly
        and results stream in via the transport when ready.

        Event callers pass the batch already advertised by their acknowledgement.
        Unmigrated lifecycle callers retain their existing queue-drain behavior.
        """
        view = self.view_instance
        if batch is not None:
            if view is not batch.owner:
                batch.discard(self.transport)
                return

            async def run_captured(name: str, callback: Any, args: Any, kwargs: Any) -> None:
                if self.view_instance is not batch.owner:
                    return
                await self._execute_async_task(name, callback, args, kwargs, event_name)

            batch.dispatch(self.transport, run_captured)
            self._dispatch_explicit_child_queues(event_name)
            return
        if not view:
            return
        self._dispatch_explicit_child_queues(event_name)

        from .mixins.async_work import track_async_task, track_running_async_task

        tasks = getattr(view, "_async_tasks", None)
        if tasks:
            for task_name, (callback, args, kwargs) in list(tasks.items()):
                future = asyncio.ensure_future(
                    self._execute_async_task(task_name, callback, args, kwargs, event_name)
                )
                track_async_task(view, future)
                track_running_async_task(view, task_name, future)
            view._async_tasks = {}

    async def _execute_async_task(
        self,
        task_name: str,
        callback: Callable[..., Any],
        args: Any,
        kwargs: Any,
        event_name: Optional[str],
    ) -> None:
        """Run one background task as an ``async``/``start`` turn (ADR-044).

        The turn chain runs before the callback starts (context a middleware
        sets reaches the callback); its result is handled in a nested
        ``async``/``result`` turn. See :meth:`_execute_async_task_body`.
        """
        # The task outlives the turn that queued it; on SSE that turn's request
        # executors die with the POST (#3097, #3200 review B-b). Before the
        # chain, so middleware runs with the task's own executors.
        _detach_from_finished_request()
        view = self.view_instance
        if not view:
            return
        await run_turn(
            Turn(
                "async",
                self.turn_transport,
                source="start",
                view=view,
                event=task_name if isinstance(task_name, str) else None,
                scope=self.scope,
                session_id=self._turn_session_id(),
                port=self,
            ),
            lambda _turn: self._execute_async_task_body(
                task_name, callback, args, kwargs, event_name
            ),
        )

    async def _run_async_start_turn(
        self, view: Any, task_name: Any, start: Callable[[], Awaitable[None]]
    ) -> None:
        """Run background work (a child's task) as an ``async``/``start`` turn."""
        await run_turn(
            Turn(
                "async",
                self.turn_transport,
                source="start",
                view=view,
                event=task_name if isinstance(task_name, str) else None,
                scope=self.scope,
                session_id=self._turn_session_id(),
                port=self,
            ),
            lambda _turn: start(),
        )

    async def _run_async_result_turn(
        self,
        view: Any,
        task_name: Any,
        complete: Callable[[], Awaitable[None]],
        source: str = "result",
    ) -> None:
        """Handle a background result as an ``async``/``result`` turn (ADR-044).

        Called with the transport's render lock held (inside ``event_context``).
        """
        turn = Turn(
            "async",
            self.turn_transport,
            source=source,
            view=view,
            event=task_name if isinstance(task_name, str) else None,
            scope=self.scope,
            session_id=self._turn_session_id(),
            port=self,
        )
        turn._locked = True
        await run_turn(turn, lambda _turn: complete())

    async def _execute_async_task_body(
        self,
        task_name: str,
        callback: Callable[..., Any],
        args: Any,
        kwargs: Any,
        event_name: Optional[str],
    ) -> None:
        """Run one background task and stream the re-rendered result.

        Mirrors ``sse.py:_sse_execute_async_task``: run the callback off-thread,
        let ``handle_async_result`` observe success/failure, re-sync + re-render,
        and emit a ``patch`` / ``html_update`` frame plus a turn-end flush. Any
        callback failure is logged and routed through ``handle_async_result`` so
        the client is never left stuck in a loading state.

        The handler + render half of each arm runs under the consumer's render
        lock, borrowed via ``transport.event_context`` (#2840/#1646: same
        serialization as the WS twin ``_run_async_work``, which holds
        ``_render_lock`` across handler + render). SSE uses its session render
        lock to prevent late results from crossing a page-replacement boundary.
        """
        # ``_detach_from_finished_request`` already ran (in the turn wrapper).
        view = self.view_instance
        if not view:
            return
        from ._exposure import uses_legacy_exposure

        legacy_diagnostics = uses_legacy_exposure(view)
        if not legacy_diagnostics:
            await self._execute_explicit_async_task(
                view, task_name, callback, args, kwargs, event_name
            )
            return

        # cancel_async() / cancel_async_all() before the task started: skip it.
        # The WS twin ``_run_async_work`` has always checked this; the runtime
        # copy, which serves WS events since the ADR-022 flip, did not (#2969).
        if _consume_async_cancel(view, task_name):
            logger.debug("Runtime: async task %s was cancelled, skipping execution", task_name)
            await self._settle_cancelled_async(view, event_name)
            return

        try:
            # Dispatch through the ONE shared helper so the sync/async handling
            # can never drift from the consumer twin (#2020, #2016 / #1646).
            # Without the coroutine check the helper encapsulates, an async
            # callback would hit ``sync_to_async`` and raise ``TypeError:
            # sync_to_async can only be applied to sync functions``, silently
            # failing every async background task on the converged WS-event path
            # (#2001, the parallel-path drift vs ``websocket.py:_run_async_work``).
            from .mixins.async_work import run_async_callback

            result = await run_async_callback(callback, args, kwargs, owner=view)

            # Teardown identity-guard (#1940 — mirror of the WS twin's
            # pre-mutation guard): the callback above is the FIRST await in
            # this detached task; during that window a disconnect (→
            # ``view_instance = None``) or a re-mount (→ a NEW view) can run.
            # Writing the stale view — handler state OR render — contaminates
            # a torn-down / replaced view.
            if self.view_instance is not view:
                if legacy_diagnostics and uses_legacy_exposure(view):
                    logger.debug(
                        "Runtime: async task %s completed after view teardown/re-mount; "
                        "dropping stale re-render",
                        task_name,
                    )
                else:
                    logger.debug("Explicit background result discarded after owner replacement")
                return

            # Cancelled while running: skip the result handler and re-render,
            # as the WS twin does (#2969).
            if _consume_async_cancel(view, task_name):
                logger.debug("Runtime: async task %s was cancelled, skipping re-render", task_name)
                await self._settle_cancelled_async(view, event_name)
                return

            # Serialise handler + render on the consumer's render lock via
            # ``transport.event_context`` (the ADR-022 mechanism): after the
            # ADR-022 flip THIS helper serves WS events' ``start_async`` work,
            # so an unlocked handler mutation / render could interleave with a
            # concurrent tick / server_push / db_notify render — the PyO3
            # VDOM baseline is not thread-safe (#2830). ``event_context`` also
            # sets ``_processing_user_event`` (ticks yield during the frame)
            # and the observability scopes — the same per-event posture a live
            # event turn gets. Ordering mirrors the WS twin: handler first,
            # THEN the in-lock identity re-check (a view torn down mid-handler
            # still gets its user callback — only the stale re-render is
            # dropped), then the render.
            async with self.transport.event_context(view):

                async def complete() -> None:
                    if hasattr(view, "handle_async_result"):
                        await call_async_result(
                            view, view.handle_async_result, task_name, result=result, error=None
                        )
                    # Identity re-check INSIDE the lock (#1940): the handler await
                    # above is an unbounded wait during which the view can be torn
                    # down or replaced. Rendering the stale view would emit a frame
                    # the connection's version/recovery state does not own.
                    if self.view_instance is not view:
                        return
                    await self._render_async_result(event_name)

                await self._run_async_result_turn(view, task_name, complete)

        except Exception as exc:
            failure = exc  # bound for the nested result turn below
            if legacy_diagnostics and uses_legacy_exposure(view):
                logger.exception(
                    "Runtime: error in start_async callback '%s' on %s",
                    task_name,
                    view.__class__.__name__ if view else "?",
                )
            else:
                logger.warning("Explicit background callback failed")
            try:
                # Same locked shape as the success arm (#2840 twin): the
                # error-state mutation + re-render must not interleave with
                # a concurrent lock-holding render. Ordering preserved:
                # handler → in-lock identity re-check → render. The result
                # frame is sent even without a ``handle_async_result``: the
                # turn announced ``async_pending`` (#2963), and this frame is
                # what ends the client's loading state.
                async with self.transport.event_context(view):

                    async def complete_error() -> None:
                        if hasattr(view, "handle_async_result"):
                            await call_async_result(
                                view,
                                view.handle_async_result,
                                task_name,
                                result=None,
                                error=failure,
                            )
                        if self.view_instance is not view:
                            return
                        await self._render_async_result(event_name)

                    await self._run_async_result_turn(view, task_name, complete_error)
            except Exception:
                if legacy_diagnostics and uses_legacy_exposure(view):
                    logger.exception(
                        "Runtime: error in handle_async_result for task '%s'", task_name
                    )
                else:
                    logger.warning("Explicit background result handling failed")

    async def _settle_cancelled_async(self, view: Any, event_name: Optional[str]) -> None:
        """End the loading state a cancelled task's event announced.

        The runtime twin of ``LiveViewConsumer._settle_cancelled_async``
        (#2963): the event's reply carried ``async_pending``, so the client
        keeps the event's loading state until a ``source="async"`` frame
        naming the event arrives. A cancelled task skips its result handler,
        so render the view's current state as that frame instead (#2969).
        Work no event owns (``event_name`` None) announced nothing.
        """
        if event_name is None:
            return
        from ._exposure import uses_legacy_exposure

        try:
            async with self.transport.event_context(view):
                if self.view_instance is not view:
                    return
                await self._render_async_result(event_name)
        except Exception:  # noqa: BLE001 — a settle frame must never raise out of a task
            if uses_legacy_exposure(view):
                logger.exception(
                    "Runtime: error settling cancelled async task for %s",
                    sanitize_for_log(event_name),
                )
            else:
                logger.warning("Explicit cancelled background settle failed")

    async def _execute_explicit_async_task(
        self,
        view: Any,
        task_name: str,
        callback: Callable[..., Any],
        args: Any,
        kwargs: Any,
        event_name: Optional[str],
    ) -> None:
        """Run a nonlegacy root's background task under fresh authority.

        The legacy runner trusts the mount-time principal and never saves. Here
        (ADR-038 E3, D-k/D-l) the callback starts only after a fresh
        authorization, the result is handled only after another one, declared
        server state is committed before the result frame, and a revoked or
        failed turn is dropped with a static error. Nothing here logs or sends
        exception values; application result handlers still get their own
        exception object.
        """
        from .mixins.async_work import run_async_callback

        # cancel_async() / cancel_async_all() before the task started (#2969).
        # No settle frame on this path: an event turn's batch ends the client's
        # loading state with its ``async_complete`` token, and unbatched
        # (server-originated) work carries ``event_name=None``, so no loading
        # state was announced.
        if _consume_async_cancel(view, task_name):
            return

        async with self._explicit_event_lock, self.transport.event_context(view):
            try:
                await self.authorize_explicit_turn(view)
            except Exception:  # noqa: BLE001 — no auth provider values on the wire/log
                if self.view_instance is view:
                    await self.deny_explicit_turn()
                return
            finally:
                view.__dict__.pop("_djust_event_request", None)

        result = error = None
        try:
            result = await run_async_callback(callback, args, kwargs, owner=view)
        except Exception as exc:  # noqa: BLE001 — only the owning application sees it
            from ._exposure_diagnostics import log_failure_for

            error = exc
            log_failure_for(
                logger,
                (view,),
                exc,
                "Runtime: error in start_async callback '%s' on %s",
                task_name,
                type(view).__name__,
                level="warning",
                traceback=True,
            )

        if self.view_instance is not view:
            logger.debug("Explicit background result discarded after owner replacement")
            return
        # Cancelled while running: skip the result handler and render (#2969).
        if _consume_async_cancel(view, task_name):
            return

        async with self._explicit_event_lock, self.transport.event_context(view):

            async def complete() -> None:
                try:
                    try:
                        await self.authorize_explicit_turn(view)
                    except Exception:  # noqa: BLE001
                        if self.view_instance is view:
                            await self.deny_explicit_turn()
                        return
                    handler = getattr(view, "handle_async_result", None)
                    if callable(handler):
                        try:
                            await call_async_result(
                                view, handler, task_name, result=result, error=error
                            )
                        except Exception as exc:  # noqa: BLE001
                            from ._exposure_diagnostics import log_failure_for

                            log_failure_for(
                                logger,
                                (view,),
                                exc,
                                "Runtime: error in handle_async_result for task '%s'",
                                task_name,
                                level="warning",
                                traceback=True,
                            )
                            return
                    elif error is not None:
                        # Legacy parity: an unhandled failure renders nothing.
                        return
                    if self.view_instance is not view:
                        return
                    if not await self.commit_explicit_turn(view, source="async"):
                        return
                    snapshot_fields = await self._explicit_event_snapshot(view)
                    await self._render_async_result(event_name, snapshot_fields=snapshot_fields)
                finally:
                    view.__dict__.pop("_djust_event_request", None)

            await self._run_async_result_turn(view, task_name, complete)

    async def _render_async_result(
        self,
        event_name: Optional[str],
        snapshot_fields: Optional[Dict[str, Any]] = None,
        *,
        force_html: bool = False,
        source: str = "async",
        broadcast: bool = False,
    ) -> None:
        """Re-sync + re-render after background work and emit the result frame.

        Shared by the success + error paths of ``_execute_async_task``. Stamps
        the transport's client-checked wire version (#1858) and drains the
        turn-end queues so push_events / deferred / etc. scheduled inside the
        background callback reach the client.

        The result frame carries ``source="async"`` (ADR-022 Iter 2 Phase 2.3a)
        so it matches the WS ``_run_async_work`` frames (websocket.py:1166/1186/
        1223/1238), which all tag ``source="async"``. Without this, the runtime's
        async-result frames were the lone untagged twin of the WS ones — a #1646
        parallel-path drift INSIDE the convergence target. The client uses
        ``source`` to distinguish an out-of-band background-completion update from
        the in-turn ``source="event"`` response, so the tag must agree across
        transports. Goes LIVE for SSE + ``url_change`` async work (both use the
        runtime async dispatcher today); WS picks it up post-flip (Phase 2.3b).
        """
        view = self.view_instance
        if not view:
            return
        if hasattr(view, "_sync_state_to_rust"):
            await sync_to_async(view._sync_state_to_rust)()
        html, patches, version = await sync_to_async(view.render_with_diff)()
        wire_version = self.transport.next_client_version(html, version)
        if force_html:
            # An explicit catch-up turn (#3200): the browser missed a withheld
            # frame, so a patch against its DOM is not safe.
            view._force_full_html = False
            patches = None

        if patches is not None:
            patch_list = fast_json_loads(patches) if isinstance(patches, str) else patches
            msg: Dict[str, Any] = {
                "type": "patch",
                "patches": patch_list,
                "version": wire_version,
                "event_name": event_name,
                "source": source,
            }
        else:
            if hasattr(view, "_strip_comments_and_whitespace"):
                html = view._strip_comments_and_whitespace(html)
            if hasattr(view, "_extract_liveview_content"):
                html = view._extract_liveview_content(html)
            msg = {
                "type": "html_update",
                "html": html,
                "version": wire_version,
                "event_name": event_name,
                "source": source,
            }
        if broadcast:
            # A server push (#3809) is tagged as the WebSocket's push turn tags
            # its frames, so the client treats both transports alike.
            msg["broadcast"] = True
        if snapshot_fields:
            # An explicit root's refreshed signed snapshot (ADR-038 E3).
            msg.update(snapshot_fields)
        await self._send_render_frame(msg)
        await self._flush_all_pending()

    # ------------------------------------------------------------------ #
    # Server-originated turns without a channel layer: SSE (#3809).
    #
    # The WebSocket consumer owns the server push, the tick and their render
    # lock (``LiveViewConsumer._server_push_to_view`` / ``_run_tick``); both are
    # wired to the channel layer and to the consumer's own lock, so the runtime
    # cannot run them for a socket. An SSE session has no channel layer: its
    # sessions live in this process, and the view's own code is the only caller.
    # These are the SSE twins of those two turns, on the session's render lock
    # borrowed through ``transport.event_context`` (the #560 version-interleave
    # guard; a runtime-local lock would not serialize against the event spine).
    # What stays WebSocket-only over SSE: ``push_to_view`` groups, push scopes
    # and ``db_notify`` (they need the channel layer), the worker-pool offload
    # of a push turn, and ``sync_push_scope_groups``.
    #
    # Back-pressure. A WebSocket tick or push awaits ``send`` under the render
    # lock, so a client that does not read slows them. An SSE frame is a
    # ``put_nowait`` on the session queue, so nothing slows a producer. While
    # the queue holds ``_MAX_QUEUED_SERVER_FRAMES`` frames or more (the client
    # is not draining), a tick is skipped and a push is deferred (queued,
    # coalescing, bounded: the newest wins), exactly as when a turn is in
    # progress. Frames answering an event are never held back.
    # ------------------------------------------------------------------ #

    async def server_push(self, view: Any, event: Dict[str, Any]) -> bool:
        """Run ``event`` (``{"handler": ..., "payload": ...}``) on ``view`` as a
        ``push``/``server_push`` turn, deferring it while the session is busy.

        The SSE twin of ``LiveViewConsumer._server_push_to_view``: a push that
        finds a turn in progress, a client that is not draining, or earlier
        pushes still queued, is queued and replayed in arrival order once the
        render lock frees; identical queued pushes coalesce and the queue is
        bounded, as on the socket (#3001). A failing handler or render is
        logged and swallowed, as on the socket. Returns whether the push was run
        or queued; ``False`` when ``view`` is not the mounted view (replaced,
        closed, or its mount frame not sent).
        """
        if view is None or self.view_instance is not view or self._mount_sent_for is not view:
            return False
        task = self._push_drain_task
        draining = task is not None and not task.done()
        # ``draining`` also covers earlier pushes still queued (the queue is only
        # non-empty while a drain runs), so a push never overtakes them.
        if draining or self._server_turn_busy(view) or self._client_backlogged():
            logger.debug("[djust] server_push on %s deferred — session busy", type(view).__name__)
            self._defer_server_push(view, event)
            return True
        await self._run_server_push(view, lambda: [event])
        return True

    def _server_turn_busy(self, view: Any) -> bool:
        """Whether a turn holds the lock(s) a server-originated turn would take."""
        from ._exposure import uses_legacy_exposure

        if not uses_legacy_exposure(view) and self._explicit_event_lock.locked():
            return True
        busy = getattr(self.transport, "render_busy", None)
        return bool(busy and busy(view))

    def _client_backlogged(self) -> bool:
        """Whether the client has stopped draining server-driven frames."""
        backlogged = getattr(self.transport, "client_backlogged", None)
        return bool(backlogged and backlogged())

    def _defer_server_push(self, view: Any, event: Dict[str, Any]) -> None:
        """Queue a push that found the session busy and make sure a drain runs.

        ``LiveViewConsumer._defer_server_push``'s rules: an identical push
        already waiting is superseded (it keeps the position of its latest
        arrival), the queue is bounded and drops the OLDEST past the bound, and
        each entry remembers the view it was addressed to, so a push queued
        before a navigation or a close is dropped rather than applied to
        another view.
        """
        queue = self._deferred_pushes
        if queue is None:
            from .websocket import _MAX_DEFERRED_PUSHES

            queue = self._deferred_pushes = collections.deque(maxlen=_MAX_DEFERRED_PUSHES)
        for queued in list(queue):
            if queued[0] is view and queued[1] == event:
                queue.remove(queued)
        if len(queue) == queue.maxlen:
            logger.debug("[djust] deferred server_push queue full — dropping the oldest")
        queue.append((view, event))
        task = self._push_drain_task
        if task is None or task.done():
            self._push_drain_task = asyncio.ensure_future(self._drain_deferred_pushes())

    async def _drain_deferred_pushes(self) -> None:
        """Replay the deferred pushes, in order, as turns on the lock they waited for."""
        queue = self._deferred_pushes
        while queue:
            view = self.view_instance
            if view is None:
                queue.clear()
                return
            if self._client_backlogged():
                # The client is not reading: hold the pushes (they coalesce and
                # are bounded) until it does, or until the view goes away.
                await asyncio.sleep(_BACKLOG_POLL_S)
                continue

            def take(view: Any = view, queue: Any = queue) -> List[Dict[str, Any]]:
                # After the lock: pushes that queued while this waited join the
                # same turn, so a backlog costs one render (#3001). Entries for
                # a view that has since been replaced are dropped.
                events: List[Dict[str, Any]] = []
                while queue:
                    queued_view, event = queue.popleft()
                    if queued_view is view:
                        events.append(event)
                return events

            await self._run_server_push(view, take)

    async def _run_server_push(self, view: Any, take: Callable[[], List[Dict[str, Any]]]) -> None:
        """Take the render lock, then run what ``take()`` returns as one push turn.

        Never raises: a failure is logged and the caller (a view's task, or the
        drain with a batch already popped) carries on, as the socket's push turn
        does. Work the handlers queued with ``start_async`` runs once the lock is
        free (#2955), even when the render failed.
        """
        from ._exposure import uses_legacy_exposure

        applied = [False]
        explicit_lock = (
            contextlib.nullcontext() if uses_legacy_exposure(view) else self._explicit_event_lock
        )
        try:
            async with explicit_lock, self.transport.event_context(view):
                events = take()
                if events and self.view_instance is view:
                    turn = Turn(
                        "push",
                        self.turn_transport,
                        source="server_push",
                        view=view,
                        scope=self.scope,
                        session_id=self._turn_session_id(),
                        port=self,
                    )
                    turn._locked = True

                    async def body(_turn: Turn) -> None:
                        await self._server_push_body(view, events, applied)

                    await run_turn(turn, body)
        except Exception as exc:  # noqa: BLE001 — logged, never into the caller
            from ._exposure_diagnostics import log_failure_for

            log_failure_for(
                logger,
                (view,),
                exc,
                "Error in server_push: %s",
                type(exc).__name__,
                level="warning",
                traceback=True,
            )
        if applied[0] and self.view_instance is view:
            self._dispatch_async_work(None)

    async def _server_push_body(
        self, view: Any, events: List[Dict[str, Any]], applied: List[bool]
    ) -> None:
        """Apply the pushes, render once, send. Sets ``applied[0]`` once a hook ran.

        The caller holds the render lock and has checked ``view`` is mounted.
        ``LiveViewConsumer._run_server_push_turn``'s stock path: the handler is
        the one ``_prepare_server_push`` allows (``handle_*``, ``@event_handler``
        or ``@push_handler``; anything else is refused with a warning, and a
        payload cannot name another), each hook runs in arrival order and a
        raising one is logged and skipped, and the turn renders unless every
        applied hook asked to skip.
        """
        from ._exposure import uses_legacy_exposure
        from .websocket import LiveViewConsumer, _resolve_skip_render

        legacy = uses_legacy_exposure(view)
        if not legacy:
            try:
                await self.authorize_explicit_turn(view)
            except Exception:  # noqa: BLE001 — no auth provider values on the wire/log
                if self.view_instance is view:
                    await self.deny_explicit_turn()
                view.__dict__.pop("_djust_event_request", None)
                return
        try:
            with _tenant_context(getattr(view, "_tenant", None)):
                render = False
                for event in events:
                    try:
                        hook = LiveViewConsumer._prepare_server_push(view, event)
                        if hook is not None:
                            handler_fn, payload = hook
                            await call_hook(handler_fn, **payload)
                    except Exception as exc:  # noqa: BLE001 — logged, the next push still runs
                        from ._exposure_diagnostics import log_failure_for

                        log_failure_for(
                            logger,
                            (view,),
                            exc,
                            "Error in server_push: %s",
                            type(exc).__name__,
                            level="warning",
                            traceback=True,
                        )
                        if self.view_instance is not view:
                            return
                        continue
                    applied[0] = True
                    if not _resolve_skip_render(view):
                        render = True
                    if self.view_instance is not view:
                        return
                if not applied[0]:
                    return
                if not render:
                    # No frame of its own (#3034): a push has no ``ref`` to acknowledge.
                    await self._flush_all_pending()
                    return
                snapshot_fields = None
                if not legacy:
                    if not await self.commit_explicit_turn(view, source="async"):
                        return
                    snapshot_fields = await self._explicit_event_snapshot(view)
                await self._render_async_result(
                    None, snapshot_fields=snapshot_fields, source="broadcast", broadcast=True
                )
        finally:
            view.__dict__.pop("_djust_event_request", None)

    async def _run_tick(self, interval_ms: int) -> None:
        """The SSE twin of ``LiveViewConsumer._run_tick``: one :meth:`_tick_once`
        every ``interval_ms`` until the view goes away or the task is cancelled."""
        interval_s = interval_ms / 1000.0
        own_view = self.view_instance
        while True:
            await asyncio.sleep(interval_s)
            view = self.view_instance
            if view is None or (own_view is not None and view is not own_view):
                # Closed, navigated away, or another view mounted (it has its own task).
                return
            try:
                await self._tick_once(view)
            except Exception as exc:  # noqa: BLE001 — a failing tick must not end the loop
                from ._exposure_diagnostics import log_failure_for

                log_failure_for(
                    logger,
                    (view,),
                    exc,
                    "Error in tick handler: %s",
                    type(exc).__name__,
                    level="warning",
                    traceback=True,
                )

    async def _tick_once(self, view: Any) -> bool:
        """One tick: ``handle_tick``, render when state changed, send. Returns
        whether a frame was sent. The SSE twin of ``LiveViewConsumer._tick_once``.

        Every early return is a skip, not a stop. A tick yields to a turn in
        progress and to a client that is not draining (it is skipped, not
        queued: the next interval picks up any change, #560), waits for the
        mount frame, and runs the turn chain, so
        ``LIVEVIEW_CONFIG["sync_turn_wrapper"]`` and ``reauth_on_event`` apply.
        """
        if self.view_instance is not view or self._mount_sent_for is not view:
            return False  # gone, or still mounting: the client has nothing to patch yet
        if self._server_turn_busy(view) or self._client_backlogged():
            logger.debug("[djust] Tick on %s skipped — session busy", type(view).__name__)
            return False
        from ._exposure import uses_legacy_exposure

        sent = False
        ran = [False]
        explicit_lock = (
            contextlib.nullcontext() if uses_legacy_exposure(view) else self._explicit_event_lock
        )
        try:
            async with explicit_lock, self.transport.event_context(view):
                if self.view_instance is not view:
                    return False
                turn = Turn(
                    "push",
                    self.turn_transport,
                    source="tick",
                    view=view,
                    scope=self.scope,
                    session_id=self._turn_session_id(),
                    port=self,
                )
                turn._locked = True

                async def body(_turn: Turn) -> None:
                    nonlocal sent
                    sent = await self._tick_body(view, ran)

                await run_turn(turn, body)
        finally:
            # start_async work handle_tick queued runs once the lock is free
            # (#2955), even when the render failed.
            if ran[0] and self.view_instance is view:
                self._dispatch_async_work(None)
        return sent

    async def _tick_body(self, view: Any, ran: List[bool]) -> bool:
        """The locked part of a tick: whether a frame was sent. ``ran[0]`` is set
        once ``handle_tick`` has run."""
        from ._exposure import uses_legacy_exposure
        from ._view_hooks import call_tick
        from .websocket import _resolve_skip_render, _snapshot_assigns

        legacy = uses_legacy_exposure(view)
        if not legacy:
            try:
                await self.authorize_explicit_turn(view)
            except Exception:  # noqa: BLE001 — no auth provider values on the wire/log
                if self.view_instance is view:
                    await self.deny_explicit_turn()
                view.__dict__.pop("_djust_event_request", None)
                return False
        try:
            with _tenant_context(getattr(view, "_tenant", None)):
                pre_assigns = _snapshot_assigns(view)
                await call_tick(view)
                ran[0] = True
                if self.view_instance is not view:
                    return False
                if _resolve_skip_render(view):
                    await self._flush_all_pending()
                    return False
                force_full_html = getattr(view, "_force_full_html", False)
                if pre_assigns == _snapshot_assigns(view) and not force_full_html:
                    await self._flush_all_pending()
                    return False
                snapshot_fields = None
                if not legacy:
                    if not await self.commit_explicit_turn(view, source="async"):
                        return False
                    snapshot_fields = await self._explicit_event_snapshot(view)
                await self._render_async_result(
                    "tick", snapshot_fields=snapshot_fields, source="tick"
                )
                return True
        finally:
            view.__dict__.pop("_djust_event_request", None)

    def stop_server_work(self, view: Any = None) -> None:
        """Stop the tick and drop the queued pushes of ``view`` (every one when
        ``None``). Safe from any thread: ``SSESession.shutdown`` also runs on a
        worker thread (``_StreamGuard.abandon``), where ``Task.cancel`` on the
        loop's task is not allowed, so off the loop the work is handed to it.

        A drain already waiting on the lock finds an empty queue (or another
        view) and ends by itself; nothing it holds outlives the lock.
        """

        def stop() -> None:
            task = self._tick_task
            if task is not None:
                ticking_for = getattr(task, "_djust_tick_view", None)
                if view is None or ticking_for is None or ticking_for is view:
                    self._tick_task = None
                    if not task.done():
                        # A task of a CLOSED loop (multi-loop teardown) cannot be
                        # woken: ``cancel`` raises, and nothing is left to cancel.
                        with contextlib.suppress(RuntimeError):
                            task.cancel()
            queue = self._deferred_pushes
            if queue:
                if view is None:
                    queue.clear()
                else:
                    for queued in [q for q in queue if q[0] is view]:
                        queue.remove(queued)

        owner = self._tick_task or self._push_drain_task
        loop = owner.get_loop() if owner is not None else None
        try:
            here: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if loop is None or loop is here or loop.is_closed() or not loop.is_running():
            stop()
        else:
            loop.call_soon_threadsafe(stop)
