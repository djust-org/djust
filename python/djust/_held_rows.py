"""Rows held on a view are read again after a step that wrote to them (#4112).

A view that keeps a QuerySet on itself (``self._rows = Model.objects.filter(...)``
in ``mount()``) or a ``cached_property`` is evaluated by the first render and
then read from its cache. Over the HTTP fallback each request builds the view
afresh, so it is always current. Over WebSocket and SSE the view lives between
turns, and a handler that wrote to the database used to be answered with the
rows as they were before the write.

The rule, the same on every transport, scoped by table:

* **Writes.** A statement that changes rows, run while one of the view's steps
  runs, marks the view with the table(s) it wrote (:func:`statement_tables`). A
  step is any ``djust.sync_turn`` step but the render, the state store and
  authorization (:data:`RECORDED_STEPS`), and an ``async def`` handler or hook
  with its own ``sync_to_async`` calls. ``rows_changed()`` marks it by hand.
* **Reads.** Every ``SELECT`` run during the view's turns and renders adds the
  tables it reads to the view's read set (bounded; a read whose tables cannot
  be determined makes the set "unknown").
* **The next render** re-reads, once, every evaluated QuerySet held on the view
  whose own tables (:func:`queryset_tables`: its compiled SQL, joins,
  subqueries and ``union()`` parts, plus the tables of its ``prefetch_related``
  lookups and their through tables) meet the written tables, and recomputes
  every ``cached_property`` if the view's read set meets them. A turn whose
  handler only wrote renders instead of answering ``noop`` exactly when
  something it shows is affected (:func:`rows_written`); a write to a table
  the view neither holds nor reads changes nothing, as before #4112.
* **Fallback: slower, never stale.** A write whose table cannot be determined
  (a ``WITH`` that writes, a statement with no readable target, ``rows_changed()``
  with no argument), a QuerySet whose tables cannot be determined, or an
  unknown read set, refreshes everything: every held QuerySet and cached
  property. Each fallback is counted in :data:`fallback_counts` and logged at
  DEBUG with its reason and the view class, no values.

Not reset, on any transport: a model instance (its field values and the
relations it was loaded with), a list of rows, a QuerySet built with
``select_for_update()``, and any QuerySet or ``cached_property`` named in the
view's ``frozen_rows``. Nor is anything the walk below does not reach.

What is walked: every attribute of the view, public and private (QuerySets at
this level first, at no cost to the limit), then the dicts, lists, tuples and
sets they hold, to :data:`_DEPTH_LIMIT` levels, at most :data:`_VISIT_LIMIT`
values. A container whose first :data:`_PROBE` items (a dict's values) are
plain scalars is not walked item by item. When the limit is reached the walk
stops, and a warning is logged once per view class.

Which views a write marks: the view that ran the step, the views above it
(``_parent_view``, a component's ``_parent`` / ``_djust_context_parent``, a
bound component's ``_view``) and every view and component below the topmost
(``_child_views``, ``_components``), at most :data:`_TREE_LIMIT`. Read sets are
compared across the same tree. A view that does not render in this turn keeps
its mark until it next renders: nothing is sent to it on its own.

The recorder is one Django ``execute_wrapper`` put on every connection when it
connects (``connection_created``), at index 0 of ``execute_wrappers``: the
OUTERMOST position; a project's ``execute_wrapper()`` blocks pop the last entry
on exit, so they still remove their own. It records after the statement
returned: a statement a wrapper inside it refused, or that the database
rejected, records nothing. Outside a djust turn or step it costs two
context-variable reads.
"""

from __future__ import annotations

import collections
import contextlib
import contextvars
import datetime
import decimal
import functools
import logging
import threading
import uuid
import weakref
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: ``djust.sync_turn`` steps whose WRITES are not recorded, and why. Every other
#: step is recorded (pinned against ``SYNC_STEPS`` and against the hooks each
#: step runs, in ``test_held_rows_4112.py``). Reads are recorded in every step.
NOT_RECORDED: Dict[str, str] = {
    "render": (
        "the render reads what the step before it wrote; a write there would "
        "re-read on the next turn for nothing it shows"
    ),
    "state_store": "djust's own save of the view's state",
    "auth": "an authorization decision; an audit write there is not a row the view shows",
}

#: The steps whose writes mark the view.
RECORDED_STEPS: FrozenSet[str] = frozenset(
    {"event", "mount", "handle_params", "hook", "async", "tick", "lifecycle"}
)

