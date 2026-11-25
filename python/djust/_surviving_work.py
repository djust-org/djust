"""Background work that survives its view's disposal (#4182).

``start_async(fn, ..., survive_disconnect=True)`` and
``@background(survive_disconnect=True)`` mark the callback with
:func:`mark_surviving`. Disposal (the connection closed, the user navigated
away) then does not cancel the running task: it runs to completion and its
result is discarded. While it runs, :data:`SURVIVING_OWNER` names the view, so
a write to declared state, a push or more scheduling on that view after its
disposal raises :class:`ViewDisposedError` instead of silently going nowhere.

This module imports nothing from djust, so ``_state`` can import it at the top.
"""

from __future__ import annotations

import functools
from contextvars import ContextVar
from typing import Any, Callable

from asgiref.sync import iscoroutinefunction

#: The view whose ``survive_disconnect=True`` task is running in this context.
SURVIVING_OWNER: ContextVar[Any] = ContextVar("djust_surviving_owner", default=None)

#: The HTTP fallback's handle of the task running in this context (an object
#: with an ``abandoned`` flag), set on the task's worker thread. Once the
#: request stops waiting for a survivor and answers, the view the task holds
#: is as gone as a disposed one (#4182 review).
ABANDONABLE_RUN: ContextVar[Any] = ContextVar("djust_abandonable_run", default=None)

_FLAG = "_djust_survive_disconnect"

_MESSAGE = (
    "%s was disposed (%s) while a survive_disconnect=True task "
    "was running, so the task may not %s. Keep the work's results outside the view "
    "(the database, a cache, a channel group) and let the next page read them."
)
_DISPOSED = "its connection closed"
_ABANDONED = "the HTTP fallback answered without waiting for the task"


class ViewDisposedError(RuntimeError):
    """A ``survive_disconnect=True`` task touched its view after the view was disposed.

    The message names the view class and the refused operation, never a value,
    so it is safe to log for an explicit-exposure owner.
    """


def mark_surviving(callback: Callable[..., Any]) -> Callable[..., Any]:
    """Return a wrapper of ``callback`` that survives its view's disposal.

    A real function (``async def`` for a coroutine function), not a marker
    object, because other runners (the HTTP fallback, the test client) call the
    callback directly.
    """
    if is_surviving(callback):
        return callback
    wrapper: Callable[..., Any]
    if iscoroutinefunction(callback):

        @functools.wraps(callback)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await callback(*args, **kwargs)

        wrapper = async_wrapper
    else:

        @functools.wraps(callback)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return callback(*args, **kwargs)

        wrapper = sync_wrapper
    setattr(wrapper, _FLAG, True)
    return wrapper


def is_surviving(callback: Any) -> bool:
    """Whether ``callback`` was marked by :func:`mark_surviving`."""
    return getattr(callback, _FLAG, False) is True


def refuse_if_disposed(view: Any, what: str) -> None:
    """Raise :class:`ViewDisposedError` when a surviving task of ``view`` is the
    caller and ``view`` is disposed, or the HTTP fallback request that ran the
    task has answered without it."""
    if SURVIVING_OWNER.get() is not view:
        return
    if getattr(view, "_djust_child_disposed", False) is True:
        raise ViewDisposedError(_MESSAGE % (type(view).__name__, _DISPOSED, what))
    if getattr(ABANDONABLE_RUN.get(), "abandoned", False) is True:
        raise ViewDisposedError(_MESSAGE % (type(view).__name__, _ABANDONED, what))
