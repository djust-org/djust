"""A WebSocket event turn sends patches, not the page: its HTML waits until asked.

A render that produces patches still serialized the whole page (0.36 ms for a
110 KB table) only so the connection could answer a later ``request_html``
recovery, which is rare. Inside :func:`deferring_html` the Rust renderer leaves
that HTML unserialized when it produced patches (``set_defer_html``), returning
``""``; the view records the version whose tree it committed
(:data:`DEFERRED_VERSION_ATTR`), and :func:`materialize` serializes that tree
when the page is needed after all: a recovery, the patch-compression fallback,
or anything else that reads the HTML.

Only a caller that handles ``""`` opens the scope (the runtime's event path for
a transport that arms recovery lazily); every other render path is unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

#: The view attribute naming the Rust version whose HTML was left unserialized.
DEFERRED_VERSION_ATTR = "_djust_deferred_html_version"

_DEFER: ContextVar[bool] = ContextVar("djust_defer_html", default=False)


@contextmanager
def deferring_html() -> Iterator[None]:
    """Renders inside this scope may return ``""`` for the page when they
    produced patches (see the module docstring)."""
    token = _DEFER.set(True)
    try:
        yield
    finally:
        _DEFER.reset(token)


def requested() -> bool:
    """Whether the current render may leave its HTML unserialized."""
    return _DEFER.get()


def deferred_version(view: Any) -> Optional[int]:
    """The Rust version whose HTML ``view``'s last render left unserialized."""
    found = getattr(view, "__dict__", {}).get(DEFERRED_VERSION_ATTR)
    return found if type(found) is int else None


def materialize(view: Any, html: str, version: int) -> str:
    """``html`` itself, or the page of the tree committed at ``version`` when
    the render left it unserialized. Raises ``RuntimeError`` when that tree is
    gone (a later render moved the view on): the caller asked too late."""
    if html or deferred_version(view) != version:
        return html
    found = view._rust_view.committed_html(version)
    if found is None:
        raise RuntimeError("the deferred page HTML is no longer available")
    return str(found)
