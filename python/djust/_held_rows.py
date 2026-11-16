"""Rows held on a view are read again after a step that wrote to them (#4112).

A view that keeps a QuerySet on itself (``self._rows = Model.objects.filter(...)``
in ``mount()``) or a ``cached_property`` is evaluated by the first render and
then read from its cache. Over the HTTP fallback each request builds the view
afresh, so it is always current. Over WebSocket and SSE the view lives between
turns, and a handler that wrote to the database used to be answered with the
rows as they were before the write.

The rule, the same on every transport:

* **W**, what a step wrote. A statement run while one of the view's steps runs
  (:data:`RECORDED_STEPS`, and ``async def`` handlers and hooks) is classified
  by :func:`statement_tables`. A write names its table only when it is one of
  the exact single-table shapes Django's ORM emits (:func:`_write_table`);
  every other statement that is not a recognised read or a recognised
  statement that changes no rows is a write to UNKNOWN tables.
* **R**, what the view read: the tables named by the ``SELECT`` statements its
  own code and its renders ran (:func:`_read_tables`, the same kind of strict
  reader; a read it cannot follow makes R UNKNOWN for that view). The
  framework's own reads (the per-turn authorization and session checks) run
  outside the view's steps and renders and do not enter R. R is UNKNOWN until
  the view has completed one render under recording, again after a restore
  (:func:`reads_unknown`), and whenever the view holds a cached property value
  or evaluated rows that were filled while nothing recorded (a QuerySet
  filled outside every step and render of the view's tree, a cached property
  not computed in one).
* **Decision.** When W is known, R is known and they do not meet, nothing is
  reset and a handler that changed nothing answers ``noop``, as before #4112.
  Otherwise everything the view holds is read again before its next render:
  every evaluated QuerySet held on it and every ``cached_property`` (minus
  ``frozen_rows``), and the turn renders even if nothing else changed.
  ``DJUST_REFRESH_ON_ANY_WRITE = True`` makes every recorded write take the
  second branch, for databases that write by themselves (triggers, cascades,
  views over tables, rules).

Table names are compared as their last identifier part, case-folded: two
schemas holding tables of the same name are one name here, which can only
refresh more than needed.

A write made by the database itself (a trigger, ``ON DELETE CASCADE``, a
database view over the written table) or outside the view's steps cannot be
seen: ``rows_changed()`` or the setting above.

The mark a write leaves is kept until a render of the view succeeds, and a
mark made while that render ran (another thread's write) survives it.

What is walked, only on a turn that wrote (to decide and to reset): every
attribute the view's code set (not the framework's own, ``_framework_attrs``),
then the dicts, lists, tuples and sets they hold, to :data:`_DEPTH_LIMIT`
levels, every item. A turn that wrote nothing walks nothing: a QuerySet stamps
itself when it fills (which view's recorded step or render it filled in, from a
wrapper around ``QuerySet._fetch_all``) and the cached properties a step or
render fills are noted then. Not reset: model instances, lists of rows,
``select_for_update()`` QuerySets, ``frozen_rows``.

The recorder is one Django ``execute_wrapper`` at index 0 (the outermost) of
every connection's ``execute_wrappers``, installed when the connection
connects. It records after the statement returned, so a refused or failed
statement records nothing. Outside a view's step or render it is a single
context-variable read.
"""

from __future__ import annotations

import contextlib
import contextvars
import collections
import functools
import logging
import re
import threading
import weakref
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: ``djust.sync_turn`` steps whose WRITES are not recorded, and why. Every other
#: step is recorded (pinned against ``SYNC_STEPS`` and against the hooks each
#: step runs, in ``test_held_rows_4112.py``).
NOT_RECORDED: Dict[str, str] = {
    "render": (
        "the render reads what the step before it wrote; a write there would "
        "re-read on the next turn for nothing it shows"
    ),
    "state_store": "djust's own save of the view's state",
    "auth": "an authorization decision; an audit write there is not a row the view shows",
}

#: The steps whose writes mark the view (and whose reads enter its read set).
RECORDED_STEPS: FrozenSet[str] = frozenset(
    {"event", "mount", "handle_params", "hook", "async", "tick", "lifecycle"}
)

#: Statements that change no rows (first keyword).
_NO_ROWS = frozenset(
    {
        "SAVEPOINT",
        "RELEASE",
        "ROLLBACK",
        "BEGIN",
        "START",
        "COMMIT",
        "END",
        "SET",
        "RESET",
        "SHOW",
        "PRAGMA",
        "LISTEN",
        "UNLISTEN",
        "NOTIFY",
        "DECLARE",
        "FETCH",
        "MOVE",
        "CLOSE",
        "DEALLOCATE",
        "DISCARD",
        "LOCK",
        "DESCRIBE",
        "DESC",
        "USE",
        "EXPLAIN",  # unless EXPLAIN ANALYZE, which runs the statement
    }
)

