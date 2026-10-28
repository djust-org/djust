"""Which ``dj-update`` containers hold rows from a ``temporary_assigns`` key (#3639).

A view that declares ``temporary_assigns`` renders only the rows it just
assigned, so a ``dj-update="append"`` / ``"prepend"`` container that shows them
has to ACCUMULATE in the differ: the new rows join the stored ones instead of
replacing them. A container fed by an ordinary attribute is re-rendered whole
every time, and the template stays authoritative there (a row it no longer lists
leaves the page). The differ cannot tell the two apart from the markup, so this
module reads the template source: a container accumulates when its body names a
temporary key (``{{ rows }}``, ``{% for r in rows %}``), a stream (``streams``,
which ``temporary_assigns`` empties after every render too) or ``{% include %}``
(whose body is not visible here, so it is assumed to read one).

The container needs a static ``id`` on its tag: the differ finds it by that
``id``, and the client adds a child only by ``id`` as well. A container whose
``id`` is itself a template expression is left alone.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, List, Optional, Tuple

_TEMPLATE_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_ACCUMULATING = ("append", "prepend")


class _Scanner(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self._line_starts = [0]
        for line in source.splitlines(keepends=True):
            self._line_starts.append(self._line_starts[-1] + len(line))
        #: open elements: (tag, container id or None, offset where the body starts)
        self._open: List[Tuple[str, Optional[str], int]] = []
        #: (container id, its body source)
        self.containers: List[Tuple[str, str]] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _VOID:
            return
        found = dict(attrs)
        cid = found.get("id")
        accumulates = found.get("dj-update") in _ACCUMULATING and bool(cid) and "{" not in cid
        body_start = self._offset() + len(self.get_starttag_text() or "")
        self._open.append((tag, cid if accumulates else None, body_start))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                _tag, cid, body_start = self._open[index]
                del self._open[index:]
                if cid:
                    self.containers.append((cid, self.source[body_start : self._offset()]))
                return


def accumulating_container_ids(source: str, temporary_keys: Tuple[str, ...]) -> Tuple[str, ...]:
    """The ``id``s of the ``dj-update`` containers in ``source`` fed by a temporary key."""
    if not temporary_keys or "dj-update" not in source:
        return ()
    scanner = _Scanner(source)
    scanner.feed(source)
    scanner.close()
    names = "|".join(re.escape(key) for key in (*temporary_keys, "streams"))
    reads = re.compile(r"(?<![\w.])(?:%s)\b|\{%%-?\s*include\b" % names)
    return tuple(
        sorted(
            {
                cid
                for cid, body in scanner.containers
                if any(reads.search(tag) for tag in _TEMPLATE_TAG.findall(body))
            }
        )
    )


def view_accumulating_container_ids(view: Any) -> Tuple[str, ...]:
    """The accumulating containers of ``view``'s template, worked out once per class.

    Cached on the view class, the way the template hash slot is: ``template`` /
    ``template_name`` are stable for a class in practice, and a hot-reloaded
    class is a new object with an empty cache.
    """
    keys = tuple(sorted(getattr(view, "temporary_assigns", None) or ()))
    if not keys:
        return ()
    cls = type(view)
    cached = cls.__dict__.get("_djust_accumulating_ids")
    if cached is not None and cached[0] == keys:
        return cached[1]
    ids = accumulating_container_ids(view.get_template(), keys)
    cls._djust_accumulating_ids = (keys, ids)  # type: ignore[attr-defined]
    return ids
