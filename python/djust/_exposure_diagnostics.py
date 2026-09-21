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


@contextmanager
def diagnostic_scope() -> Iterator[None]:
    """Inherit the caller's restriction and restore it on every exit path."""
    token = _details_allowed.set(_details_allowed.get())
    try:
        yield
    finally:
        _details_allowed.reset(token)


def restrict_diagnostics(view: Any) -> None:
    """A nonlegacy owner makes the current diagnostic scope value-free."""
    if not uses_legacy_exposure(view):
        _details_allowed.set(False)


def diagnostics_allowed() -> bool:
    """Whether the current scope permits exception details at all."""
    return _details_allowed.get() is True