#: Words that may follow a table (and its alias) in a FROM / JOIN.
_AFTER_TABLE = frozenset(
    {
        "WHERE",
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL",
        "CROSS",
        "NATURAL",
        "JOIN",
        "ON",
        "USING",
        "GROUP",
        "ORDER",
        "LIMIT",
        "OFFSET",
        "HAVING",
        "WINDOW",
        "UNION",
        "EXCEPT",
        "INTERSECT",
        "FOR",
        "FETCH",
        "OUTER",
    }
)

#: Functions whose argument list uses FROM without naming a table.
_FROM_IN_ARGUMENTS = frozenset({"EXTRACT", "SUBSTRING", "TRIM", "OVERLAY", "POSITION"})

#: Words that must not appear outside parentheses after the table of a write.
_WRITE_TAIL_BANNED = frozenset({"FROM", "USING", "JOIN", "INTO", "SELECT", "WITH"})

#: The project setting naming more tables whose writes do not refresh rows.
IGNORED_TABLES_SETTING = "DJUST_IGNORED_WRITE_TABLES"
#: The project setting: every recorded write refreshes everything (round-1 rule).
ANY_WRITE_SETTING = "DJUST_REFRESH_ON_ANY_WRITE"

#: Nesting walked below a view attribute (``self.x`` is depth 0; ``self.x["k"][0]`` is 2).
_DEPTH_LIMIT = 3
#: Views marked per write (the writer's tree).
_TREE_LIMIT = 256
#: Tables kept in a view's read set; past this the set is UNKNOWN.
_READ_SET_LIMIT = 512


#: Every "refresh everything" decision, by reason.
fallback_counts: "collections.Counter[str]" = collections.Counter()


class _Writer:
    """The view a running step or render belongs to, held weakly.

    ``writes`` is false around a render: its reads count, its writes do not.
    ``noted`` holds the tables already marked by this step.
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


class _Reads:
    """What a view's own code and renders read."""

    __slots__ = ("ref", "tables", "unknown", "complete", "known", "dropped")

    def __init__(self, ref: Any) -> None:
        self.ref = ref
        self.tables: Set[str] = set()
        self.unknown = False  # a read could not be followed, or the set overflowed
        self.complete = False  # one render finished under recording
        self.known: FrozenSet[str] = frozenset()  # cached properties filled while recording
        self.dropped: FrozenSet[str] = frozenset()  # emptied by a reset in this step


#: ``id(view) -> [weakref, tables or None, generation]``: writes not yet rendered.
_marked: Dict[int, List[Any]] = {}
#: ``id(view) -> _Reads``.
_read_sets: Dict[int, _Reads] = {}
_lock = threading.Lock()
_generation = [0]


# ---------------------------------------------------------------------------
# Reading a statement
# ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<comment>--[^\n]*(?:\n|$)|/\*.*?\*/)
  | (?P<dollar>\$(?P<tag>[A-Za-z_]\w*|)\$.*?\$(?P=tag)\$)
  | (?P<estr>[Ee]'(?:[^'\\]|\\.|'')*')
  | (?P<str>'(?:[^']|'')*')
  | (?P<qid>"(?:[^"]|"")*")
  | (?P<bid>`(?:[^`]|``)*`)
  | (?P<cast>::)
  | (?P<param>%s|%\(\w+\)s|\$\d+|:[A-Za-z_]\w*)
  | (?P<word>[A-Za-z_][A-Za-z0-9_$]*)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<punct>[~!@\#%^&|?<>=+\-*/(),.;\[\]{}])
  | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)

#: A word that makes a statement a write wherever it appears.
_WRITE_WORDS = frozenset({"INSERT", "DELETE", "MERGE", "TRUNCATE"})


def _tokens(text: str) -> Optional[List[Tuple[str, str]]]:
    """``(kind, value)`` tokens without whitespace and comments; ``None`` if unreadable.

    Kinds: ``word`` (upper-cased), ``qid`` (``"..."``), ``bid`` (backticks),
    ``str`` / ``estr`` (``'...'``, ``E'...'``), ``dollar`` (``$tag$...$tag$``),
    ``param`` (``%s``, ``%(name)s``, ``$1``, ``:name``), ``num``, ``cast``
    (``::``) and ``punct``: one character each, parentheses, comma, dot,
    semicolon, brackets and every operator character ``~ ! @ # % ^ & | ? < > =
    + - * /``. A longer operator (``->>``, ``#>``, ``@>``, ``<@``, ``&&``,
    ``?|``, ``?&``, ``||``, ``<=``, ``!=``...) is a run of these and names no
    table, and ``?`` (a qmark placeholder or the jsonb operator) is one of them.
    Any other character (an unterminated string or comment, a non-ASCII bare
    name) makes the statement unreadable.
    """
    out: List[Tuple[str, str]] = []
    for match in _TOKEN.finditer(text):
        kind = match.lastgroup
        if kind == "tag":  # the inner group of a dollar-quoted string
            kind = "dollar"
        value = match.group()
        if kind in ("space", "comment"):
            continue
        if kind == "other":
            return None
        if kind == "word":
            value = value.upper()
        out.append((kind, value))  # type: ignore[arg-type]
    return out


def _name(token: Tuple[str, str]) -> Optional[str]:
    """A quoted identifier's name, case-folded; ``None`` if not one we can trust."""
    kind, value = token
    if kind not in ("qid", "bid"):
        return None
    inner = value[1:-1]
    if not inner or '"' in inner or "`" in inner:
        return None  # an escaped quote
    return inner.lower()


