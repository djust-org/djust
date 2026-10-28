"""Which ``dj-update`` containers hold rows from a ``temporary_assigns`` key (#3639, #3647).

A view that declares ``temporary_assigns`` renders only the rows it just
assigned, so a ``dj-update="append"`` / ``"prepend"`` container that shows them
has to ACCUMULATE in the differ: the new rows join the stored ones instead of
replacing them. A container fed by an ordinary attribute is re-rendered whole
every time, and the template stays authoritative there (a row it no longer lists
leaves the page). The differ cannot tell the two apart from the markup, so this
module reads the template source: a container accumulates when its own body names
a temporary key (``{{ rows }}``, ``{% for r in rows %}``) or a stream
(``streams``, which ``temporary_assigns`` empties after every render too).

What counts as "names a temporary key":

* **Names the key reaches the container under.** The key itself, a
  ``{% with items=rows %}`` / ``{% with rows as items %}`` alias in force at the
  container (the ``with`` may wrap it from outside), and a context key the
  caller found to hold the very same object (``get_context_data`` returning
  ``{"feed": self.rows}``, see :func:`view_accumulating_container_ids`).
* **The container's own body.** A nested ``dj-update`` container's body belongs
  to that container: it is not part of its parent's.
* **An ``{% include %}`` is read.** A static name is resolved the way the
  renderer resolves it (the template dirs, then Django's loaders) and its source
  scanned, recursively; ``only`` hides the view context from the included file, so
  only the ``with`` clause counts then. A dynamic name, a name that cannot be
  resolved, a cycle, a chain deeper than :data:`MAX_INCLUDE_DEPTH`, an
  ``{% extends %}`` or too many files are NOT seen through: the include is
  assumed to read a temporary key, which is how #3645 treated every include.

Anything else the scan cannot see (a template tag that reads the view by itself,
a context value derived from the attribute such as ``list(self.rows)``) leaves
the container re-rendered whole. The scan never raises and is bounded in time:
a source over :data:`MAX_SOURCE_CHARS`, or one that outlasts
:data:`SCAN_BUDGET_SECONDS`, accumulates nothing (the pre-#3645 behaviour) and
says so in the log.

The container needs a static ``id`` on its tag: the differ finds it by that
``id``, and the client adds a child only by ``id`` as well. A container whose
``id`` is itself a template expression is left alone.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
import time
from bisect import bisect_left, bisect_right
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: A page (or included file) larger than this many characters is not scanned.
MAX_SOURCE_CHARS = 1_000_000
#: ``{% include %}`` nesting followed before the include is assumed to read a key.
MAX_INCLUDE_DEPTH = 8
#: Included files read by one scan before the rest are assumed to read a key.
MAX_INCLUDED_TEMPLATES = 64
#: Open HTML elements tracked at once; deeper than this is not real markup.
MAX_ELEMENT_DEPTH = 1000
#: ``{% with %}`` aliases of a temporary key tracked at one point of a template.
MAX_ALIASES = 64
#: Wall-clock budget of one scan.
SCAN_BUDGET_SECONDS = 2.0

_CHUNK = 65536
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_ACCUMULATING = ("append", "prepend")
#: Inside a template tag these would end or split an HTML tag; blank them out
#: (same length, so every offset stays valid) before the HTML parser sees them.
_MASK = str.maketrans({"<": "_", ">": "_", '"': "_", "'": "_"})
_TAG_NAME = re.compile(r"\{%\s*(\w+)")
_TOKEN = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s"'])+""")
_KWARG = re.compile(r"^(\w+)=(.+)$", re.S)
_INCLUDE = re.compile(
    r"""\{%\s*include\s+(?P<name>"[^"]*"|'[^']*'|\S+)(?P<rest>.*?)\s*%\}\Z""", re.S
)


