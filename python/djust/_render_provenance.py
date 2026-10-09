"""HTML plus immutable, render-local UTF-8 authored intervals (#3252).

Only renderer results grant intervals. Slicing/concatenation shift them;
replacement bytes never inherit the authority of bytes they replace.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any


class RenderedHTML(str):
    def __new__(
        cls, html: str, spans: tuple[tuple[int, int], ...] = (), held: tuple = ()
    ) -> RenderedHTML:
        obj = super().__new__(cls, html)
        object.__setattr__(obj, "spans", tuple(spans))
        object.__setattr__(obj, "held", tuple(held))
        return obj

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Render provenance is immutable")

    def __getitem__(self, key: Any) -> str:
        value = super().__getitem__(key)
        if not isinstance(key, slice) or key.step not in (None, 1):
            return value
        start, end, _ = key.indices(len(self))
        left = len(str.__getitem__(self, slice(0, start)).encode())
        right = len(str.__getitem__(self, slice(0, end)).encode())
        spans = tuple(
            (max(a, left) - left, min(b, right) - left)
            for a, b in self.spans
            if a < right and b > left
        )
        held = tuple(
            (a - left, b - left, block) for a, b, block in self.held if a >= left and b <= right
        )
        return RenderedHTML(value, spans, held)

    def __add__(self, other: str) -> RenderedHTML:
        offset = len(self.encode())
        spans = self.spans + tuple((a + offset, b + offset) for a, b in getattr(other, "spans", ()))
        held = self.held + tuple(
            (a + offset, b + offset, block) for a, b, block in getattr(other, "held", ())
        )
        return RenderedHTML(str.__add__(self, other), spans, held)

    def __radd__(self, other: str) -> RenderedHTML:
        return RenderedHTML(other) + self

    def replace(self, old: str, new: str, count: int = -1) -> RenderedHTML:
        if not old:
            # Inserting between characters is a transform, not composition.
            return RenderedHTML(str.replace(self, old, new, count))
        result = RenderedHTML("")
        cursor = 0
        for i, match in enumerate(re.finditer(re.escape(old), self)):
            if count >= 0 and i >= count:
                break
            result += self[cursor : match.start()] + new
            cursor = match.end()
        return result + self[cursor:]


def restore_preserved(html: RenderedHTML) -> RenderedHTML:
    """Restore only render-owned placeholder intervals, never matching text."""
    for start, end, block in reversed(html.held):
        encoded = html.encode()
        left, right = len(encoded[:start].decode()), len(encoded[:end].decode())
        if isinstance(block, RenderedHTML):
            block = restore_preserved(block)
        html = html[:left] + block + html[right:]
    return html


@lru_cache(maxsize=256)
def _needs_provenance(source: str) -> bool:
    return any(token in source.lower() for token in ("dj-lazy", "include", "extends"))


def render_html(rust_view: Any, source: str) -> str:
    """Keep plain pages on their String render; includes may introduce lazy HTML."""
    if not _needs_provenance(source):
        return rust_view.render()
    html, spans = rust_view.render_with_provenance()
    return RenderedHTML(html, tuple(spans))


def sub(pattern: Any, replacement: Any, html: str, flags: int = 0) -> str:
    """Regex replacement with explicit emission offsets on tracked pages."""
    if not isinstance(html, RenderedHTML):
        return re.sub(pattern, replacement, html, flags=flags)
    result = RenderedHTML("")
    cursor = 0
    for match in re.finditer(pattern, html, flags):
        value = replacement(match) if callable(replacement) else match.expand(replacement)
        # An unchanged normalizer token is an identity operation. A changed
        # token drops its old intervals, even if its replacement spells HTML.
        if value == match.group(0) and not isinstance(value, RenderedHTML):
            value = html[match.start() : match.end()]
        result += html[cursor : match.start()] + value
        cursor = match.end()
    return result + html[cursor:]


def collapse(html: str, block_tags: list[str]) -> str:
    from ._rust import collapse_inter_tag_whitespace, inter_tag_whitespace_edits

    if not isinstance(html, RenderedHTML):
        return collapse_inter_tag_whitespace(html, block_tags)
    encoded = html.encode()
    result = RenderedHTML("")
    cursor = 0
    for start, end in inter_tag_whitespace_edits(html, block_tags):
        left = len(encoded[:cursor].decode())
        right = len(encoded[:start].decode())
        result += html[left:right]
        cursor = end
    return result + html[len(encoded[:cursor].decode()) :]