def _text(sql: Any) -> Optional[str]:
    """The statement as text, when that is certain: ``str``, ``bytes``, or a psycopg
    ``Composed`` made only of ``SQL`` and one-part ``Identifier`` pieces."""
    if isinstance(sql, str):
        return sql
    if isinstance(sql, (bytes, bytearray, memoryview)):
        try:
            return bytes(sql).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return _render_composable(sql, 0)


def _render_composable(part: Any, depth: int) -> Optional[str]:
    if depth > 8:
        return None
    kind = type(part).__name__
    if kind == "SQL":
        value = getattr(part, "string", None)  # psycopg2
        if value is None:
            value = getattr(part, "_obj", None)  # psycopg 3
        return value if isinstance(value, str) else None
    if kind == "Identifier":
        names = getattr(part, "strings", None)  # psycopg2
        if names is None:
            names = getattr(part, "_obj", None)  # psycopg 3
        if not isinstance(names, tuple) or not names:
            return None
        if any(not isinstance(n, str) or '"' in n for n in names):
            return None
        return ".".join('"%s"' % n for n in names)
    if kind == "Composed":
        parts = getattr(part, "seq", None)  # psycopg2
        if parts is None:
            parts = getattr(part, "_obj", None)  # psycopg 3
        if not isinstance(parts, (list, tuple)):
            return None
        rendered = [_render_composable(p, depth + 1) for p in parts]
        if any(r is None for r in rendered):
            return None  # a Literal or Placeholder: not certain
        return "".join(rendered)  # type: ignore[arg-type]
    return None


def _skip_group(toks: List[Tuple[str, str]], i: int) -> int:
    """``toks[i]`` is ``(``: the index after its matching ``)``, or -1."""
    depth = 0
    while i < len(toks):
        value = toks[i][1]
        if toks[i][0] == "punct":
            if value == "(":
                depth += 1
            elif value == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return -1


def _tail_ok(toks: List[Tuple[str, str]], i: int) -> bool:
    """The tail of a write: no :data:`_WRITE_TAIL_BANNED` word outside parentheses,
    and no other write (INSERT, DELETE, MERGE, TRUNCATE, WITH, INTO, or UPDATE
    inside parentheses) at any depth."""
    depth = 0
    for kind, value in toks[i:]:
        if kind == "punct":
            if value == "(":
                depth += 1
            elif value == ")":
                depth -= 1
                if depth < 0:
                    return False
        elif kind == "word":
            if depth == 0 and value in _WRITE_TAIL_BANNED:
                return False
            if value in _WRITE_WORDS or value in ("WITH", "INTO"):
                return False
            if depth > 0 and value == "UPDATE":
                return False
    return depth == 0


def _write_table(toks: List[Tuple[str, str]]) -> Optional[str]:
    """The table of a single-table ORM write, or ``None`` (UNKNOWN). The grammar::

        INSERT [OR IGNORE | IGNORE] INTO <table> [ ( ...columns... ) ]
               ( VALUES ... | DEFAULT VALUES | SELECT * FROM UNNEST ( ... ) ) <tail>
        UPDATE <table> SET <tail>
        DELETE FROM <table> [ WHERE ... | RETURNING ... ]

    ``<table>`` is ONE identifier in double quotes or backticks, with no
    escaped quote and no schema part, and no alias. ``<tail>`` may not contain,
    outside parentheses, FROM, USING, JOIN, INTO, SELECT or WITH, nor at any
    depth INSERT, DELETE, MERGE, TRUNCATE, WITH or INTO, nor UPDATE inside
    parentheses (:func:`_tail_ok`).
    """
    if not toks or toks[0][0] != "word":
        return None
    verb = toks[0][1]
    if verb == "INSERT":
        # ``ignore_conflicts``: SQLite's ``INSERT OR IGNORE INTO``, MySQL's
        # ``INSERT IGNORE INTO`` (PostgreSQL's is a tail: ``ON CONFLICT DO NOTHING``).
        i = 1
        if toks[1:3] == [("word", "OR"), ("word", "IGNORE")]:
            i = 3
        elif toks[1:2] == [("word", "IGNORE")]:
            i = 2
        if len(toks) < i + 2 or toks[i] != ("word", "INTO"):
            return None
        table = _name(toks[i + 1])
        i += 2
        if table is None:
            return None  # a schema prefix leaves "." where the grammar wants "(" or VALUES
        if i < len(toks) and toks[i] == ("punct", "("):
            i = _skip_group(toks, i)
            if i < 0:
                return None
        rest = [value for _, value in toks[i : i + 6]]
        if rest[:1] == ["VALUES"]:
            i += 1
        elif rest[:2] == ["DEFAULT", "VALUES"]:
            i += 2
        elif rest[:5] == ["SELECT", "*", "FROM", "UNNEST", "("]:
            i = _skip_group(toks, i + 4)
            if i < 0:
                return None
        else:
            return None
        return table if _tail_ok(toks, i) else None
    if verb == "UPDATE":
        if len(toks) < 3:
            return None
        table = _name(toks[1])
        if table is None or toks[2] != ("word", "SET"):
            return None
        return table if _tail_ok(toks, 3) else None
    if verb == "DELETE":
        if len(toks) < 3 or toks[1] != ("word", "FROM"):
            return None
        table = _name(toks[2])
        if table is None:
            return None
        if len(toks) > 3 and toks[3] not in (("word", "WHERE"), ("word", "RETURNING")):
            return None
        return table if _tail_ok(toks, 3) else None
    return None