#: First keywords of statements that change no rows.
_READS = frozenset(
    {
        "SELECT",
        "VALUES",
        "TABLE",
        "SHOW",
        "EXPLAIN",
        "DESCRIBE",
        "DESC",
        "USE",
        "PRAGMA",
        "SET",
        "RESET",
        "BEGIN",
        "START",
        "COMMIT",
        "END",
        "ROLLBACK",
        "ABORT",
        "SAVEPOINT",
        "RELEASE",
        "LISTEN",
        "UNLISTEN",
        "NOTIFY",
        "FETCH",
        "MOVE",
        "CLOSE",
        "DECLARE",
        "DISCARD",
        "DEALLOCATE",
        "PREPARE",
        "LOCK",
    }
)

#: Reads whose tables are collected (the others read no rows).
_ROW_READS = frozenset({"SELECT", "VALUES", "TABLE"})

#: Writes whose target table is named next, and the words that may come first.
_TARGET_AFTER = {
    "INSERT": ("INTO", "IGNORE", "OR", "REPLACE", "ABORT", "FAIL", "ROLLBACK"),
    "REPLACE": ("INTO",),
    "UPDATE": ("ONLY", "OR", "REPLACE", "ABORT", "FAIL", "IGNORE", "ROLLBACK"),
    "DELETE": ("FROM", "ONLY"),
    "MERGE": ("INTO",),
    "TRUNCATE": ("TABLE", "ONLY"),
}

#: Words after a table name in a FROM / JOIN that are not its alias.
_NOT_ALIAS = frozenset(
    {
        "WHERE",
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL",
        "CROSS",
        "OUTER",
        "NATURAL",
        "JOIN",
        "ON",
        "USING",
        "GROUP",
        "ORDER",
        "LIMIT",
        "OFFSET",
        "UNION",
        "EXCEPT",
        "INTERSECT",
        "HAVING",
        "WINDOW",
        "FOR",
        "RETURNING",
        "SET",
        "VALUES",
        "LATERAL",
        "FETCH",
    }
)

#: The project setting naming more tables whose writes do not refresh rows.
IGNORED_TABLES_SETTING = "DJUST_IGNORED_WRITE_TABLES"

#: Nesting walked below a view attribute (``self.x`` is depth 0; ``self.x["k"][0]`` is 2).
_DEPTH_LIMIT = 3
#: Values looked at per reset, in containers, all attributes together.
_VISIT_LIMIT = 10_000
#: Items looked at to decide that a container holds plain scalars only.
_PROBE = 8
#: Views marked per write (the writer's tree).
_TREE_LIMIT = 256
#: Tables kept in a view's read set; past this the set is "unknown".
_READ_SET_LIMIT = 512

_SCALARS = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    decimal.Decimal,
    datetime.date,
    datetime.time,
    datetime.timedelta,
    uuid.UUID,
)

#: Every refresh of everything, by reason (see the module docstring).
fallback_counts: "collections.Counter[str]" = collections.Counter()


class _Writer:
    """The view a running step belongs to, held weakly (a context copy can outlive it).

    ``writes`` is false around a render: its reads count, its writes do not.
    ``noted`` holds the tables already marked by this step, so a loop of
    writes marks the tree once per table.
    """

    __slots__ = ("ref", "writes", "noted")

    def __init__(self, view: Any, writes: bool = True) -> None:
        try:
            self.ref: Callable[[], Any] = weakref.ref(view)
        except TypeError:
            self.ref = lambda: view
        self.writes = writes
        self.noted: Set[Optional[str]] = set()


_writer: contextvars.ContextVar[Optional[_Writer]] = contextvars.ContextVar(
    "djust_held_rows_writer", default=None
)

#: ``id(view) -> [weakref, tables]``: views with a write not yet rendered.
#: ``tables`` is a set of written table names, or ``None`` for "everything".
_marked: Dict[int, List[Any]] = {}
#: ``id(view) -> [weakref, tables, unknown]``: what the view's turns have read.
_read_sets: Dict[int, List[Any]] = {}
_lock = threading.Lock()
_budget_warned: set = set()
_turn_var: Any = None


# ---------------------------------------------------------------------------
# Reading a statement
# ---------------------------------------------------------------------------


