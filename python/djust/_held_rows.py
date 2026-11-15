"""Rows held on a view are read again after a step that wrote to the database (#4112).

A view that keeps a QuerySet on itself (``self._rows = Model.objects.filter(...)``
in ``mount()``), a ``cached_property`` that lists rows, or a model loaded with
``prefetch_related`` is evaluated by the first render and then read from its
cache. Over the HTTP fallback each request builds the view afresh, so the rows
are always current. Over WebSocket and SSE the view lives between turns, and a
handler that wrote to the database used to be answered with the rows as they
were before the write.

The rule, the same on every transport:

* A sync step of the view (an event handler, a ``start_async`` callback or
  ``@background`` work and ``handle_async_result``, ``handle_tick``, a
  server-push hook or ``handle_info``) or an ``async def`` handler or hook,
  or a component's handler, that runs a
  statement which is not a read, on any database alias and through any API
  (the ORM, ``update()`` / ``delete()`` / ``bulk_*``, a raw cursor, a write
  inside ``transaction.atomic`` that is rolled back), marks the view.
* The next render of a marked view first drops what it holds from the database:
  the result cache of every QuerySet held on it, ``cached_property`` values,
  and the QuerySets in the ``_prefetched_objects_cache`` of models held on it.
  The render then reads the rows again, once.
* A turn whose handler only wrote (it changed nothing on the view) renders
  anyway instead of answering ``noop`` (:func:`rows_written`).
* A turn that wrote nothing changes nothing: no reset, no added query.

What counts as a write is decided by the statement's first keyword: anything
but ``SELECT`` (``SELECT ... FOR UPDATE`` included), ``SAVEPOINT``,
``RELEASE``, ``ROLLBACK``, ``BEGIN``, ``COMMIT``, ``SET``, ``SHOW``,
``EXPLAIN``, ``PRAGMA``, ``VALUES``, ``FETCH``, ``CLOSE`` or ``DECLARE``. A
statement it cannot read (a leading comment, bytes) counts as a write: the cost
of a wrong guess that way is one query, the other way stale rows.

Which views a write refreshes: the view that wrote and every view in its tree,
found from it through the parent links (an embedded or sticky child's
``_parent_view``, a component's ``_parent`` / ``_djust_context_parent``, a
bound component's ``_view``) and then down through
``_child_views`` and ``_components`` (at most :data:`_TREE_LIMIT` views). A
view that does not render in this turn keeps its mark until it next renders.

What is reset is found by walking the view's attributes, public and private,
and the dicts, lists, tuples and sets they hold, to :data:`_DEPTH_LIMIT` levels
of nesting and at most :data:`_VISIT_LIMIT` values. Not reset: a list or tuple
of rows (an evaluated snapshot) and the field values of a model instance, a
QuerySet built with ``select_for_update()`` (re-evaluating it outside its
transaction would raise), and every attribute named in the view's
``frozen_rows`` (a QuerySet or a ``cached_property`` the view wants kept as it
was first read).

The recorder is one Django ``execute_wrapper`` placed on every connection when
it connects (the ``connection_created`` signal), at the inside of the wrapper
chain so a ``with connection.execute_wrapper(...)`` block of the project's
(django-debug-toolbar, ``CaptureQueriesContext``) removes its own wrapper and
not this one. Outside a step it is a context-variable read and a call: the
context variable is set only around a view's step, follows the step into
``sync_to_async`` threads (with or without ``thread_sensitive``) and is reset
when the step returns, so nothing is attributed across turns, threads or
requests.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import threading
import weakref
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar

_T = TypeVar("_T")

#: The sync steps whose writes mark the view: the steps a turn's own work runs in
#: (the handler, server-push hooks and ``handle_info``, background work and its
#: result, ``handle_tick``). Not the render, the state store, authorization,
#: ``mount`` / ``handle_params`` (the render that follows them reads every
#: QuerySet for the first time anyway) or the connection lifecycle hooks.
RECORDED_STEPS = frozenset({"event", "hook", "async", "tick"})

#: First keywords of statements that do not change rows.
_READS = frozenset(
    {
        "SELECT",
        "SAVEPOINT",
        "RELEASE",
        "ROLLBACK",
        "BEGIN",
        "COMMIT",
        "SET",
        "SHOW",
        "EXPLAIN",
        "PRAGMA",
        "VALUES",
        "FETCH",
        "CLOSE",
        "DECLARE",
    }
)

#: Nesting walked below a view attribute (``self.x`` is depth 0; ``self.x["k"][0]`` is 2).
_DEPTH_LIMIT = 3
#: Values looked at per reset, all attributes together.
_VISIT_LIMIT = 10_000
#: Views marked per write (the writer's tree).
_TREE_LIMIT = 256


class _Writer:
    """The view a running step belongs to, held weakly (a context copy can outlive it)."""

    __slots__ = ("ref",)

    def __init__(self, view: Any) -> None:
        try:
            self.ref: Callable[[], Any] = weakref.ref(view)
        except TypeError:
            self.ref = lambda: view


_writer: contextvars.ContextVar[Optional[_Writer]] = contextvars.ContextVar(
    "djust_held_rows_writer", default=None
)

#: ``id(view) -> weakref`` of every view marked and not yet rendered.
_marked: Dict[int, Any] = {}
_lock = threading.Lock()


def is_write(sql: Any) -> bool:
    """Whether ``sql`` may change rows (see the module docstring)."""
    if isinstance(sql, str) and sql.startswith("SELECT "):
        return False  # what the ORM emits for every read: no parsing
    if not isinstance(sql, str):
        try:
            sql = str(sql)
        except Exception:  # noqa: BLE001 - an unreadable statement counts as a write
            return True
    head = sql.lstrip().lstrip("(").lstrip()
    end = 0
    for char in head[:12]:
        if not char.isalpha():
            break
        end += 1
    return head[:end].upper() not in _READS


def _recorder(execute: Callable[..., Any], sql: Any, params: Any, many: Any, context: Any) -> Any:
    writer = _writer.get()
    if writer is not None and is_write(sql):
        view = writer.ref()
        if view is not None and id(view) not in _marked:
            mark_tree(view)
    return execute(sql, params, many, context)


def install(connection: Any, **_kwargs: Any) -> None:
    """Put the recorder on ``connection`` (the ``connection_created`` receiver).

    At index 0, the inside of the chain: Django's ``execute_wrapper()`` pops the
    LAST wrapper on exit, so a project wrapper entered before this connection
    connected still removes itself, not this one. Idempotent on a reconnect.
    """
    wrappers = getattr(connection, "execute_wrappers", None)
    if isinstance(wrappers, list) and _recorder not in wrappers:
        wrappers.insert(0, _recorder)


def install_everywhere() -> None:
    """Install on every future connection, and on this thread's open ones (``ready()``)."""
    from django.db import connections
    from django.db.backends.signals import connection_created

    connection_created.connect(install, dispatch_uid="djust_held_rows_recorder")
    for connection in connections.all(initialized_only=True):
        install(connection)