def _template_spans(source: str) -> List[Tuple[int, int]]:
    """``(start, end)`` of every ``{{ ... }}`` / ``{% ... %}`` in ``source``.

    The same tags a lazy ``\\{\\{.*?\\}\\}|\\{%.*?%\\}`` finds, in one pass. That
    regex is quadratic on input with many unterminated ``{{``: each one scans to
    the end of the text before giving up. Here the position of the next opener
    and closer of each kind is searched once and reused until it is passed, and a
    closer that does not exist is known not to exist for good.
    """
    spans: List[Tuple[int, int]] = []
    n = len(source)
    find = source.find
    i = 0
    open_var = open_tag = close_var = close_tag = -2  # -2: not searched yet
    while i < n:
        if open_var != -1 and open_var < i:
            open_var = find("{{", i)
        if open_tag != -1 and open_tag < i:
            open_tag = find("{%", i)
        starts = [p for p in (open_var, open_tag) if p != -1]
        if not starts:
            break
        start = min(starts)
        if start == open_var:
            if close_var != -1 and close_var < start + 2:
                close_var = find("}}", start + 2)
            end = close_var
            width = 2
        else:
            if close_tag != -1 and close_tag < start + 2:
                close_tag = find("%}", start + 2)
            end = close_tag
            width = 2
        if end == -1:
            # No closer anywhere ahead: this opener is plain text. A later opener
            # of the OTHER kind may still close, so step by one character.
            i = start + 1
            continue
        spans.append((start, end + width))
        i = end + width
    return spans


def _mask(source: str, spans: List[Tuple[int, int]]) -> str:
    """``source`` with ``< > " '`` blanked out inside template tags, same length."""
    if not spans:
        return source
    parts: List[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(source[cursor:start])
        parts.append(source[start:end].translate(_MASK))
        cursor = end
    parts.append(source[cursor:])
    return "".join(parts)


class _Container:
    __slots__ = ("cid", "marked", "open_start", "body_start", "body_end", "nested")

    def __init__(self, cid: Optional[str], marked: bool, open_start: int, body_start: int) -> None:
        self.cid = cid
        self.marked = marked
        self.open_start = open_start
        self.body_start = body_start
        self.body_end = body_start
        #: ``(start, end)`` of the ``dj-update`` elements closed inside this one
        self.nested: List[Tuple[int, int]] = []


class _ScanTimeout(Exception):
    """The scan ran out of its time budget."""


class _ScanTooDeep(Exception):
    """More than :data:`MAX_ELEMENT_DEPTH` elements open at once."""


class _Scanner(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        # Only "\n" counts as a line break for ``getpos``; ``str.splitlines`` also
        # splits on form feeds and U+2028 and would shift every offset after one.
        self._line_starts = [0]
        position = text.find("\n")
        while position != -1:
            self._line_starts.append(position + 1)
            position = text.find("\n", position + 1)
        self._open: List[Tuple[str, _Container]] = []
        #: how many open elements carry each tag name, so an end tag that matches
        #: none is rejected without walking the whole stack
        self._open_count: Dict[str, int] = {}
        #: every ``dj-update="append|prepend"`` element, in closing order
        self.containers: List[_Container] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _VOID:
            return
        found = dict(attrs)
        marked = found.get("dj-update") in _ACCUMULATING
        cid = found.get("id")
        static = bool(cid) and "{" not in cid
        if len(self._open) >= MAX_ELEMENT_DEPTH:
            raise _ScanTooDeep
        start = self._offset()
        body_start = start + len(self.get_starttag_text() or "")
        self._open.append((tag, _Container(cid if static else None, marked, start, body_start)))
        self._open_count[tag] = self._open_count.get(tag, 0) + 1

    def handle_endtag(self, tag: str) -> None:
        if not self._open_count.get(tag):
            return
        end = self._offset()
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] != tag:
                continue
            # Elements still open above the match were closed implicitly by this tag.
            closing = [element for _tag, element in self._open[index:]]
            for name, _element in self._open[index:]:
                self._open_count[name] -= 1
            del self._open[index:]
            for position in range(len(closing) - 1, -1, -1):
                element = closing[position]
                element.body_end = end
                if position == 0 and element.marked:
                    self.containers.append(element)
                if position > 0:
                    parent: Optional[_Container] = closing[position - 1]
                else:
                    parent = self._open[-1][1] if self._open else None
                if parent is None:
                    continue
                if element.marked:
                    parent.nested.append((element.open_start, end))
                else:
                    parent.nested.extend(element.nested)
            return


def _tag_word(text: str) -> str:
    """``include`` for ``{% include ... %}``; "" for a variable or anything else."""
    match = _TAG_NAME.match(text) if text.startswith("{%") else None
    return match.group(1) if match else ""


def _names_pattern(names: Iterable[str]) -> "re.Pattern[str]":
    return _compiled_names(tuple(sorted(set(names))))


@lru_cache(maxsize=64)
def _compiled_names(names: Tuple[str, ...]) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w.])(?:%s)\b" % "|".join(re.escape(name) for name in names))


