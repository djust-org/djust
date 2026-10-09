"""Immutable result-owned authored byte intervals and stable node addresses."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from itertools import accumulate
from typing import Any


class RenderedHTML(str):
    spans: tuple
    held: tuple
    origins: tuple
    byte_length: int

    def __new__(
        cls,
        html: str,
        spans: tuple = (),
        held: tuple = (),
        origins: tuple = (),
        *,
        byte_length: int | None = None,
    ) -> RenderedHTML:
        obj = super().__new__(cls, html)
        object.__setattr__(obj, "spans", tuple(spans))
        object.__setattr__(obj, "held", tuple(held))
        object.__setattr__(obj, "origins", tuple(origins))
        # Sparse UTF-8 index: ASCII costs nothing, and slices never encode a
        # growing prefix. The index is built once for this owned result.
        object.__setattr__(obj, "_wide", None)
        object.__setattr__(obj, "_extra", None)
        object.__setattr__(
            obj, "byte_length", len(html.encode()) if byte_length is None else byte_length
        )
        object.__setattr__(obj, "_span_index", None)
        object.__setattr__(obj, "_origin_index", None)
        object.__setattr__(obj, "_held_index", None)
        return obj

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Render provenance is immutable")

    def byte_offset(self, pos: int) -> int:
        if pos == 0:
            return 0
        if pos == len(self):
            return self.byte_length
        if self.isascii():
            return pos
        if self._wide is None:
            # Root/page splicing usually queries tiny ASCII prefixes/suffixes.
            # Bound these probes; never scan or encode a growing page prefix.
            if pos < 256 and str.__getitem__(self, slice(0, pos)).isascii():
                return pos
            if len(self) - pos < 256 and str.__getitem__(self, slice(pos, None)).isascii():
                return self.byte_length - len(self) + pos
        if self._wide is None:
            extra = [
                (m.start(), len(m.group().encode()) - 1) for m in re.finditer(r"[^\x00-\x7f]", self)
            ]
            object.__setattr__(self, "_wide", tuple(p for p, _ in extra))
            object.__setattr__(self, "_extra", (0, *accumulate(n for _, n in extra)))
        return pos + self._extra[bisect_left(self._wide, pos)]

    def char_offset(self, byte: int) -> int:
        # Only boundary offsets enter from the Rust renderer/editor.
        if byte == 0:
            return 0
        if byte == self.byte_length:
            return len(self)
        tail = self.byte_length - byte
        if 0 <= tail < 256:
            pos = len(self) - tail
            if pos >= 0 and str.__getitem__(self, slice(pos, None)).isascii():
                return pos
        low, high = 0, len(self)
        while low < high:
            mid = (low + high) // 2
            if self.byte_offset(mid) < byte:
                low = mid + 1
            else:
                high = mid
        return low

    def __getitem__(self, key: Any) -> str:
        value = super().__getitem__(key)
        if not isinstance(key, slice) or key.step not in (None, 1):
            return value
        if not value:
            return RenderedHTML("", byte_length=0)
        start, end, _ = key.indices(len(self))
        left, right = self.byte_offset(start), self.byte_offset(end)
        if self._span_index is None:
            object.__setattr__(
                self,
                "_span_index",
                (tuple(a for a, b in self.spans), tuple(b for a, b in self.spans)),
            )
        starts, ends = self._span_index
        begin, finish = bisect_left(ends, left + 1), bisect_left(starts, right)
        spans = tuple(
            (max(a, left) - left, min(b, right) - left) for a, b in self.spans[begin:finish]
        )
        if self._held_index is None:
            object.__setattr__(
                self,
                "_held_index",
                (tuple(a for a, b, _ in self.held), tuple(b for a, b, _ in self.held)),
            )
        starts, ends = self._held_index
        begin, finish = bisect_left(starts, left), bisect_right(ends, right)
        held = tuple((a - left, b - left, block) for a, b, block in self.held[begin:finish])
        begin, finish = self._origin_range(left, right)
        origins = tuple(
            (max(a, left) - left, min(b, right) - left, c + max(a, left) - a, id)
            for a, b, c, id in self.origins[begin:finish]
        )
        return RenderedHTML(value, spans, held, origins, byte_length=right - left)

    def _origin_range(self, left: int, right: int) -> tuple[int, int]:
        if self._origin_index is None:
            object.__setattr__(
                self,
                "_origin_index",
                (tuple(a for a, b, _, _ in self.origins), tuple(b for a, b, _, _ in self.origins)),
            )
        starts, ends = self._origin_index
        return bisect_left(ends, left + 1), bisect_left(starts, right)

    def authored_identity(self, start: int, end: int) -> tuple[int, str] | None:
        """Address the authored node intersecting a surviving container."""
        begin, finish = self._origin_range(start, end)
        if begin == finish:
            return None
        a, _, source, node = self.origins[begin]
        return source + max(start, a) - a, node

    def __add__(self, other: str) -> RenderedHTML:
        return join((self, other))

    def __radd__(self, other: str) -> RenderedHTML:
        return join((other, self))

    def replace(self, old: str, new: str, count: int = -1) -> RenderedHTML:
        if not old:
            return RenderedHTML(str.replace(self, old, new, count))
        parts, cursor = [], 0
        for i, match in enumerate(re.finditer(re.escape(old), self)):
            if count >= 0 and i >= count:
                break
            parts.extend((self[cursor : match.start()], new))
            cursor = match.end()
        parts.append(self[cursor:])
        return join(parts)


def join(parts: Any) -> RenderedHTML:
    """One allocation and one offset pass for a sequence of rendered parts."""
    parts = list(parts)
    spans, held, origins, offset = [], [], [], 0
    for part in parts:
        spans.extend((a + offset, b + offset) for a, b in getattr(part, "spans", ()))
        held.extend((a + offset, b + offset, block) for a, b, block in getattr(part, "held", ()))
        origins.extend(
            (a + offset, b + offset, c, id) for a, b, c, id in getattr(part, "origins", ())
        )
        offset += part.byte_length if isinstance(part, RenderedHTML) else len(part.encode())
    return RenderedHTML(
        "".join(parts), tuple(spans), tuple(held), tuple(origins), byte_length=offset
    )


def restore_preserved(html: RenderedHTML) -> RenderedHTML:
    parts, cursor = [], 0
    for start, end, block in html.held:
        left, right = html.char_offset(start), html.char_offset(end)
        if isinstance(block, RenderedHTML):
            block = restore_preserved(block)
        parts.extend((html[cursor:left], block))
        cursor = right
    if not parts:
        return html
    parts.append(html[cursor:])
    return join(parts)


def _needs_provenance(source: str, dirs: tuple = ()) -> bool:
    from ._rust import template_needs_provenance

    if not dirs:
        from .utils import get_template_dirs

        dirs = tuple(get_template_dirs())
    return template_needs_provenance(source, list(dirs))


def render_html(rust_view: Any, source: str) -> str:
    """Plan from compiled authored nodes and resolved template dependencies."""
    if not _needs_provenance(source):
        return rust_view.render()
    html, spans, origins = rust_view.render_lazy_html()
    return RenderedHTML(html, tuple(spans), origins=tuple(origins)) if spans else html


def sub(pattern: Any, replacement: Any, html: str, flags: int = 0) -> str:
    if not isinstance(html, RenderedHTML):
        return re.sub(pattern, replacement, html, flags=flags)
    if (
        getattr(pattern, "pattern", pattern) == r"[ \t\n\r\f]+"
        and replacement == " "
        and not html.held
    ):
        from ._rust import normalize_provenance_whitespace

        value, spans, origins = normalize_provenance_whitespace(
            html, list(html.spans), list(html.origins)
        )
        return RenderedHTML(value, tuple(spans), origins=tuple(origins))
    parts, cursor = [], 0
    for match in re.finditer(pattern, html, flags):
        value = replacement(match) if callable(replacement) else match.expand(replacement)
        if value == match.group(0) and not isinstance(value, RenderedHTML):
            value = html[match.start() : match.end()]
        parts.extend((html[cursor : match.start()], value))
        cursor = match.end()
    if not parts:
        return html
    parts.append(html[cursor:])
    return join(parts)


def collapse(html: str, block_tags: list[str]) -> str:
    from ._rust import collapse_inter_tag_whitespace, collapse_provenance_whitespace

    if not isinstance(html, RenderedHTML):
        return collapse_inter_tag_whitespace(html, block_tags)
    if not html.held:
        value, spans, origins = collapse_provenance_whitespace(
            html, list(html.spans), list(html.origins), block_tags
        )
        return RenderedHTML(value, tuple(spans), origins=tuple(origins))
    # Held placeholders are few; preserve their owner intervals through the
    # same batched slices as other normalizer edits.
    from ._rust import inter_tag_whitespace_edits

    parts, cursor = [], 0
    for start, end in inter_tag_whitespace_edits(html, block_tags):
        parts.append(html[html.char_offset(cursor) : html.char_offset(start)])
        cursor = end
    parts.append(html[html.char_offset(cursor) :])
    return join(parts)