_WRITE = object()  # what the read reader returns for a statement that writes


def _read_tables(toks: List[Tuple[str, str]], ctes: FrozenSet[str] = frozenset()) -> Any:
    """The tables a ``SELECT`` names after FROM / JOIN: a frozenset, ``None``
    (UNKNOWN) or :data:`_WRITE` (it contains a write).

    Every ``FROM`` and ``JOIN``, at any depth, must be followed either by a
    subquery ``( SELECT ... )`` (its own FROMs are read as the scan goes on) or
    by ONE quoted identifier with no schema part (or the name of one of
    ``ctes``), an optional alias, and then a comma (another such table), a
    closing parenthesis, the end, or one of :data:`_AFTER_TABLE`. A ``FROM``
    inside ``EXTRACT(...)`` and friends, and ``IS [NOT] DISTINCT FROM``, name no
    table. Anything else is UNKNOWN: a bare name, ``ONLY``, a function in FROM,
    ``LATERAL``, ``TABLESAMPLE``, a parenthesised join group ``( "a" JOIN "b"
    ...)``, the ``TABLE`` shorthand, ``SELECT ... INTO``, a nested ``WITH``.
    INSERT, DELETE, MERGE or TRUNCATE anywhere, or an UPDATE not part of
    ``FOR [NO KEY] UPDATE``, is a write.
    """
    tables: Set[str] = set()
    openers: List[str] = []
    n = len(toks)
    i = 0

    def word_at(j: int) -> str:
        return toks[j][1] if 0 <= j < n and toks[j][0] == "word" else ""

    for j, (kind, value) in enumerate(toks):
        if kind == "word" and (
            value in _WRITE_WORDS or (value == "UPDATE" and word_at(j - 1) not in ("FOR", "KEY"))
        ):
            return _WRITE
    while i < n:
        kind, value = toks[i]
        if kind == "punct":
            if value == "(":
                openers.append(word_at(i - 1))
            elif value == ")" and openers:
                openers.pop()
            i += 1
            continue
        if kind != "word":
            i += 1
            continue
        if value in ("INTO", "TABLE", "WITH"):
            return None
        if value not in ("FROM", "JOIN"):
            i += 1
            continue
        if value == "FROM" and (
            (openers and openers[-1] in _FROM_IN_ARGUMENTS)
            or (word_at(i - 1) == "DISTINCT" and word_at(i - 2) in ("IS", "NOT"))
        ):
            i += 1
            continue
        i += 1
        while True:
            if i >= n:
                return None
            if toks[i] == ("punct", "("):
                j = i
                while j < n and toks[j] == ("punct", "("):
                    j += 1
                if word_at(j) != "SELECT":
                    return None  # a parenthesised join group or anything else
                break  # a subquery: read on
            token = toks[i]
            if token[0] == "word" and token[1].lower() in ctes:
                table = None  # a CTE of this statement
            else:
                table = _name(token)
                if table is None:
                    return None
                if table in ctes:
                    table = None
            i += 1
            if table is not None:
                tables.add(table)  # a schema prefix leaves "." next: UNKNOWN below
            if i < n and toks[i] == ("word", "AS"):
                i += 1
                if i >= n or toks[i][0] not in ("word", "qid", "bid"):
                    return None
                i += 1
            elif i < n and (
                toks[i][0] in ("qid", "bid")
                or (toks[i][0] == "word" and toks[i][1] not in _AFTER_TABLE)
            ):
                i += 1  # an alias
            if i >= n or toks[i] == ("punct", ")"):
                break
            if toks[i] == ("punct", ","):
                i += 1
                continue
            if toks[i][0] == "word" and toks[i][1] in _AFTER_TABLE:
                break
            return None
    return frozenset(tables)