# ---------------------------------------------------------------------------
# Attributing a step to its view
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def writes_by(view: Any) -> Iterator[None]:
    """Attribute the statements run inside the block (and the threads and tasks
    it starts) to ``view``. Usable on the event loop around an ``await``."""
    if view is None:
        yield
        return
    token = _writer.set(_Writer(view))
    try:
        yield
    finally:
        _writer.reset(token)


def attributed(view: Any, fn: Callable[..., _T]) -> Callable[..., _T]:
    """``fn`` as a callable whose statements are attributed to ``view`` where it runs."""
    if view is None:
        return fn

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> _T:
        with writes_by(view):
            return fn(*args, **kwargs)

    return run


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------


def _forget(key: int) -> Callable[[Any], None]:
    def callback(_ref: Any) -> None:
        with _lock:
            _marked.pop(key, None)

    return callback


def _mark(view: Any) -> None:
    key = id(view)
    try:
        ref: Any = weakref.ref(view, _forget(key))
    except TypeError:
        return  # cannot be tracked without keeping it alive
    with _lock:
        _marked[key] = ref


def _related(view: Any, name: str) -> list:
    value = getattr(view, "__dict__", {}).get(name)
    if isinstance(value, dict):
        return list(value.values())
    return []


def mark_tree(view: Any) -> None:
    """Mark ``view`` and the views it shares a page with (see the module docstring)."""
    root, seen_up = view, 0
    while seen_up < _TREE_LIMIT:
        state = getattr(root, "__dict__", {})
        parent = (
            state.get("_parent_view")
            or state.get("_parent")
            or state.get("_djust_context_parent")
            or state.get("_view")  # a bound component's view
        )
        if parent is None or parent is root:
            break
        root, seen_up = parent, seen_up + 1
    pending, seen = [root, view], set()
    while pending and len(seen) < _TREE_LIMIT:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        _mark(current)
        pending.extend(_related(current, "_child_views"))
        pending.extend(_related(current, "_components"))