def _text(sql: Any) -> Optional[str]:
    """The statement as text: ``str``, ``bytes`` or a psycopg ``sql.Composed`` / ``SQL``."""
    if isinstance(sql, str):
        return sql
    if isinstance(sql, (bytes, bytearray, memoryview)):
        return bytes(sql).decode("utf-8", "replace")
    for _ in range(8):  # a Composed nests Composables; its first part leads the text
        if isinstance(sql, str):
            return sql
        literal = getattr(sql, "string", None)  # psycopg2 sql.SQL
        if isinstance(literal, str):
            return literal
        parts = getattr(sql, "seq", None)  # psycopg2 sql.Composed
        if parts is None:
            parts = getattr(sql, "_obj", None)  # psycopg 3 sql.SQL (str) / Composed (list)
        if isinstance(parts, str):
            return parts
        if isinstance(parts, (list, tuple)) and parts:
            sql = parts[0]
            continue
        break
    return None


def _skip_space(text: str, i: int) -> int:
    """Past whitespace and ``--`` / ``/* */`` comments."""
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            break
    return i


def _word(text: str, i: int) -> Tuple[str, int]:
    """The keyword at ``i`` (upper case) and the index after it."""
    start = i
    while i < len(text) and (text[i].isalnum() or text[i] == "_"):
        i += 1
    return text[start:i].upper(), i


def _skip_group(text: str, i: int) -> int:
    """``text[i]`` is ``(``: the index after its matching ``)``, or -1."""
    depth, n = 0, len(text)
    while i < n:
        char = text[i]
        if char in "'\"`":
            end = _string_end(text, i)
            if end < 0:
                return -1
            i = end
            continue
        if text.startswith("--", i) or text.startswith("/*", i):
            i = _skip_space(text, i)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _string_end(text: str, i: int) -> int:
    """``text[i]`` opens a quoted string or identifier: the index after it, or -1."""
    quote = text[i]
    end = text.find(quote, i + 1)
    while end >= 0 and text.startswith(quote * 2, end):
        end = text.find(quote, end + 2)
    return -1 if end < 0 else end + 1


def _identifier(text: str, i: int) -> Tuple[str, int]:
    """A possibly quoted, possibly schema-qualified name: its last part, lower case."""
    name = ""
    while i < len(text):
        if text[i] in '"`[':
            close = {'"': '"', "`": "`", "[": "]"}[text[i]]
            end = text.find(close, i + 1)
            if end < 0:
                return "", len(text)
            name, i = text[i + 1 : end], end + 1
        else:
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] in "_$"):
                i += 1
            if i == start:
                return name.lower(), i
            name = text[start:i]
        if i < len(text) and text[i] == ".":
            i += 1
            continue
        return name.lower(), i
    return name.lower(), i


def _read_tables(text: str) -> Optional[FrozenSet[str]]:
    """The tables a read names after ``FROM`` / ``JOIN``; ``None`` when unknown.

    Unknown: a function call in a ``FROM`` (``FROM generate_series(...)``,
    ``FROM json_each(...)``), which may read anything. A ``FROM`` inside an
    expression (``EXTRACT(YEAR FROM "t"."c")``) adds a name that is not a
    table; that only widens the set.
    """
    tables: Set[str] = set()
    n, i = len(text), 0
    while i < n:
        char = text[i]
        if char == "'":
            end = _string_end(text, i)
            if end < 0:
                return None
            i = end
            continue
        if char == "-" and text.startswith("--", i) or char == "/" and text.startswith("/*", i):
            i = _skip_space(text, i)
            continue
        if not (char.isalpha() or char == "_"):
            i += 1
            continue
        word, j = _word(text, i)
        if word not in ("FROM", "JOIN"):
            i = j
            continue
        i = j
        while True:
            i = _skip_space(text, i)
            if i < n and text[i] == "(":
                break  # a subquery: its own FROMs are read as the scan goes on
            name, k = _identifier(text, i)
            if not name:
                break
            k = _skip_space(text, k)
            if k < n and text[k] == "(":
                return None  # a function in FROM
            tables.add(name)
            i = k
            word, k = _word(text, i)
            if word == "AS":
                i = _skip_space(text, k)
                _, i = _identifier(text, i)
            elif word and word not in _NOT_ALIAS:
                i = k  # an alias
            elif i < n and text[i] in '"`':
                _, i = _identifier(text, i)
            i = _skip_space(text, i)
            if i < n and text[i] == ",":
                i += 1
                continue
            break
    return frozenset(tables)