class _Budget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + SCAN_BUDGET_SECONDS
        self.included = 0

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise _ScanTimeout


def _resolve_include(name: str, origin: Optional[str]) -> Optional[Tuple[str, str]]:
    """``(canonical name, source)`` of the template an ``{% include name %}`` renders.

    The renderer reads the template dirs in order (``get_template_dirs``); Django's
    own loaders are the fallback for a template only they can find. ``./`` and
    ``../`` names are relative to the including template's NAME, as in Django. None
    when it cannot be found, is unreadable, or escapes the template dirs.
    """
    if not name or "\x00" in name or "\\" in name:
        return None
    if name.startswith(("./", "../")):
        if not origin:
            return None
        name = posixpath.join(posixpath.dirname(origin), name)
    name = posixpath.normpath(name)
    if name.startswith("/") or name in (".", "..") or name.startswith("../"):
        return None
    try:
        from .utils import get_template_dirs

        for directory in get_template_dirs():
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                if os.path.getsize(path) > MAX_SOURCE_CHARS * 4:
                    return None
                with open(path, encoding="utf-8") as handle:
                    return name, handle.read()
        from django.template import loader

        source = getattr(getattr(loader.get_template(name), "template", None), "source", None)
        return (name, source) if isinstance(source, str) else None
    except Exception as exc:  # noqa: BLE001 — a template we cannot read is one we cannot see through
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(
            logger,
            (),
            exc,
            "dj-update scan: could not read included template %s",
            name,
            level="debug",
        )
        return None


