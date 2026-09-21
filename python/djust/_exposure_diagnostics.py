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


@contextmanager
def diagnostic_scope() -> Iterator[None]:
    """Inherit the caller's restriction and restore it on every exit path."""
    token = _details_allowed.set(_details_allowed.get())
    owners_token = _owner_slots.set(_owner_slots.get())
    try:
        yield
    finally:
        _owner_slots.reset(owners_token)
        _details_allowed.reset(token)


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