def _write_targets(text: str, i: int, keyword: str) -> Optional[FrozenSet[str]]:
    """The tables a write names as its target; ``None`` when there is none to read."""
    after = _TARGET_AFTER.get(keyword)
    if after is None:
        return None
    i = _skip_space(text, i)
    for _ in range(3):
        word, j = _word(text, i)
        if word not in after:
            break
        i = _skip_space(text, j)
    tables = set()
    while True:
        name, i = _identifier(text, i)
        if not name:
            return None
        tables.add(name)
        i = _skip_space(text, i)
        if keyword == "TRUNCATE" and i < len(text) and text[i] == ",":
            i = _skip_space(text, i + 1)
            continue
        return frozenset(tables)


def _with_writes(text: str, i: int, depth: int) -> bool:
    """A ``WITH`` statement writes if any of its bodies or its final statement does."""
    if depth > 4:
        return True
    i = _skip_space(text, i)
    word, j = _word(text, i)
    if word == "RECURSIVE":
        i = j
    while True:
        i = _skip_space(text, i)
        _, i = _identifier(text, i)
        i = _skip_space(text, i)
        if i < len(text) and text[i] == "(":  # a column list
            i = _skip_group(text, i)
            if i < 0:
                return True
            i = _skip_space(text, i)
        word, i = _word(text, i)
        if word != "AS":
            return True
        i = _skip_space(text, i)
        word, j = _word(text, i)
        if word == "NOT":
            i = _skip_space(text, j)
            word, j = _word(text, i)
        if word == "MATERIALIZED":
            i = _skip_space(text, j)
        if i >= len(text) or text[i] != "(":
            return True
        end = _skip_group(text, i)
        if end < 0:
            return True
        if _classify(text[i + 1 : end - 1], depth + 1)[0] == "write":
            return True
        i = _skip_space(text, end)
        if i < len(text) and text[i] == ",":
            i += 1
            continue
        return _classify(text[i:], depth + 1)[0] == "write"


def _classify(text: str, depth: int = 0) -> Tuple[str, Optional[FrozenSet[str]]]:
    """``("read", tables)``, ``("write", tables)`` or ``("other", empty)``.

    ``tables`` is ``None`` when they cannot be determined.
    """
    i = _skip_space(text, 0)
    while i < len(text) and text[i] == "(":
        i = _skip_space(text, i + 1)
    keyword, j = _word(text, i)
    if not keyword:
        return "write", None  # unreadable: refresh everything, never miss a write
    if keyword == "WITH":
        if _with_writes(text, j, depth):
            return "write", None
        return "read", _read_tables(text)
    if keyword in _READS:
        if keyword in _ROW_READS:
            return "read", _read_tables(text)
        return "other", frozenset()
    return "write", _write_targets(text, j, keyword)


@functools.lru_cache(maxsize=4096)
def _classify_cached(text: str) -> Tuple[str, Optional[FrozenSet[str]]]:
    return _classify(text)


def statement_tables(sql: Any) -> Tuple[str, Optional[FrozenSet[str]]]:
    """``(kind, tables)`` of a statement: ``"read"``, ``"write"`` or ``"other"``.

    The first keyword decides, after leading whitespace, comments and opening
    parentheses: :data:`_READS` change no rows (``SELECT ... FOR UPDATE``
    included); ``WITH`` writes if one of its bodies or its final statement
    writes; anything else writes. A read's tables are the names after ``FROM``
    and ``JOIN``; a write's are its target (``INSERT INTO``, ``UPDATE``,
    ``DELETE FROM``, ``MERGE INTO``, ``TRUNCATE``). ``None`` tables mean they
    could not be determined. What this cannot see: a ``SELECT`` that writes
    (``nextval()``, a function with side effects, ``SELECT ... INTO``),
    ``EXPLAIN ANALYZE`` of a write, and ``cursor.callproc()`` (it bypasses
    Django's execute wrappers): call ``rows_changed()`` for those.
    """
    text = _text(sql)
    if text is None:
        return "write", None
    if len(text) > 20_000:
        return _classify(text)
    return _classify_cached(text)


@functools.lru_cache(maxsize=1)
def ignored_tables() -> FrozenSet[str]:
    """Tables whose writes do not refresh held rows: Django's and djust's bookkeeping.

    ``django_session`` (the session, and djust's default state store), every
    ``DatabaseCache`` table in ``CACHES``, and the tables named in
    ``DJUST_IGNORED_WRITE_TABLES`` (a project's state-store table, a heartbeat
    or audit table no view shows).
    """
    from django.conf import settings

    tables = {"django_session"}
    for cache in (getattr(settings, "CACHES", None) or {}).values():
        if isinstance(cache, dict) and str(cache.get("BACKEND", "")).endswith("db.DatabaseCache"):
            location = cache.get("LOCATION")
            if isinstance(location, str) and location:
                tables.add(location.lower())
    extra = getattr(settings, IGNORED_TABLES_SETTING, ()) or ()
    if isinstance(extra, str):
        extra = (extra,)
    tables.update(name.lower() for name in extra if isinstance(name, str))
    return frozenset(tables)


