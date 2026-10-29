"""Render-local child thunks for a full-page render used as a live-root update.

An emitted capability is private to this render. Validate all occurrences and
select their HTML5 locations before invoking any mount callback. Page-shell and
inert occurrences are removed without constructing or mounting a child.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from secrets import token_hex
from typing import Any

from django.template import Context
from django.utils.html import format_html

from ._exposure import ExposureError


@dataclass
class _Deferred:
    owner: Any
    entries: dict[str, tuple[str, Callable[[], str]]] = field(default_factory=dict)
    materializing: bool = False


_active: ContextVar[_Deferred | None] = ContextVar("djust_deferred_root_children", default=None)


@contextmanager
def defer_root_children(view: Any, enabled: bool) -> Iterator[None]:
    if not enabled:
        yield
        return
    from ._rust import set_deferred_child_scope

    previous = set_deferred_child_scope(True)
    scope = _Deferred(view)
    token = _active.set(scope)
    try:
        yield
        if scope.entries:
            raise ExposureError("Deferred child render was not consumed")
    finally:
        _active.reset(token)
        set_deferred_child_scope(previous)
        scope.entries.clear()
        scope.owner = None


def defer_child_render(
    parent: Any, context: Context, view_path: str, kwargs: dict[str, Any]
) -> str | None:
    scope = _active.get()
    if scope is None or scope.owner is not parent or scope.materializing:
        return None
    from .templatetags.live_tags import live_render

    # The bridge's context stack can be popped before root selection. Retain
    # the resolved caller values, without reevaluating selectors or arguments.
    captured = Context(
        context.flatten(),
        autoescape=context.autoescape,
        use_l10n=context.use_l10n,
        use_tz=context.use_tz,
    )
    captured.render_context = context.render_context
    captured_kwargs = dict(kwargs)
    key = token_hex(16)
    while key in scope.entries:
        key = token_hex(16)
    marker = format_html('<div dj-view data-djust-embedded="{}"></div>', key)
    scope.entries[key] = (marker, lambda: live_render(captured, view_path, **captured_kwargs))
    return marker


def deferred_children_pending() -> bool:
    scope = _active.get()
    return scope is not None and not scope.materializing and bool(scope.entries)


def materialize_deferred_children(html: str, page_root: bool) -> list[tuple[int, int, str]]:
    scope = _active.get()
    if scope is None or scope.materializing:
        raise ExposureError("Deferred child render scope is unavailable")
    from ._rust import child_render_regions

    # Check the entire ledger before running any child code. A transform may
    # wrap or move a genuine same-render occurrence, but cannot duplicate,
    # consume, rewrite, fabricate or replay its private capability.
    positions = []
    for key, (marker, callback) in scope.entries.items():
        if html.count(marker) != 1 or html.count(key) != 1:
            raise ExposureError("Deferred child occurrence changed")
        start = html.index(marker)
        positions.append((start, start + len(marker), key, callback))
    try:
        regions = child_render_regions(html, list(scope.entries), page_root, False)
    except ValueError:
        raise ExposureError("Invalid deferred child ownership") from None
    positions.sort(key=lambda entry: entry[0])
    if any(a[1] > b[0] for a, b in zip(positions, positions[1:])):
        raise ExposureError("Overlapping deferred child occurrences")
    from ._render_provenance import RenderedHTML

    offsets = RenderedHTML(html)
    scope.materializing = True
    try:
        # Materialize one occurrence, then let Rust revalidate the root and
        # HTML5 context before the next sibling can run.
        start, end, key, callback = positions[0]
        replacement = callback() if regions.get(key) == "root" else ""
        if not isinstance(replacement, str):
            raise ExposureError("Deferred child renderer did not return HTML")
        del scope.entries[key]
        return [(offsets.byte_offset(start), offsets.byte_offset(end), str(replacement))]
    finally:
        scope.materializing = False