def is_marked(view: Any) -> bool:
    """Whether ``view`` has a write it has not rendered since."""
    return view is not None and id(view) in _marked


def rows_written(view: Any) -> bool:
    """Whether the turn must render although no assign changed.

    A handler that only writes to the database (deletes a row, saves a form)
    changes nothing on the view, and the auto-skip of an unchanged turn would
    answer ``noop`` with the old rows on the page. Every auto-skip asks this
    first; an explicit ``_skip_render`` request still wins.
    """
    return is_marked(view)


# ---------------------------------------------------------------------------
# The reset
# ---------------------------------------------------------------------------


def refresh_if_written(view: Any) -> bool:
    """Before ``view`` renders: drop what it holds from the database if a step wrote.

    ``False`` (and nothing touched) for a view with no write since its last
    render: one dict lookup.
    """
    if view is None or not _marked:
        return False
    with _lock:
        ref = _marked.pop(id(view), None)
    if ref is None or ref() is not view:
        return False
    reset_held_rows(view)
    return True


@functools.lru_cache(maxsize=1024)
def _cached_property_names(cls: type) -> frozenset:
    from django.utils.functional import cached_property

    names = set()
    for klass in cls.__mro__:
        for name, attr in vars(klass).items():
            if isinstance(attr, (cached_property, functools.cached_property)):
                names.add(getattr(attr, "attrname", None) or getattr(attr, "name", None) or name)
    return frozenset(names)


def frozen_names(view: Any) -> frozenset:
    """The view's ``frozen_rows``: a name, or a collection of names."""
    declared = getattr(type(view), "frozen_rows", ())
    if isinstance(declared, str):
        return frozenset({declared})
    try:
        return frozenset(name for name in declared if isinstance(name, str))
    except TypeError:
        return frozenset()


def reset_held_rows(view: Any) -> None:
    """Drop the database caches ``view`` holds (see the module docstring)."""
    from django.db.models import Model, QuerySet

    state = getattr(view, "__dict__", None)
    if not isinstance(state, dict):
        return
    frozen = frozen_names(view)
    for name in _cached_property_names(type(view)) - frozen:
        state.pop(name, None)

    budget = [_VISIT_LIMIT]

    def visit(value: Any, depth: int) -> None:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(value, QuerySet):
            if value._result_cache is not None and not value.query.select_for_update:
                value._result_cache = None
                value._prefetch_done = False
        elif isinstance(value, Model):
            # Each prefetched relation is a QuerySet holding its rows: emptied,
            # it is read again once and then kept (dropping the whole cache
            # would query on every later render).
            for cached in list(value.__dict__.get("_prefetched_objects_cache", {}).values()):
                if isinstance(cached, QuerySet):
                    cached._result_cache = None
                    cached._prefetch_done = False
        elif depth < _DEPTH_LIMIT:
            if isinstance(value, dict):
                items = list(value.values())
            elif isinstance(value, (list, tuple, set, frozenset)):
                items = list(value)
            else:
                return
            for item in items:
                visit(item, depth + 1)

    for name, value in list(state.items()):
        if name not in frozen:
            visit(value, 0)