def _settings_changed(setting: str = "", **_kwargs: Any) -> None:
    if setting in ("CACHES", IGNORED_TABLES_SETTING):
        ignored_tables.cache_clear()


def is_write(sql: Any) -> bool:
    """Whether ``sql`` may change rows a view shows (a write not only to :func:`ignored_tables`)."""
    kind, tables = statement_tables(sql)
    if kind != "write":
        return False
    return tables is None or bool(tables - ignored_tables())


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------


def _turn_view() -> Any:
    global _turn_var
    if _turn_var is None:
        from .turns import _current_turn

        _turn_var = _current_turn
    ref = _turn_var.get()
    if ref is None:
        return None
    turn = ref()
    return None if turn is None else turn.view


def _recorder(execute: Callable[..., Any], sql: Any, params: Any, many: Any, context: Any) -> Any:
    writer = _writer.get()
    if writer is None:
        view = _turn_view()
        if view is None:
            return execute(sql, params, many, context)
        result = execute(sql, params, many, context)
        kind, tables = statement_tables(sql)
        if kind == "read":
            note_reads(view, tables)
        return result
    result = execute(sql, params, many, context)
    view = writer.ref()
    if view is None:
        return result
    kind, tables = statement_tables(sql)
    if kind == "read":
        note_reads(view, tables)
    elif kind == "write" and writer.writes:
        _note_write(writer, view, tables, "a write whose table could not be read")
    return result


def _note_write(writer: _Writer, view: Any, tables: Optional[FrozenSet[str]], reason: str) -> None:
    if tables is not None:
        tables = tables - ignored_tables()
        if not tables:
            return
        if tables <= writer.noted and id(view) in _marked:
            return
        writer.noted.update(tables)
    elif None in writer.noted and id(view) in _marked:
        return
    else:
        writer.noted.add(None)
        _fallback(reason, view)
    mark_tree(view, tables)


def _fallback(reason: str, view: Any) -> None:
    fallback_counts[reason] += 1
    logger.debug(
        "djust: %s; %s.%s re-reads everything it holds (#4112)",
        reason,
        type(view).__module__,
        type(view).__qualname__,
    )


def install(connection: Any, **_kwargs: Any) -> None:
    """Put the recorder on ``connection`` (the ``connection_created`` receiver).

    At index 0, the outermost wrapper. Django's ``execute_wrapper()`` pops the
    LAST wrapper on exit, so a project wrapper entered before this connection
    connected still removes itself, not this one. Idempotent on a reconnect.
    """
    wrappers = getattr(connection, "execute_wrappers", None)
    if isinstance(wrappers, list) and _recorder not in wrappers:
        wrappers.insert(0, _recorder)


def install_everywhere() -> None:
    """Install on every future connection, and on this thread's open ones (``ready()``)."""
    from django.core.signals import setting_changed
    from django.db import connections
    from django.db.backends.signals import connection_created

    connection_created.connect(install, dispatch_uid="djust_held_rows_recorder")
    setting_changed.connect(_settings_changed, dispatch_uid="djust_held_rows_settings")
    for connection in connections.all(initialized_only=True):
        install(connection)


# ---------------------------------------------------------------------------
# Attributing a step to its view
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def writes_by(view: Any, *, writes: bool = True) -> Iterator[None]:
    """Attribute the statements run inside the block (and the threads and tasks
    it starts) to ``view``. Usable on the event loop around an ``await``.
    ``writes=False``: its reads count, its writes do not (a render)."""
    if view is None:
        yield
        return
    token = _writer.set(_Writer(view, writes))
    try:
        yield
    finally:
        _writer.reset(token)


def attributed(view: Any, fn: Callable[..., _T], *, writes: bool = True) -> Callable[..., _T]:
    """``fn`` as a callable whose statements are attributed to ``view`` where it runs."""
    if view is None:
        return fn

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> _T:
        with writes_by(view, writes=writes):
            return fn(*args, **kwargs)

    return run


# ---------------------------------------------------------------------------
# Read sets and marks
# ---------------------------------------------------------------------------


