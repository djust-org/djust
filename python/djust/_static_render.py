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

The state backend then stores the released view: the template and the version,
no context and no tree. Any later render of the view (an event, a server push,
``request_html``, a reconnect's mount, an HTTP fallback POST) has nothing to diff
against and sends the full page, as a first render does. That is the fail-safe:
correct output, at the cost of a full render and a full-page frame.

Only the HTML renderer is released: a native renderer's wire format is patches.
"""

from __future__ import annotations

from typing import Any


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
    release()
    rust_view.retain_state_keys([])
    if hasattr(rust_view, "set_raw_py_values"):
        rust_view.set_raw_py_values({})
    # The next sync must send every key: the renderer no longer holds any.
    view._prev_context_refs = {}
    view._prev_context_immutables = {}
    view._prev_context_fingerprints = {}
    # ``static_assigns`` are sent once because the renderer retains them.
    if getattr(view, "_static_assigns_sent", False):
        view._static_assigns_sent = False
    return True
