"""``static_render``: a view that keeps no diff baseline (#3626).

djust keeps, for every rendered view, the rendered tree the next turn diffs
against and a copy of every context value the render used, both in the live
``RustLiveView`` and in the state backend's snapshot. For a page that renders
once and does not change (a report, a diagram, a document), that is memory spent
on patches that never come. A view that sets ``static_render = True`` drops both
after every render:

- the tree, the last HTML and the render caches (``release_baseline``, which
  keeps the version counter the HTTP fallback and SSE stamp on their frames);
- the renderer's context copy (``retain_state_keys([])``), and with it the
  change-detection fingerprints, so the next sync sends the whole context again.
  The one thing kept is the few immutable values the page's ``<title>`` reads,
  which ``navigation_title`` renders after a ``dj-navigate``.

The state backend then stores the released view: the template and the version,
no context and no tree. Any later render of the view (an event, a server push,
``request_html``, a reconnect's mount, an HTTP fallback POST) has nothing to diff
against and sends the full page, as a first render does. That is the fail-safe:
correct output, at the cost of a full render and a full-page frame.

Only the HTML renderer is released: a native renderer's wire format is patches.
"""

from __future__ import annotations

import logging
import weakref
from typing import Any

logger = logging.getLogger(__name__)

# Classes already warned about, so a view warns once per process, not per render.
_WARNED_TEMPORARY_ASSIGNS: "weakref.WeakSet[type]" = weakref.WeakSet()


def _warn_temporary_assigns(view: Any) -> None:
    """``static_render`` + ``temporary_assigns``: every later render is full, and a
    ``temporary_assigns`` key has been reset to its default by then, so its rows
    vanish from the page after the first event. Say so once per class."""
    declared = getattr(view, "temporary_assigns", None)
    cls = type(view)
    if not declared or cls in _WARNED_TEMPORARY_ASSIGNS:
        return
    _WARNED_TEMPORARY_ASSIGNS.add(cls)
    logger.warning(
        "%s sets static_render = True and temporary_assigns (%s). Every render after "
        "the first is a full page rendered from the view's current state, and a "
        "temporary_assigns key is reset to its default after each render, so those "
        "rows are missing from every later page. Drop one of the two: static_render "
        "for a page that updates, temporary_assigns for a page that renders once.",
        cls.__name__,
        ", ".join(sorted(str(key) for key in declared)),
    )


def is_static_render(view: Any) -> bool:
    """Whether ``view`` opted in (``static_render = True`` on its class, exactly)."""
    return getattr(type(view), "static_render", False) is True


def release_render_baseline(view: Any) -> bool:
    """Drop ``view``'s diff baseline and its renderer's context copy, if it opted in.

    Called right after the render, before the state backend re-stores the
    view's snapshot, so the stored entry is the released one. Returns whether
    anything was released.
    """
    if not is_static_render(view):
        return False
    renderer = getattr(view, "_djust_renderer", None)
    if renderer is not None:
        from .renderers import HtmlRenderer

        if not isinstance(renderer, HtmlRenderer):
            return False
    rust_view = getattr(view, "_rust_view", None)
    release = getattr(rust_view, "release_baseline", None)
    if not callable(release):
        return False
    # ``navigation_title`` renders the destination's ``<title>`` from the immutable
    # values of the last render. Keep the ones the title reads, nothing else: a
    # title built from a value ``get_context_data`` computed would otherwise be
    # lost, and the tab would keep the previous page's title after ``dj-navigate``.
    from .runtime import navigation_title_variables

    held = getattr(view, "_prev_context_immutables", None) or {}
    title_values = {name: held[name] for name in navigation_title_variables(view) if name in held}
    release()
    rust_view.retain_state_keys([])
    if hasattr(rust_view, "set_raw_py_values"):
        rust_view.set_raw_py_values({})
    # The next sync must send every key: the renderer no longer holds any.
    view._prev_context_refs = {}
    view._prev_context_immutables = title_values
    view._prev_context_fingerprints = {}
    _warn_temporary_assigns(view)
    # ``static_assigns`` are sent once because the renderer retains them.
    if getattr(view, "_static_assigns_sent", False):
        view._static_assigns_sent = False
    return True