def _forget(registry: Dict[int, Any], key: int) -> Callable[[Any], None]:
    """A weakref callback dropping ``registry[key]`` once its view is collected.

    It takes no lock: the garbage collector runs it at any allocation, also in a
    thread that already holds ``_lock`` (``_mark`` / ``note_reads`` allocate
    under it), and a non-reentrant acquire there deadlocks the thread. It drops
    the entry only if it still holds the collected reference, so an entry made
    since for a new view at the same ``id()`` stays.
    """

    def callback(ref: Any) -> None:
        entry = registry.get(key)
        if entry is not None and entry[0] is ref:
            registry.pop(key, None)

    return callback


def note_reads(view: Any, tables: Optional[FrozenSet[str]]) -> None:
    """Add what a read named to ``view``'s read set (``None``: it could not be read)."""
    entry = _read_sets.get(id(view))
    if entry is not None and entry[0]() is view:
        if entry[2] or (tables is not None and tables <= entry[1]):
            return
    else:
        try:
            ref = weakref.ref(view, _forget(_read_sets, id(view)))
        except TypeError:
            return
        entry = [ref, set(), False]
        with _lock:
            _read_sets[id(view)] = entry
    if tables is None or len(entry[1]) + len(tables) > _READ_SET_LIMIT:
        entry[2] = True
        entry[1] = set()
    else:
        entry[1].update(tables)


def read_set(view: Any) -> Tuple[FrozenSet[str], bool]:
    """``(tables, unknown)`` read by ``view`` and the views it shares a page with."""
    tables: Set[str] = set()
    for node in _tree(view):
        entry = _read_sets.get(id(node))
        if entry is not None and entry[0]() is node:
            if entry[2]:
                return frozenset(), True
            tables |= entry[1]
    return frozenset(tables), False


def _mark(view: Any, tables: Optional[FrozenSet[str]]) -> None:
    key = id(view)
    with _lock:
        entry = _marked.get(key)
        if entry is not None and entry[0]() is view:
            if entry[1] is not None:
                entry[1] = None if tables is None else entry[1] | set(tables)
            return
        try:
            ref: Any = weakref.ref(view, _forget(_marked, key))
        except TypeError:
            return  # cannot be tracked without keeping it alive
        _marked[key] = [ref, None if tables is None else set(tables)]


def _is_view_like(value: Any) -> bool:
    """A view or a component: what a parent link or a child registry holds.

    A type check only, never truthiness: a QuerySet in an attribute that
    happens to be called ``_parent`` must not be evaluated here.
    """
    kind = type(value)
    return callable(getattr(kind, "get_context_data", None)) or callable(
        getattr(kind, "render", None)
    )


_PARENT_LINKS = ("_parent_view", "_parent", "_djust_context_parent", "_view")


def _parent_of(node: Any) -> Any:
    state = getattr(node, "__dict__", None) or {}
    for name in _PARENT_LINKS:
        value = state.get(name)
        if value is not None and value is not node and _is_view_like(value):
            return value
    return None


def _children_of(node: Any) -> List[Any]:
    state = getattr(node, "__dict__", None) or {}
    found: List[Any] = []
    for name in ("_child_views", "_components"):
        registry = state.get(name)
        if isinstance(registry, dict):
            found.extend(value for value in registry.values() if _is_view_like(value))
    return found


def _tree(view: Any) -> List[Any]:
    """``view``, the views above it and every view below the topmost (bounded)."""
    root = view
    for _ in range(_TREE_LIMIT):
        parent = _parent_of(root)
        if parent is None:
            break
        root = parent
    pending, seen, nodes = [root, view], set(), []
    while pending and len(seen) < _TREE_LIMIT:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        nodes.append(current)
        pending.extend(_children_of(current))
    return nodes


def mark_tree(view: Any, tables: Optional[FrozenSet[str]] = None) -> None:
    """Mark ``view`` and its tree as written: ``tables``, or everything (``None``)."""
    for node in _tree(view):
        _mark(node, tables)


def named_tables(targets: Any) -> Optional[FrozenSet[str]]:
    """``rows_changed()``'s arguments as tables: models or names; none is everything."""
    if not targets:
        return None
    tables: Set[str] = set()
    for target in targets:
        if isinstance(target, str):
            tables.add(target.lower())
        elif hasattr(target, "_meta"):
            tables |= {name.lower() for name in _model_tables(target)}
        else:
            raise TypeError("rows_changed() takes model classes or table names")
    return frozenset(tables)