def _starts_select(toks: List[Tuple[str, str]]) -> bool:
    j = 0
    while j < len(toks) and toks[j] == ("punct", "("):
        j += 1
    return j < len(toks) and toks[j] == ("word", "SELECT")


def _classify_with(toks: List[Tuple[str, str]]) -> Tuple[str, Optional[FrozenSet[str]]]:
    """``WITH [RECURSIVE] name [(cols)] AS [[NOT] MATERIALIZED] ( SELECT ... ) [, ...]
    SELECT ...``: a read when every part is a SELECT (each read through
    :func:`_read_tables`, the CTE names not counted as tables); any other part
    makes it a write to UNKNOWN tables."""
    n = len(toks)
    i = 1
    if i < n and toks[i] == ("word", "RECURSIVE"):
        i += 1
    ctes: Set[str] = set()
    parts: List[List[Tuple[str, str]]] = []
    while True:
        if i >= n:
            return "write", None
        kind, value = toks[i]
        name = value.lower() if kind == "word" else _name(toks[i])
        if not name:
            return "write", None
        ctes.add(name)
        i += 1
        if i < n and toks[i] == ("punct", "("):
            i = _skip_group(toks, i)
            if i < 0:
                return "write", None
        if i >= n or toks[i] != ("word", "AS"):
            return "write", None
        i += 1
        if i < n and toks[i] == ("word", "NOT"):
            i += 1
        if i < n and toks[i] == ("word", "MATERIALIZED"):
            i += 1
        if i >= n or toks[i] != ("punct", "("):
            return "write", None
        end = _skip_group(toks, i)
        if end < 0:
            return "write", None
        parts.append(toks[i + 1 : end - 1])
        i = end
        if i < n and toks[i] == ("punct", ","):
            i += 1
            continue
        break
    parts.append(toks[i:])
    tables: Set[str] = set()
    unknown = False
    for part in parts:
        if not _starts_select(part):
            return "write", None
        found = _read_tables(part, frozenset(ctes))
        if found is _WRITE:
            return "write", None
        if found is None:
            unknown = True
        else:
            tables |= found
    return "read", None if unknown else frozenset(tables)


def _classify(text: str) -> Tuple[str, Optional[FrozenSet[str]]]:
    toks = _tokens(text)
    if not toks:
        return "write", None
    for index, token in enumerate(toks):
        if token == ("punct", ";") and index != len(toks) - 1:
            return "write", None  # more than one statement
    if toks[-1] == ("punct", ";"):
        toks = toks[:-1]
    if not toks:
        return "write", None
    if toks[0] == ("word", "WITH"):
        return _classify_with(toks)
    if _starts_select(toks):
        tables = _read_tables(toks)
        if tables is _WRITE or (tables is None and any(t == ("word", "INTO") for t in toks)):
            return "write", None  # SELECT ... INTO creates a table
        return "read", tables
    if toks[0] == ("punct", "("):
        return "write", None
    first = toks[0]
    if first[0] == "word" and first[1] in _NO_ROWS:
        if first[1] == "EXPLAIN" and len(toks) > 1 and toks[1][1] in ("ANALYZE", "ANALYSE", "("):
            return "write", None  # EXPLAIN ANALYZE runs the statement
        return "other", frozenset()
    table = _write_table(toks)
    return "write", None if table is None else frozenset({table})


@functools.lru_cache(maxsize=4096)
def _classify_cached(text: str) -> Tuple[str, Optional[FrozenSet[str]]]:
    return _classify(text)


def statement_tables(sql: Any) -> Tuple[str, Optional[FrozenSet[str]]]:
    """``(kind, tables)`` of a statement: ``"read"``, ``"write"`` or ``"other"``.

    ``tables`` is ``None`` when they are UNKNOWN. A statement that is not a
    recognised read or a recognised no-row statement is a write. See
    :func:`_write_table` and :func:`_read_tables` for the two grammars.
    """
    text = _text(sql)
    if text is None:
        return "write", None
    if len(text) > 20_000:
        return _classify(text)
    return _classify_cached(text)


