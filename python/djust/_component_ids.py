"""Default component ids that are the same on every render of a view (#4114).

A component built without an explicit ``component_id`` got ``<class>_<8 random hex>``.
A view that builds one inside ``get_context_data`` (the demo project's navbar) then
rendered a different ``id="..."`` on every request, so the HTTP fallback's render
digest never matched and the page always came back whole (#4108).

While a view's render runs (``_held_rows.recorded_render``: the outermost render
method of the view; ``reading``: its ``"render"`` step) or its ``mount()`` runs
(``sync_turn.run_sync_step``) the id is instead derived from what identifies the
component there: the view's class and, for a child, its ``view_id``; the phase
(``"render"`` or ``"mount"``, so a mount-built id never equals a render-built one);
the component's class; and how many components of that class the phase has built so
far. Two runs of the same view that build the same components in the same order get
the same ids; two navbars in one render get different ones.

An id is only kept while it is unique among what the view holds (review of #4114):

* A component built in an earlier render and still held (built lazily, kept in an
  attribute, a list or a dict, or registered in ``view._components``) is not rebuilt, so
  a later render's counter would hand its id to the first component built there. The id
  is then the random one, as before. (A view that rebuilds ``self.nav`` in every render
  holds the previous one while it builds the next, so it gets the random id from the
  second render on: no worse than before, and the usual shape, a component put in the
  context, is not affected.)
* The HTTP GET runs ``get_context_data`` as its own ``"render"`` step and the page
  render after it: the second continues the first's numbering (``carry_over``), so
  components built in both are not numbered 1 twice.
* A mount of an embedded child (``{% live_render %}``) runs before the child has its
  ``view_id``, so two children of one class would number alike: it keeps random ids.

Components built anywhere else (an event handler, ``handle_params``, an ``async def``
mount, a module level) keep the random id. They are saved with the view's state and
restored from it, so their id is already the same on every turn, and a per-step counter
would hand two handler calls the same id.

Ids are the same on every render only if the components of a class are built in the same
order; a render that builds them from parallel threads gets distinct ids in whatever order
the threads happen to run.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import threading
import weakref
from typing import Any, Dict, Iterator, Optional, Set

logger = logging.getLogger(__name__)


class _Scope:
    """The components one run of one view has built so far, by class name."""

    __slots__ = ("view", "phase", "counts", "lock", "held", "rendered")

    def __init__(self, view: Any, phase: str, counts: Optional[Dict[str, int]] = None) -> None:
        try:
            self.view: Any = weakref.ref(view)
        except TypeError:
            self.view = lambda: view
        self.phase = phase
        self.counts: Dict[str, int] = counts if counts is not None else {}
        #: Components may be built on context-copying threads that share this scope.
        self.lock = threading.Lock()
        #: ``component_id`` of every component the view held when the first one was built.
        self.held: Optional[Set[str]] = None
        #: The view's render method ran inside this scope (see :func:`render_ids`).
        self.rendered = False


_scope: contextvars.ContextVar[Optional[_Scope]] = contextvars.ContextVar(
    "djust_component_id_scope", default=None
)

#: The numbering a ``"render"`` step left for the render method that follows it.
_carried: "weakref.WeakKeyDictionary[Any, Dict[str, int]]" = weakref.WeakKeyDictionary()


@contextlib.contextmanager
def render_ids(view: Any, phase: str = "render", *, carry_over: bool = False) -> Iterator[None]:
    """Number the components built inside the block as the ``phase`` of ``view``.

    ``carry_over`` is for the outermost render method: it continues the numbering the
    view's ``"render"`` step (``get_context_data``) left, when that step ended without
    the render method having run inside it (the HTTP GET and POST). The step itself
    leaves its numbering behind only in that case.
    """
    if view is None:
        yield
        return
    counts = None
    if carry_over:
        try:
            counts = _carried.pop(view, None)
        except TypeError:  # not weak-referenceable: nothing carried
            counts = None
    scope = _Scope(view, phase, counts)
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)
        if not carry_over and phase == "render" and not scope.rendered and scope.counts:
            try:
                _carried[view] = scope.counts
            except TypeError:  # not weak-referenceable: nothing to carry
                logger.debug("view %s cannot carry component id counts", type(view).__name__)


def note_rendered() -> None:
    """The view's render method runs inside the active scope: it is not left to carry."""
    scope = _scope.get()
    if scope is not None:
        scope.rendered = True


def _held_ids(view: Any) -> Set[str]:
    """The ids of the components ``view`` holds: attributes, one level into a list, tuple
    or dict attribute, and the keys of ``view._components``."""
    from .components.base import LiveComponent

    held: Set[str] = set()

    def take(value: Any) -> None:
        if isinstance(value, LiveComponent):
            component_id = getattr(value, "component_id", None)
            if isinstance(component_id, str):
                held.add(component_id)

    attributes = getattr(view, "__dict__", None)
    if not isinstance(attributes, dict):
        return held
    for value in list(attributes.values()):
        take(value)
        if isinstance(value, (list, tuple)):
            for item in list(value):
                take(item)
        elif isinstance(value, dict):
            for item in list(value.values()):
                take(item)
    registry = attributes.get("_components")
    if isinstance(registry, dict):
        held.update(key for key in list(registry) if isinstance(key, str))
    return held


def render_id(prefix: str) -> Optional[str]:
    """The next id for a component of class ``prefix`` built in a numbered run, else ``None``
    (also when the id is already taken by a component the view holds)."""
    scope = _scope.get()
    if scope is None:
        return None
    view = scope.view()
    if view is None:
        return None
    with scope.lock:
        n = scope.counts[prefix] = scope.counts.get(prefix, 0) + 1
        if scope.held is None:
            scope.held = _held_ids(view)
        owner = type(view)
        digest = hashlib.sha256(
            (
                "%s.%s:%s:%s:%s:%d"
                % (
                    owner.__module__,
                    owner.__qualname__,
                    getattr(view, "_view_id", None) or "",
                    scope.phase,
                    prefix,
                    n,
                )
            ).encode()
        ).hexdigest()
        candidate = "%s_%s" % (prefix, digest[:8])
        return None if candidate in scope.held else candidate
