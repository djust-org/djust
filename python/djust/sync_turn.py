"""``LIVEVIEW_CONFIG["sync_turn_wrapper"]``: a scope around each sync step.

A turn (``djust.turns``) runs a view's sync code in separate hops: the event
handler, ``mount``, ``handle_params``, a ``start_async`` callback and its
``handle_async_result``, ``handle_tick``, and the render's
``get_context_data()`` (where the JIT evaluates the view's QuerySets), the
authorization checks (``check_permissions``, ``get_object`` and
``has_object_permission``) and the ``on_mount`` / ``connected`` /
``disconnected`` lifecycle hooks. Between the hops the turn awaits, and with
``worker_threads`` the pool thread runs other sessions' hops. A ``LIVEVIEW_MIDDLEWARE`` therefore cannot hold a
database transaction (or any thread-bound state) for the turn: it would span
those awaits and leak into the other sessions' hops.

The wrapper is the supported place for it. It is a callable
``wrapper(view, step, turn)`` that returns a context manager; djust enters it
ON THE THREAD that runs the step, immediately around the step, so whatever it
opens is opened and closed inside one sync call and never spans an await::

    # settings.py
    LIVEVIEW_CONFIG = {"sync_turn_wrapper": "myapp.db.caller_scope"}

    # myapp/db.py
    from contextlib import nullcontext
    from django.db import transaction

    def caller_scope(view, step, turn):
        if turn is not None and turn.transport == "http":
            return nullcontext()        # the request's own transaction holds
        return transaction.atomic()     # e.g. then SET LOCAL the caller

``step`` is one of :data:`SYNC_STEPS`. ``view`` is the view (or component)
whose code runs; ``turn`` is :func:`djust.turns.current_turn` (``None`` when the
step runs outside a turn, such as a ``start_async`` callback after its start
turn ended). A step that runs inside another step on the same thread (a handler
that calls ``get_context_data()``) does not enter the wrapper again.

An ``async def`` handler or hook runs on the event loop and is not wrapped:
its own ``sync_to_async`` calls are the app's to scope. A ``sync_safe`` view's
render runs on the loop, so its wrapper runs there too.

A hop is one wrapper entry, not one step name: a ``start_async`` callback and
the ``handle_async_result`` call that follows it are two hops, each entered on
its own (the awaits between them are the point of the wrapper).

Reported downstream by Point Guard (N-30): Postgres row-level security sets
the caller per transaction, so a socket turn that reads rows needs each sync
hop in a transaction with the caller set.
"""

from __future__ import annotations

import contextvars
import functools
from typing import Any, Callable, ContextManager, Optional, Tuple, TypeVar

__all__ = [
    "SYNC_STEPS",
    "SyncTurnWrapper",
    "guarded_step",
    "run_guarded_step",
    "run_sync_step",
    "sync_step",
]

#: Every ``step`` the wrapper is called with.
#:
#: ``"async"`` covers a ``start_async`` callback AND the ``handle_async_result``
#: that receives its outcome; ``"tick"`` is ``handle_tick`` (#3625).
#:
#: ``"auth"`` is an authorization decision that runs application code: the
#: view-level check (``check_permissions``, Django's access mixins and the
#: decorators replayed from ``dispatch``) and the object-permission check
#: (``get_object`` and ``has_object_permission``). ``"lifecycle"`` is the
#: connection lifecycle: the ``on_mount`` hooks, ``connected()`` and
#: ``disconnected()`` (#3637).
SYNC_STEPS = frozenset(
    {"event", "mount", "handle_params", "hook", "async", "tick", "render", "auth", "lifecycle"}
)

SyncTurnWrapper = Callable[[Any, str, Any], ContextManager[Any]]

_T = TypeVar("_T")

#: The step entered on this thread's current call, so a nested step does not
#: enter the wrapper again.
_active_step: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "djust_sync_step", default=None
)

_resolved: Optional[Tuple[Any, SyncTurnWrapper]] = None