@functools.lru_cache(maxsize=1)
def ignored_tables() -> FrozenSet[str]:
    """Tables whose writes never refresh held rows: Django's and djust's bookkeeping.

    ``django_session`` (the session, and djust's default state store), every
    ``DatabaseCache`` table in ``CACHES``, and ``DJUST_IGNORED_WRITE_TABLES``.
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
    """Whether ``sql`` is a write that counts (not one only to :func:`ignored_tables`)."""
    kind, tables = statement_tables(sql)
    if kind != "write":
        return False
    return tables is None or bool(tables - ignored_tables())


def _any_write_refreshes() -> bool:
    from django.conf import settings

    return bool(getattr(settings, ANY_WRITE_SETTING, False))


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------


def _recorder(execute: Callable[..., Any], sql: Any, params: Any, many: Any, context: Any) -> Any:
    writer = _writer.get()
    if writer is None:
        return execute(sql, params, many, context)
    result = execute(sql, params, many, context)
    view = writer.ref()
    if view is None:
        return result
    kind, tables = statement_tables(sql)
    if kind == "read":
        note_reads(view, tables)
    elif kind == "write":
        if writer.writes:
            _note_write(writer, view, tables)
        elif tables is None:
            # In a render: a statement the readers could not follow (``WITH``,
            # ``TABLE t``, several statements) may have read anything.
            note_reads(view, None)
    return result


def _note_write(writer: _Writer, view: Any, tables: Optional[FrozenSet[str]]) -> None:
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
    mark_tree(view, tables)


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

    install_fill_stamps()
    connection_created.connect(install, dispatch_uid="djust_held_rows_recorder")
    setting_changed.connect(_settings_changed, dispatch_uid="djust_held_rows_settings")
    for connection in connections.all(initialized_only=True):
        install(connection)


# ---------------------------------------------------------------------------
# Attributing a step or a render to its view
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def writes_by(view: Any, *, writes: bool = True) -> Iterator[None]:
    """Attribute the statements run inside the block (and the threads and tasks
    it starts) to ``view``. ``writes=False``: its reads count, its writes do not."""
    if view is None:
        yield
        return
    before = _filled_properties(view)
    token = _writer.set(_Writer(view, writes))
    try:
        yield
    finally:
        _writer.reset(token)
        _properties_filled(view, before)


def attributed(view: Any, fn: Callable[..., _T], *, writes: bool = True) -> Callable[..., _T]:
    """``fn`` as a callable whose statements are attributed to ``view`` where it runs."""
    if view is None:
        return fn

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> _T:
        with writes_by(view, writes=writes):
            return fn(*args, **kwargs)

    return run


@contextlib.contextmanager
def reading(view: Any) -> Iterator[None]:
    """A render of ``view``: its reads count, its writes do not. Inside a step or a
    render of the same view already, that attribution stays."""
    current = _writer.get()
    if view is None or (current is not None and current.ref() is view):
        yield
        return
    with writes_by(view, writes=False):
        yield


def recorded_render(method: Callable[..., _T]) -> Callable[..., _T]:
    """Decorate a view's render method: its reads enter the view's read set, and a
    render that returns completes the set (see the module docstring)."""

    @functools.wraps(method)
    def render(self: Any, *args: Any, **kwargs: Any) -> _T:
        current = _writer.get()
        if current is not None and current.ref() is self:
            return method(self, *args, **kwargs)  # nested: the outer render records
        with writes_by(self, writes=False):
            result = method(self, *args, **kwargs)
        render_completed(self)
        return result

    return render


# ---------------------------------------------------------------------------
# Read sets
# ---------------------------------------------------------------------------


def _forget(registry: Dict[int, Any], key: int) -> Callable[[Any], None]:
    """A weakref callback dropping ``registry[key]`` once its view is collected.

    It takes no lock: the garbage collector runs it at any allocation, also in a
    thread that already holds ``_lock``. It drops the entry only if it still
    holds the collected reference (a new view may have the same ``id()``).
    """

    def callback(ref: Any) -> None:
        entry = registry.get(key)
        held = getattr(entry, "ref", None) if not isinstance(entry, list) else entry[0]
        if entry is not None and held is ref:
            registry.pop(key, None)

    return callback


def _reads_entry(view: Any) -> Optional[_Reads]:
    """``view``'s read set, made under ``_lock`` if missing."""
    entry = _read_sets.get(id(view))
    if entry is not None and entry.ref() is view:
        return entry
    try:
        ref = weakref.ref(view, _forget(_read_sets, id(view)))
    except TypeError:
        return None
    entry = _Reads(ref)
    _read_sets[id(view)] = entry
    return entry


def note_reads(view: Any, tables: Optional[FrozenSet[str]]) -> None:
    """Add what a read named to ``view``'s read set (``None``: UNKNOWN).

    Under ``_lock``: on a free-threaded build two threads of one view (a
    handler's ``sync_to_async(thread_sensitive=False)`` and the render) may add
    at once, and the overflow reset must not interleave with an update.
    """
    with _lock:
        entry = _reads_entry(view)
        if entry is None or entry.unknown:
            return
        if tables is None or len(entry.tables) + len(tables - entry.tables) > _READ_SET_LIMIT:
            entry.unknown = True
            entry.tables = set()
            return
        entry.tables |= tables


def reads_unknown(view: Any) -> None:
    """What ``view`` holds came from elsewhere (a restore): R is UNKNOWN until its next render."""
    with _lock:
        entry = _read_sets.get(id(view))
        if entry is not None and entry.ref() is view:
            entry.complete = False
            entry.known = frozenset()


def render_completed(view: Any) -> None:
    """A render of ``view`` finished under recording: R is complete."""
    with _lock:
        entry = _reads_entry(view)
        if entry is not None:
            entry.complete = True