def is_marked(view: Any) -> bool:
    """Whether ``view`` has a write it has not rendered since."""
    return view is not None and id(view) in _marked


def written_tables(view: Any) -> Optional[FrozenSet[str]]:
    """The tables written since ``view`` last rendered; ``None`` for everything."""
    entry = _marked.get(id(view))
    if entry is None or entry[1] is None:
        return None
    return frozenset(entry[1])


def unmark(view: Any) -> None:
    """A render of ``view`` succeeded: what it holds is current again."""
    with _lock:
        _marked.pop(id(view), None)


def _plan(view: Any) -> Tuple[bool, Optional[FrozenSet[str]], bool]:
    """``(everything, written, reads_affected)`` for a marked view."""
    written = written_tables(view)
    if written is None:
        return True, None, True
    reads, unknown = read_set(view)
    if unknown:
        _fallback("a read whose tables could not be read", view)
        return True, None, True
    return False, written, bool(written & reads)


def rows_written(view: Any) -> bool:
    """Whether the turn must render although no assign changed.

    A handler that only writes (deletes a row, saves a form) changes nothing on
    the view, and the auto-skip would answer ``noop`` with the old rows on the
    page. Every auto-skip asks this first: true when the write touched a table
    the view read or a held QuerySet reads. A write to a table the view does
    not show is forgotten here and the turn stays a ``noop``. An explicit
    ``skip_render()`` still wins (the mark then waits for the next render).
    """
    if not is_marked(view):
        return False
    everything, written, reads_affected = _plan(view)
    if everything or reads_affected:
        return True
    assert written is not None
    if any(_affected(qs, written) for qs in _held_querysets(view)):
        return True
    unmark(view)
    return False


# ---------------------------------------------------------------------------
# A QuerySet's tables
# ---------------------------------------------------------------------------


def _relation(model: Any, name: str) -> Optional[Tuple[Any, Optional[Any]]]:
    """``(related model, through model or None)`` of ``model.name``; ``None`` if unknown."""
    try:
        field = model._meta.get_field(name)
    except Exception:  # noqa: BLE001 - an accessor name (``user_set``) is looked up below
        field = None
        for rel in model._meta.related_objects:
            if rel.get_accessor_name() == name:
                field = rel
                break
        if field is None:
            return None
    related = getattr(field, "related_model", None)
    if related is None or isinstance(related, str):
        return None  # a generic relation: anything
    through = None
    if getattr(field, "many_to_many", False):
        remote = getattr(field, "remote_field", None)
        through = getattr(field, "through", None) or getattr(remote, "through", None)
        if through is None or isinstance(through, str):
            return None
    return related, through


def _model_tables(model: Any) -> Set[str]:
    meta = model._meta
    tables = {meta.db_table}
    tables.update(parent._meta.db_table for parent in meta.get_parent_list())
    return tables


def _prefetch_tables(model: Any, lookup: Any) -> Optional[Set[str]]:
    from django.db.models import Prefetch
    from django.db.models.constants import LOOKUP_SEP

    inner = None
    path = lookup
    if isinstance(lookup, Prefetch):
        path, inner = lookup.prefetch_through, lookup.queryset
    if not isinstance(path, str):
        return None
    tables: Set[str] = set()
    current = model
    for part in path.split(LOOKUP_SEP):
        relation = _relation(current, part)
        if relation is None:
            return None
        current, through = relation
        tables |= _model_tables(current)
        if through is not None:
            tables |= _model_tables(through)
    if inner is not None:
        more = queryset_tables(inner)
        if more is None:
            return None
        tables |= more
    return tables


def queryset_tables(queryset: Any) -> Optional[FrozenSet[str]]:
    """The tables evaluating ``queryset`` reads; ``None`` when unknown.

    From its compiled SQL (the base table, joins from filters and
    ``select_related``, subqueries, ``Exists`` / ``OuterRef``, ``union()``
    parts) and from its ``prefetch_related`` lookups (each model on the path,
    their parents and their many-to-many through tables, and a ``Prefetch``'s
    own QuerySet). Unknown (anything raises, a generic relation, a function in
    ``FROM``) means "intersects every write".
    """
    from django.core.exceptions import EmptyResultSet

    try:
        try:
            sql, _params = queryset.query.get_compiler(using=queryset.db).as_sql()
        except EmptyResultSet:
            sql = ""
        tables = _read_tables(sql) if sql else frozenset()
        if tables is None:
            return None
        found = set(tables) | _model_tables(queryset.model)
        for lookup in getattr(queryset, "_prefetch_related_lookups", ()) or ():
            more = _prefetch_tables(queryset.model, lookup)
            if more is None:
                return None
            found |= more
        return frozenset(found)
    except Exception:  # noqa: BLE001 - unknown is the safe answer: re-read
        return None


