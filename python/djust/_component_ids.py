"""Default component ids that are the same on every render of a view (#4114).

A component built without an explicit ``component_id`` got ``<class>_<8 random hex>``.
A view that builds one inside ``get_context_data`` (the demo project's navbar) then
rendered a different ``id="..."`` on every request, so the HTTP fallback's render
digest never matched and the page always came back whole (#4108).

While a view's render runs (``_held_rows.recorded_render``: the outermost render
method of the view) the id is instead derived from what identifies the component
in that render: the view's class, the component's class and how many components of
that class the render has built so far. Two renders of the same view that build the
same components in the same order get the same ids; two navbars in one render get
different ones.

Components built anywhere else (``mount()``, an event handler, a module level) keep
the random id. They are saved with the view's state and restored from it, so their
id is already the same on every turn, and a per-step counter would hand two
handler calls the same id.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import weakref
from typing import Any, Dict, Iterator, Optional


class _Scope:
    """The components one render of one view has built so far, by class name."""

    __slots__ = ("view", "counts")

    def __init__(self, view: Any) -> None:
        try:
            self.view: Any = weakref.ref(view)
        except TypeError:
            self.view = lambda: view
        self.counts: Dict[str, int] = {}


_scope: contextvars.ContextVar[Optional[_Scope]] = contextvars.ContextVar(
    "djust_component_id_scope", default=None
)


@contextlib.contextmanager
def render_ids(view: Any) -> Iterator[None]:
    """Number the components built inside the block as the render of ``view``."""
    if view is None:
        yield
        return
    token = _scope.set(_Scope(view))
    try:
        yield
    finally:
        _scope.reset(token)


def render_id(prefix: str) -> Optional[str]:
    """The next id for a component of class ``prefix`` built in a render, else ``None``."""
    scope = _scope.get()
    if scope is None:
        return None
    view = scope.view()
    if view is None:
        return None
    n = scope.counts[prefix] = scope.counts.get(prefix, 0) + 1
    owner = type(view)
    digest = hashlib.sha256(
        ("%s.%s:%s:%d" % (owner.__module__, owner.__qualname__, prefix, n)).encode()
    ).hexdigest()
    return "%s_%s" % (prefix, digest[:8])