def _wrapper() -> Optional[SyncTurnWrapper]:
    """The configured wrapper, imported once per configured value."""
    global _resolved
    from .config import config

    raw = config.get("sync_turn_wrapper")
    if not raw:
        return None
    cached = _resolved
    if cached is not None and cached[0] is raw:
        return cached[1]
    if isinstance(raw, str):
        from django.utils.module_loading import import_string

        wrapper = import_string(raw)
    else:
        wrapper = raw
    if not callable(wrapper):
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("LIVEVIEW_CONFIG['sync_turn_wrapper'] must be callable")
    _resolved = (raw, wrapper)
    return wrapper


def _is_async(fn: Callable[..., Any]) -> bool:
    """Whether calling ``fn`` returns a coroutine (it must never be wrapped).

    An ``async def`` hook runs on the event loop. Wrapping it in a plain function
    (``functools.wraps`` copies the name but not the coroutine-ness) would hide it
    from ``sync_to_async``, which refuses a coroutine function with a ``TypeError``
    when no wrapper is configured: with one the hook body would silently never run
    (#3637 review).
    """
    from ._view_hooks import is_async_hook

    return is_async_hook(fn)


def run_sync_step(view: Any, step: str, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Call ``fn(*args, **kwargs)`` on this thread inside the configured wrapper."""
    wrapper = _wrapper()
    if wrapper is None or _active_step.get() is not None or _is_async(fn):
        return fn(*args, **kwargs)
    from .turns import current_turn

    token = _active_step.set(step)
    try:
        with wrapper(view, step, current_turn()):
            return fn(*args, **kwargs)
    finally:
        _active_step.reset(token)


def sync_step(view: Any, step: str, fn: Callable[..., _T]) -> Callable[..., _T]:
    """``fn`` as a callable that runs inside the wrapper (for ``sync_to_async``).

    Returns ``fn`` itself when no wrapper is configured, and when ``fn`` is an
    ``async def`` (not a sync step; ``sync_to_async`` then refuses it exactly as
    it does with no wrapper).
    """
    if _wrapper() is None or _is_async(fn):
        return fn

    @functools.wraps(fn)
    def stepped(*args: Any, **kwargs: Any) -> _T:
        return run_sync_step(view, step, fn, *args, **kwargs)

    return stepped


def run_guarded_step(
    view: Any, step: str, fn: Callable[..., _T], /, *args: Any, **kwargs: Any
) -> _T:
    """:func:`run_sync_step` for a step whose outcome is an allow / deny verdict.

    A context manager may suppress an exception (``__exit__`` returning true).
    For an ordinary step that is the wrapper's business, but for a verdict it
    would turn a denial into a pass: ``with wrapper: return check()`` falls off
    the end, so the caller sees ``None`` ("no redirect", "mount may proceed").
    Here a body that did not run to completion makes the call raise
    :class:`~django.core.exceptions.PermissionDenied` instead, so a wrapper can
    never open a way through an authorization check (#3637). Any exception the
    wrapper itself raises, on entry or on exit, propagates as for every step;
    each caller already treats a non-``PermissionDenied`` failure as a denial.
    """
    if _wrapper() is None or _active_step.get() is not None or _is_async(fn):
        return fn(*args, **kwargs)
    completed: list = []

    def verdict() -> _T:
        result = fn(*args, **kwargs)
        completed.append(None)
        return result

    result = run_sync_step(view, step, verdict)
    if not completed:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("The sync_turn_wrapper suppressed a failed authorization step")
    return result


def guarded_step(view: Any, step: str, fn: Callable[..., _T]) -> Callable[..., _T]:
    """``fn`` as a callable that runs through :func:`run_guarded_step`.

    Returns ``fn`` itself when no wrapper is configured or ``fn`` is ``async def``.
    """
    if _wrapper() is None or _is_async(fn):
        return fn

    @functools.wraps(fn)
    def stepped(*args: Any, **kwargs: Any) -> _T:
        return run_guarded_step(view, step, fn, *args, **kwargs)

    return stepped