def _affected(queryset: Any, written: FrozenSet[str]) -> bool:
    tables = queryset_tables(queryset)
    if tables is None:
        _fallback("a held QuerySet whose tables could not be read", queryset)
        return True
    return bool(tables & written)


# ---------------------------------------------------------------------------
# The reset
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1024)
def _cached_property_names(cls: type) -> FrozenSet[str]:
    from django.utils.functional import cached_property

    names = set()
    for klass in cls.__mro__:
        for name, attr in vars(klass).items():
            if isinstance(attr, (cached_property, functools.cached_property)):
                names.add(getattr(attr, "attrname", None) or getattr(attr, "name", None) or name)
    return frozenset(names)


def frozen_names(view: Any) -> FrozenSet[str]:
    """The view's ``frozen_rows``: a name, or a collection of names."""
    declared = getattr(type(view), "frozen_rows", ())
    if isinstance(declared, str):
        return frozenset({declared})
    try:
        return frozenset(name for name in declared if isinstance(name, str))
    except TypeError:
        return frozenset()


def _evaluated(queryset: Any) -> bool:
    return queryset._result_cache is not None and not queryset.query.select_for_update


def _only_scalars(items: List[Any]) -> bool:
    return all(isinstance(item, _SCALARS) for item in items[:_PROBE])


def _warn_budget(view: Any) -> None:
    kind = type(view)
    if kind in _budget_warned:
        return
    _budget_warned.add(kind)
    logger.warning(
        "djust: %s.%s holds more than %d values in containers; QuerySets nested past "
        "that point were not read again after a write (#4112). Keep such a QuerySet "
        "in an attribute of its own, or call rows_changed() and read it in "
        "get_context_data().",
        kind.__module__,
        kind.__qualname__,
        _VISIT_LIMIT,
    )


def _held_querysets(view: Any) -> Iterator[Any]:
    """The evaluated QuerySets held on ``view`` (see the module docstring for the walk)."""
    from django.db.models import QuerySet

    state = getattr(view, "__dict__", None)
    if not isinstance(state, dict):
        return
    frozen = frozen_names(view)
    containers = []
    for name, value in list(state.items()):
        if name in frozen:
            continue
        if isinstance(value, QuerySet):
            if _evaluated(value):
                yield value
        elif isinstance(value, (dict, list, tuple, set, frozenset)):
            containers.append(value)

    budget = [_VISIT_LIMIT]

    def walk(container: Any, depth: int) -> Iterator[Any]:
        items = list(container.values()) if isinstance(container, dict) else list(container)
        if _only_scalars(items):
            return
        for item in items:
            if budget[0] <= 0:
                return
            budget[0] -= 1
            if isinstance(item, QuerySet):
                if _evaluated(item):
                    yield item
            elif depth < _DEPTH_LIMIT and isinstance(item, (dict, list, tuple, set, frozenset)):
                yield from walk(item, depth + 1)

    for container in containers:
        if budget[0] <= 0:
            _warn_budget(view)
            return
        yield from walk(container, 1)
    if budget[0] <= 0:
        _warn_budget(view)


def _empty(queryset: Any) -> None:
    queryset._result_cache = None
    queryset._prefetch_done = False


def reset_held_rows(view: Any, written: Optional[FrozenSet[str]] = None) -> None:
    """Empty what ``view`` holds that ``written`` affects (``None``: everything).

    With ``written``: each held QuerySet whose tables meet it, and the cached
    properties if the view's read set meets it.
    """
    state = getattr(view, "__dict__", None)
    if not isinstance(state, dict):
        return
    if written is None:
        clear_properties = True
    else:
        reads, unknown = read_set(view)
        clear_properties = unknown or bool(written & reads)
    if clear_properties:
        for name in _cached_property_names(type(view)) - frozen_names(view):
            state.pop(name, None)
    for queryset in list(_held_querysets(view)):
        if written is None or _affected(queryset, written):
            _empty(queryset)


def refresh_for_render(view: Any) -> None:
    """Before a render of a marked view: empty what its writes affected."""
    everything, written, _ = _plan(view)
    reset_held_rows(view, None if everything else written)