class _Scan:
    """One pass over a page: the tags, the ``with`` aliases and the containers."""

    def __init__(self, source: str, keys: Tuple[str, ...], origin: Optional[str]) -> None:
        self.source = source
        self.keys = tuple(sorted(set(keys) | {"streams"}))
        self.origin = origin
        self.budget = _Budget()
        self.spans = _template_spans(source)
        #: included file reads, by (name, key pattern); None while being scanned
        self._memo: Dict[Tuple[str, str], Optional[bool]] = {}

    # -- {% with %} aliases ---------------------------------------------------

    def _alias_states(self) -> Tuple[List[int], List[FrozenSet[str]]]:
        """The ``{% with %}`` aliases of a temporary key in force at each offset.

        Returns ``(offsets, states)``: from ``offsets[i]`` until ``offsets[i + 1]``
        the aliases in force are ``states[i]``, those of the enclosing ``with``
        tags included (``{% with b=a %}`` inside ``{% with a=rows %}`` makes ``b``
        one too). Use :meth:`_aliases_at`.
        """
        offsets: List[int] = [-1]
        states: List[FrozenSet[str]] = [frozenset()]
        #: aliases in force inside each open ``with``
        stack: List[FrozenSet[str]] = []
        source = self.source
        base = frozenset(self.keys)
        for count, (start, end) in enumerate(self.spans):
            if count % 512 == 0:
                self.budget.check()
            if not source.startswith("{%", start):
                continue
            match = _TAG_NAME.match(source, start)
            word = match.group(1) if match else ""
            if word == "with" and match:
                inherited = stack[-1] if stack else frozenset()
                pattern = _names_pattern(base | inherited)
                tokens = _TOKEN.findall(source[match.end() : end - 2])
                bindings: List[Tuple[str, str]] = []
                if len(tokens) == 3 and tokens[1] == "as":
                    bindings.append((tokens[2], tokens[0]))
                else:
                    for token in tokens:
                        kwarg = _KWARG.match(token)
                        if kwarg:
                            bindings.append((kwarg.group(1), kwarg.group(2)))
                bound = {alias for alias, value in bindings if pattern.search(value)}
                if bound and len(inherited) < MAX_ALIASES:
                    inherited = inherited | bound
                stack.append(inherited)
                offsets.append(end)
                states.append(inherited)
            elif word == "endwith" and stack:
                stack.pop()
                offsets.append(start)
                states.append(stack[-1] if stack else frozenset())
        return offsets, states

    @staticmethod
    def _aliases_at(states: Tuple[List[int], List[FrozenSet[str]]], offset: int) -> FrozenSet[str]:
        offsets, sets = states
        return sets[bisect_right(offsets, offset) - 1]

    # -- one tag --------------------------------------------------------------

    def _tag_reads(
        self, text: str, pattern: "re.Pattern[str]", origin: Optional[str], depth: int
    ) -> bool:
        if _tag_word(text) == "include":
            return self._include_reads(text, pattern, origin, depth)
        return bool(pattern.search(text))

    def _include_reads(
        self, text: str, pattern: "re.Pattern[str]", origin: Optional[str], depth: int
    ) -> bool:
        parsed = _INCLUDE.match(text)
        if not parsed:
            return True
        name, rest = parsed.group("name"), parsed.group("rest")
        static = len(name) >= 2 and name[0] in "\"'" and name[0] == name[-1]
        if not static or rest.lstrip().startswith("|"):
            return True  # a variable or a filtered name: not visible here
        if pattern.search(rest):
            return True  # passed in by the include tag itself
        if "only" in _TOKEN.findall(rest):
            return False  # the included file sees nothing but the `with` values
        return self._file_reads(name[1:-1], pattern, origin, depth + 1)

    def _file_reads(
        self, name: str, pattern: "re.Pattern[str]", origin: Optional[str], depth: int
    ) -> bool:
        self.budget.check()
        if depth > MAX_INCLUDE_DEPTH:
            return True
        resolved = _resolve_include(name, origin)
        if resolved is None:
            return True
        canonical, source = resolved
        memo_key = (canonical, pattern.pattern)
        if memo_key in self._memo:
            seen = self._memo[memo_key]
            return True if seen is None else seen  # None: a cycle
        self.budget.included += 1
        if self.budget.included > MAX_INCLUDED_TEMPLATES or len(source) > MAX_SOURCE_CHARS:
            return True
        self._memo[memo_key] = None
        reads = False
        for start, end in _template_spans(source):
            text = source[start:end]
            if _tag_word(text) == "extends":
                reads = True
                break
            if self._tag_reads(text, pattern, canonical, depth):
                reads = True
                break
        self._memo[memo_key] = reads
        return reads

    # -- the containers -------------------------------------------------------

    def containers(self) -> Tuple[str, ...]:
        source = self.source
        masked = _mask(source, self.spans)
        scanner = _Scanner(masked)
        for offset in range(0, len(masked), _CHUNK):
            self.budget.check()
            scanner.feed(masked[offset : offset + _CHUNK])
        self.budget.check()
        scanner.close()
        aliases = self._alias_states()
        starts = [span[0] for span in self.spans]
        found = set()
        for element in scanner.containers:
            if not element.cid:
                continue
            self.budget.check()
            pattern = _names_pattern(set(self.keys) | self._aliases_at(aliases, element.body_start))
            nested = sorted(element.nested)
            cursor = 0
            for index in range(bisect_left(starts, element.body_start), len(self.spans)):
                start, end = self.spans[index]
                if start >= element.body_end:
                    break
                while cursor < len(nested) and nested[cursor][1] <= start:
                    cursor += 1
                if cursor < len(nested) and nested[cursor][0] <= start:
                    continue  # inside a nested dj-update container
                if self._tag_reads(source[start:end], pattern, self.origin, 0):
                    found.add(element.cid)
                    break
        return tuple(sorted(found))