def _filled_properties(view: Any) -> FrozenSet[str]:
    """The cached properties ``view`` holds a value for now (O(its cached properties))."""
    names = _cached_property_names(type(view))
    if not names:
        return frozenset()
    state = getattr(view, "__dict__", None)
    if not isinstance(state, dict):
        return frozenset()
    return frozenset(name for name in names if name in state)


def _properties_filled(view: Any, before: FrozenSet[str]) -> None:
    """After a recorded step or render: the cached properties computed inside it are
    known to have been read with recording on; one dropped since is forgotten."""
    now = _filled_properties(view)
    if not now and not before:
        return
    with _lock:
        entry = _reads_entry(view)
        if entry is not None:
            # Filled inside: new since the step began, or emptied by a reset in
            # it (a reset inside a render: the render refilled it).
            filled = (now - before) | (now & entry.dropped)
            entry.known = filled | (now & entry.known)
            entry.dropped = frozenset()


def _stamp_fill(queryset: Any) -> None:
    """A QuerySet just filled its result cache: record which view's recorded step or
    render it was filled in (``0``: none), so held rows filled while nothing
    recorded are found without walking the view on every render."""
    writer = _writer.get()
    view = writer.ref() if writer is not None else None
    queryset.__dict__["_djust_filled_by"] = id(view) if view is not None else 0


def install_fill_stamps() -> None:
    """Wrap ``QuerySet._fetch_all`` once to call :func:`_stamp_fill` when it fills."""
    from django.db.models.query import QuerySet

    original = QuerySet._fetch_all
    if getattr(original, "_djust_held_rows", False):
        return

    @functools.wraps(original)
    def _fetch_all(self: Any) -> None:  # noqa: dead-method-allowed (installed on QuerySet)
        filling = self._result_cache is None
        original(self)
        if filling:
            _stamp_fill(self)

    _fetch_all._djust_held_rows = True  # type: ignore[attr-defined]
    QuerySet._fetch_all = _fetch_all  # type: ignore[method-assign]


def _unrecorded(view: Any, tree_ids: FrozenSet[int], known: FrozenSet[str]) -> bool:
    """Whether ``view`` holds a cached value or evaluated rows filled while nothing
    recorded: a cached property not in ``known``, or a QuerySet whose fill stamp
    is not a view of this tree."""
    if _filled_properties(view) - frozen_names(view) - known:
        return True
    for queryset in _held_querysets(view):
        if queryset.__dict__.get("_djust_filled_by", 0) not in tree_ids:
            return True
    return False


def _view_reads(view: Any, tree_ids: FrozenSet[int]) -> Tuple[FrozenSet[str], bool]:
    """``(tables, unknown)`` for one node of the tree."""
    entry = _read_sets.get(id(view))
    if entry is None or entry.ref() is not view:
        # Nothing it read was recorded: known (and empty) only if it holds
        # nothing from the database and is not a view still to render.
        return frozenset(), _renders_itself(view) or _unrecorded(view, tree_ids, frozenset())
    with _lock:
        tables, unknown, complete, known = (
            frozenset(entry.tables),
            entry.unknown,
            entry.complete,
            entry.known,
        )
    if unknown or (_renders_itself(view) and not complete):
        return frozenset(), True
    if _unrecorded(view, tree_ids, known):
        return frozenset(), True
    return tables, False


def _renders_itself(view: Any) -> bool:
    """A view (not a component): its own render completes its read set."""
    return callable(getattr(type(view), "render_with_diff", None))


def read_set(view: Any) -> Tuple[FrozenSet[str], bool]:
    """``(tables, unknown)`` read by ``view`` and the views it shares a page with."""
    tables: Set[str] = set()
    nodes = _tree(view)
    tree_ids = frozenset(id(node) for node in nodes)
    for node in nodes:
        node_tables, unknown = _view_reads(node, tree_ids)
        if unknown:
            return frozenset(), True
        tables |= node_tables
    return frozenset(tables), False


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------


def _mark(view: Any, tables: Optional[FrozenSet[str]]) -> None:
    key = id(view)
    with _lock:
        _generation[0] += 1
        generation = _generation[0]
        entry = _marked.get(key)
        if entry is not None and entry[0]() is view:
            if entry[1] is not None:
                entry[1] = None if tables is None else entry[1] | set(tables)
            entry[2] = generation
            return
        try:
            ref: Any = weakref.ref(view, _forget(_marked, key))
        except TypeError:
            return  # cannot be tracked without keeping it alive
        _marked[key] = [ref, None if tables is None else set(tables), generation]


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
    """Mark ``view`` and its tree as written: ``tables``, or UNKNOWN (``None``)."""
    for node in _tree(view):
        _mark(node, tables)


