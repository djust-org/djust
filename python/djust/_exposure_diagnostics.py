"""Turn-local restrictions for automatic exception diagnostics.

A nested operation may restrict diagnostics, never grant them. ContextVars
carry the restriction across async and sync_to_async work without sharing it
with another request, connection or sibling mount.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from ._exposure import uses_legacy_exposure

_details_allowed: ContextVar[bool] = ContextVar("djust_exception_details_allowed", default=True)
_owner_slots: ContextVar[tuple[tuple[Any, str], ...]] = ContextVar(
    "djust_diagnostic_owner_slots", default=()
)
_scope_depth: ContextVar[int] = ContextVar("djust_diagnostic_scope_depth", default=0)


@contextmanager
def diagnostic_scope() -> Iterator[None]:
    """Restore caller state, carrying protected failures to an enclosing catch.

    A nested restricted failure must not regain details while unwinding toward
    its transport boundary. The outermost scope still resets on every exit,
    including cancellation; successful nested operations retain local cleanup.
    """
    token = _details_allowed.set(_details_allowed.get())
    owners_token = _owner_slots.set(_owner_slots.get())
    parent_depth = _scope_depth.get()
    depth_token = _scope_depth.set(parent_depth + 1)
    protected_failure = False
    try:
        yield
    except BaseException:
        protected_failure = not diagnostics_allowed()
        raise
    finally:
        _scope_depth.reset(depth_token)
        _owner_slots.reset(owners_token)
        _details_allowed.reset(token)
        if protected_failure and parent_depth:
            _details_allowed.set(False)


def restrict_diagnostics(view: Any) -> None:
    """A nonlegacy owner makes the current diagnostic scope value-free."""
    if not uses_legacy_exposure(view):
        _details_allowed.set(False)


def watch_diagnostic_owner(container: Any, attribute: str) -> None:
    """Track a framework-owned slot so nested catches see owner replacement.

    Call inside an owned diagnostic scope. Slots are inherited by nested calls
    and worker contexts, but removed when that scope exits. Only trusted
    framework call sites register slots; this is not an application callback API.
    """
    slots = _owner_slots.get()
    if not any(owner is container and name == attribute for owner, name in slots):
        _owner_slots.set((*slots, (container, attribute)))
    diagnostics_allowed()


def diagnostics_allowed() -> bool:
    """Apply current owner restrictions before allowing exception details."""
    for container, attribute in _owner_slots.get():
        try:
            restrict_diagnostics(getattr(container, attribute))
        except Exception:  # noqa: BLE001 — an unreadable owner cannot grant diagnostics
            _details_allowed.set(False)
    return _details_allowed.get() is True


def log_failure(
    log: Any,
    exc: BaseException,
    msg: str,
    *args: Any,
    level: str = "error",
    traceback: bool = False,
) -> None:
    """Log a caught failure, value-free unless diagnostics are allowed here.

    The logging counterpart of ``handle_exception``'s gate: a raw ``logger``
    call does not consult ``diagnostics_allowed()``, and undeclared state can
    occur in an exception's message and traceback. ``msg``/``args``/``level`` are
    what the call site passed to its logger and ``traceback=True`` stands for
    ``logger.exception``, so output is unchanged wherever details are allowed.
    """
    if diagnostics_allowed():
        getattr(log, level)(msg, *args, exc_info=exc if traceback else None)
    else:
        log.error("Protected view operation failed")


def log_failure_for(
    log: Any,
    owners: tuple[Any, ...],
    exc: BaseException,
    msg: str,
    *args: Any,
    level: str = "error",
    traceback: bool = False,
) -> None:
    """:func:`log_failure` for code that runs outside a runtime turn.

    Opens a scope restricted to each owner — any nonlegacy owner restricts, none
    grants — so background tasks, consumer hooks and SSE flushes that have no
    turn scope of their own still log value-free for a nonlegacy owner.
    """
    with diagnostic_scope():
        for owner in owners:
            restrict_diagnostics(owner)
        log_failure(log, exc, msg, *args, level=level, traceback=traceback)