def accumulating_container_ids(
    source: str,
    temporary_keys: Iterable[str],
    *,
    origin: Optional[str] = None,
) -> Tuple[str, ...]:
    """The ``id``s of the ``dj-update`` containers in ``source`` fed by a temporary key.

    ``temporary_keys`` are the names the template reads the rows under
    (``temporary_assigns`` keys plus any context key that holds the same object);
    ``origin`` is the template's own name, which relative ``{% include %}`` names
    are resolved against. Never raises: on any failure, an oversize source or a scan
    that outlasts its budget the answer is ``()``, the pre-#3645 behaviour.
    """
    keys = tuple(temporary_keys)
    if not keys or "dj-update" not in source:
        return ()
    if len(source) > MAX_SOURCE_CHARS:
        logger.warning(
            "dj-update scan: a template of %d characters is too large to scan (limit %d); "
            "its dj-update containers are re-rendered whole instead of accumulating rows",
            len(source),
            MAX_SOURCE_CHARS,
        )
        return ()
    try:
        return _Scan(source, keys, origin).containers()
    except _ScanTimeout:
        logger.warning(
            "dj-update scan: scanning a template took more than %.1f s; its dj-update "
            "containers are re-rendered whole instead of accumulating rows",
            SCAN_BUDGET_SECONDS,
        )
        return ()
    except _ScanTooDeep:
        logger.warning(
            "dj-update scan: a template nests more than %d elements; its dj-update "
            "containers are re-rendered whole instead of accumulating rows",
            MAX_ELEMENT_DEPTH,
        )
        return ()
    except Exception as exc:  # noqa: BLE001 — the scan is advisory; the page must still render
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(
            logger,
            (),
            exc,
            "dj-update scan failed; its dj-update containers are re-rendered whole",
            level="warning",
        )
        return ()


def view_accumulating_container_ids(view: Any, aliases: Iterable[str] = ()) -> Tuple[str, ...]:
    """The accumulating containers of ``view``'s template, worked out once per class.

    ``aliases`` are context keys the caller found to hold a ``temporary_assigns``
    object under another name. The answer is cached on the view class, keyed by
    the names it was worked out for, and dropped by
    ``hot_view_replacement.note_template_change`` when the file watcher sees a
    template (or an included file) change; without the watcher it lasts until the
    process restarts, which is when templates change in production.
    """
    declared = getattr(view, "temporary_assigns", None)
    if not declared:
        return ()
    keys = tuple(sorted(set(declared) | set(aliases)))
    cls = type(view)
    cached: Optional[Dict[Tuple[str, ...], Tuple[str, ...]]] = cls.__dict__.get(
        "_djust_accumulating_ids"
    )
    if cached is not None and keys in cached:
        return cached[keys]
    try:
        source = view.get_template()
        origin = None if getattr(view, "template", None) else getattr(view, "template_name", None)
        ids = accumulating_container_ids(source, keys, origin=origin)
    except Exception as exc:  # noqa: BLE001 — an unreadable template renders nothing anyway
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(
            logger,
            (view,),
            exc,
            "%s: could not read the template to find its dj-update containers; "
            "they are re-rendered whole instead of accumulating rows",
            cls.__name__,
            level="warning",
        )
        ids = ()
    fresh = dict(cached or {})
    if len(fresh) >= 8:
        fresh = {}
    fresh[keys] = ids
    cls._djust_accumulating_ids = fresh  # type: ignore[attr-defined]
    return ids
