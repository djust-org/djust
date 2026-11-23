"""The pre-handler render of a page-POST fallback request (#4108).

A fallback request builds its view afresh, so its renderer has nothing to diff
the handler's result against. :func:`prepare_baseline` renders the restored
view once before the handler runs. When that render is the one the browser
shows (the client echoed its digest and reports the same DOM shape,
``_render_digest``), its renderer becomes the request's renderer, and the
render after the handler returns the patches from it. In every other case the
request goes on exactly as it did before this module existed: one render after
the handler, answered as a whole page.

**The pre-handler render runs on a throwaway copy of the view.** A render is
not free of effects: it evaluates querysets (which then hold their rows), fills
``cached_property`` values, consumes ``temporary_assigns`` and stream
operations, registers embedded children, and records what it sent for the next
render's change detection. Done on the view itself, any of those would leak
into the render after the handler: a queryset the handler's write made stale
would be rendered from its cache, and the answer would miss the change that a
single render shows. So the view is deep-copied (the request is shared, not
copied), the copy is rendered and dropped, and only its renderer (the compiled
template and the baseline tree) is kept. The view the handler runs on has never
rendered: its render after the handler is a first render in every respect
except that the renderer has a tree to diff against. It sends its whole
context and renders every node.

What the copy cannot isolate is the request, which both renders share:

* Django's messages are marked used when a template iterates them, and a used
  message is deleted when the response is processed. The flag is put back after
  the pre-render: a request that then answers without a render (a
  ``live_redirect``, a skipped render) must leave the message for the next
  page.
* ``get_context_data()`` and the context processors run for the pre-render as
  they do for any render (through ``render_with_diff`` and the sync-turn
  wrapper). A view whose context is costly, or acts on something outside the
  view (the database, a counter in the session), turns the feature off:
  ``http_fallback_patches = False``.

No pre-render is made, and the answer is the whole page, for a view with
``temporary_assigns`` or streams and for a page whose template embeds another
view: there the browser's DOM is by design not the server's render. A render
that holds a ``dj-update="append"`` / ``"prepend"`` / ``"replace"``
container, a virtual list (``dj-virtual``) or an embedded child is answered
with the whole page too. These are read from the render's start tags, not its
text: ``dj-update`` written in the page's content (a code sample) changes
nothing (#4283).

A ``dj-update="ignore"`` element (``{% dj_flash %}``'s container) does not
turn patches off (#4283). Its children are the client's, so both shapes leave
them out (``_render_digest.render_shape``) and a patch elsewhere on the page
addresses the same node in both trees. Its path is kept on the view
(``_http_fallback_opaque_paths``): an answer whose patches address the element
or anything under it (:func:`patches_touch_opaque`) is the whole page, which
keeps its client-owned children. (The differ emits nothing under such an
element, whose previous children it splices into the new render; a change to
the element's own attributes is what this catches.)

A view that cannot be copied (it holds a lock, a connection, an open file),
and a pre-render that raises, are not the request's failure: the handler runs
and the answer is the whole page.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from ._render_digest import (
    canonical_html,
    render_digest,
    render_shape,
    supplied_token,
    tokens_match,
)

logger = logging.getLogger(__name__)

#: Where :func:`prepare_baseline` keeps the paths of the matched render's
#: ``dj-update="ignore"`` elements, for :func:`patches_touch_opaque`.
OPAQUE_PATHS_ATTR = "_http_fallback_opaque_paths"


def patches_touch_opaque(view: Any, patches: Any, html: Any = None) -> bool:
    """Whether a patch addresses a ``dj-update="ignore"`` element, or a node under
    one (#4283): its path is the element's path or starts with it. A patch
    without a path counts as touching. Such an answer is the whole page, which
    keeps the client's children of the element.

    The client resolves each patch's path against the DOM as the patches before
    it left it, so the element's path is taken from both ends: the baseline
    render (kept by :func:`prepare_baseline`) and ``html``, the render after the
    handler. A path that only matches by coincidence costs a whole page, never
    a wrong patch. The differ itself emits nothing under such an element (it
    splices the previous render's children in), so what this catches in
    practice is a change to the element's own attributes.
    """
    opaque = set(view.__dict__.get(OPAQUE_PATHS_ATTR) or ())
    if not opaque:
        return False
    if html is not None:
        after = render_shape(html)
        if after is None:
            return True
        opaque.update(after.opaque_paths)
    for patch in patches:
        path = patch.get("path") if isinstance(patch, dict) else None
        if not isinstance(path, list):
            return True
        for prefix in opaque:
            if tuple(path[: len(prefix)]) == prefix:
                return True
    return False


def patches_enabled(view: Any) -> bool:
    """The global setting and the view's own switch; both default to on."""
    if getattr(view, "http_fallback_patches", True) is False:
        return False
    try:
        from .config import config

        return config.get("http_fallback_patches", True) is not False
    except Exception:  # noqa: BLE001 - an unreadable config keeps the whole-page answer
        return False


def _consumed_by_a_render(view: Any) -> bool:
    """Whether the view holds something a render would use up."""
    if getattr(view, "temporary_assigns", None):
        return True
    return bool(getattr(view, "_streams", None))


#: ``{% live_render %}`` (lazy or not) and the ``dj-lazy`` hydration attribute.
_EMBEDDING_MARKERS = ("live_render", "dj-lazy")


def _embeds_views(view: Any) -> bool:
    """Whether the view's own template source embeds another view.

    Read from the template text, before any render: such a page is not
    pre-rendered at all. A child that only an ``{% include %}`` brings in is
    not seen here; ``_holds_other_views`` finds it after the render.
    """
    try:
        source = view.get_template()
    except Exception:  # noqa: BLE001 - an unreadable template is not pre-rendered
        return True
    if not isinstance(source, str):
        return True
    return any(marker in source for marker in _EMBEDDING_MARKERS)


def _holds_other_views(view: Any) -> bool:
    """Whether the render registered embedded children or lazy slots."""
    getter = getattr(view, "_get_all_child_views", None)
    if callable(getter) and getter():
        return True
    if getattr(view, "_explicit_child_vdom_tracking", False):
        return True
    return bool(view.__dict__.get("_http_lazy_containers") or view.__dict__.get("_lazy_thunks"))


def _throwaway_copy(view: Any, request: Any) -> Any:
    """A deep copy of ``view`` that shares the request and nothing else."""
    memo: dict[int, Any] = {id(request): request}
    return copy.deepcopy(view, memo)


def prepare_baseline(view: Any, request: Any) -> bool:
    """Render a copy of ``view`` before its handler; ``True`` if the client shows that render.

    ``True`` gives ``view`` the copy's renderer: the render after the handler
    returns the patches from the copy's render. ``False`` leaves ``view``
    untouched, so the request answers exactly as it did before this existed.
    Never raises.
    """
    supplied = supplied_token(request)
    if (
        supplied is None
        or not patches_enabled(view)
        or _consumed_by_a_render(view)
        or _embeds_views(view)
    ):
        return False

    messages = getattr(request, "_messages", None)
    messages_used = getattr(messages, "used", None)
    matched = False
    try:
        from ._child_rendering import render_view_with_diff

        baseline = _throwaway_copy(view, request)
        with baseline._processor_context(request):
            baseline._prepare_child_ids()
            html, _, _ = render_view_with_diff(baseline, request)
        found = render_shape(canonical_html(html, request)) if isinstance(html, str) else None
        if (
            found is not None
            and not _holds_other_views(baseline)
            and not found.client_owned
            and tokens_match(html, request, render_digest(view, request, html), supplied)
        ):
            renderer = getattr(baseline, "_rust_view", None)
            if renderer is not None:
                # The renderer only: the compiled template and the baseline
                # tree. None of the copy's Python state comes with it.
                view._rust_view = renderer
                view._rust_view_explicit = getattr(baseline, "_rust_view_explicit", False)
                view.__dict__[OPAQUE_PATHS_ATTR] = tuple(found.opaque_paths)
                matched = True
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(logger, (view,), exc, "HTTP fallback pre-handler render failed")
        matched = False
    finally:
        if messages_used is not None:
            try:
                messages.used = messages_used
            except Exception:  # noqa: BLE001 - a storage without the flag keeps its own
                logger.debug("could not restore the messages 'used' flag")
    return matched