def named_tables(targets: Any) -> Optional[FrozenSet[str]]:
    """``rows_changed()``'s arguments as tables: models or names; none is UNKNOWN."""
    if not targets:
        return None
    tables: Set[str] = set()
    for target in targets:
        if isinstance(target, str):
            tables.add(target.lower())
        elif hasattr(target, "_meta"):
            meta = target._meta
            tables.add(meta.db_table.lower())
            tables.update(parent._meta.db_table.lower() for parent in meta.get_parent_list())
        else:
            raise TypeError("rows_changed() takes model classes or table names")
    return frozenset(tables)


def is_marked(view: Any) -> bool:
    """Whether ``view`` has a write it has not rendered since."""
    return view is not None and id(view) in _marked


def mark_generation(view: Any) -> int:
    """The generation of ``view``'s mark now (0 if none): see :func:`unmark`."""
    entry = _marked.get(id(view))
    return 0 if entry is None else entry[2]


def written_tables(view: Any) -> Optional[FrozenSet[str]]:
    """The tables written since ``view`` last rendered; ``None`` for UNKNOWN."""
    entry = _marked.get(id(view))
    if entry is None or entry[1] is None:
        return None
    return frozenset(entry[1])


def unmark(view: Any, generation: Optional[int] = None) -> None:
    """A render of ``view`` succeeded. With ``generation`` (taken when it started),
    only a mark no newer than that is cleared: one made during the render stays."""
    with _lock:
        entry = _marked.get(id(view))
        if entry is None:
            return
        if generation is None or entry[2] <= generation:
            _marked.pop(id(view), None)


def _refresh_needed(view: Any) -> bool:
    """The decision of the module docstring, for a marked view."""
    if _any_write_refreshes():
        fallback_counts["DJUST_REFRESH_ON_ANY_WRITE"] += 1
        return True
    written = written_tables(view)
    if written is None:
        _fallback("a write whose table is UNKNOWN", view)
        return True
    reads, unknown = read_set(view)
    if unknown:
        _fallback("a read set that is UNKNOWN", view)
        return True
    return bool(written & reads)


def _fallback(reason: str, view: Any) -> None:
    fallback_counts[reason] += 1
    logger.debug(
        "djust: %s; %s.%s re-reads everything it holds (#4112)",
        reason,
        type(view).__module__,
        type(view).__qualname__,
    )


def rows_written(view: Any) -> bool:
    """Whether the turn must render although no assign changed.

    True when a write may have changed something the view shows (the decision
    of the module docstring). Otherwise the mark is forgotten and the turn
    stays a ``noop``. An explicit ``skip_render()`` still wins (the mark then
    waits for the next render).
    """
    if not is_marked(view):
        return False
    if _refresh_needed(view):
        return True
    unmark(view)
    return False


def refresh_for_render(view: Any) -> None:
    """Before a render of a marked view: empty everything it holds, or nothing."""
    if _refresh_needed(view):
        reset_held_rows(view)


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


def _held_querysets(view: Any) -> Iterator[Any]:
    """The evaluated QuerySets ``view`` holds: in the attributes its code set, and
    in the dicts, lists, tuples and sets they hold, to :data:`_DEPTH_LIMIT`
    levels. Every item is looked at (no shortcut and no limit): this runs only
    on a turn that wrote, to decide and to reset."""
    from django.db.models import QuerySet

    state = getattr(view, "__dict__", None)
    if not isinstance(state, dict):
        return
    # The framework's own attributes (set before mount(), ``_framework_attrs``)
    # hold no application rows.
    skip = frozen_names(view) | _framework_names(view)
    pending = [(value, 0) for name, value in list(state.items()) if name not in skip]
    containers = (dict, list, tuple, set, frozenset)
    while pending:
        value, depth = pending.pop()
        if isinstance(value, QuerySet):
            if _evaluated(value):
                yield value
        elif depth < _DEPTH_LIMIT and isinstance(value, containers):
            items = value.values() if isinstance(value, dict) else value
            pending.extend((item, depth + 1) for item in items)


def _framework_names(view: Any) -> FrozenSet[str]:
    names = getattr(view, "__dict__", {}).get("_framework_attrs")
    return names if isinstance(names, frozenset) else frozenset()


_MISSING = object()


def _empty(queryset: Any) -> None:
    queryset._result_cache = None
    queryset._prefetch_done = False


def reset_held_rows(view: Any) -> None:
    """Empty every QuerySet and cached property ``view`` holds (minus ``frozen_rows``)."""
    state = getattr(view, "__dict__", None)
    if not isinstance(state, dict):
        return
    dropped = set()
    for name in _cached_property_names(type(view)) - frozen_names(view):
        if state.pop(name, _MISSING) is not _MISSING:
            dropped.add(name)
    if dropped:
        with _lock:
            entry = _reads_entry(view)
            if entry is not None:
                # Refilled inside this render: known again at its end
                # (:func:`_properties_filled`); not refilled: gone from ``now``.
                entry.dropped = entry.dropped | dropped
    for queryset in list(_held_querysets(view)):
        _empty(queryset)
