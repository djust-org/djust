"""The pre-handler render of a page-POST fallback request (#4108).

A fallback request builds its view afresh, so its renderer has nothing to diff
the handler's result against. :func:`prepare_baseline` renders the restored
view once before the handler runs. When that render is the one the browser
shows (the client echoed its digest, ``_render_digest``), the renderer keeps it
as the diff baseline and the request answers with patches. In every other case
the renderer is reset, and the request goes on exactly as it did before this
module existed: one render after the handler, answered as a whole page.

A render is not free of effects, so the extra one is only made when nothing it
consumes is needed again. What a render consumes, and what happens here:

* ``temporary_assigns`` are reset to their defaults after a render, and stream
  operations are cleared. A view that has either is not pre-rendered.
* Django's messages are marked used when a template iterates them, and a used
  message is deleted when the response is processed. The flag is put back after
  the pre-render: a request that then answers without a render (a
  ``live_redirect``, a skipped render) must leave the message for the next
  page. A render after the handler iterates and consumes them as before.
* Embedded child views and lazy slots are registered (and, on an explicit page,
  bound to its nodes) by the render that prints them. A page whose template
  embeds one is not pre-rendered. If a render registers one all the same (an
  ``{% include %}`` brought it in), it is discarded like any render the client
  does not show, and the answer is the whole page.
* ``dj-update`` containers (append, prepend, ignore) and virtual lists hold DOM
  that is not the server's render, so a path into them cannot be trusted. A
  page whose render has either is answered with the whole page.
* ``get_context_data()`` and the context processors run for the pre-render as
  they do for any render (through ``render_with_diff`` and the sync-turn
  wrapper). A view whose context is costly or has effects of its own turns the
  feature off: ``http_fallback_patches = False``.
* An exception raised by the pre-render is not the request's failure: the
  renderer is reset and the handler runs. If the same error is real, the
  render after the handler raises it and is answered as before.
"""

from __future__ import annotations

import logging
from typing import Any

from ._render_digest import digests_match, render_digest, supplied_digest

logger = logging.getLogger(__name__)

#: A render containing either of these is not patched over this transport.
_CLIENT_OWNED_MARKERS = ("dj-update", "dj-virtual")


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


def _discard(view: Any) -> None:
    """Forget the pre-render: the next render is a first render again."""
    rust_view = getattr(view, "_rust_view", None)
    if rust_view is not None:
        rust_view.reset()


def rerender_request_scoped(view: Any) -> None:
    """Have the next render re-render what reads a context processor's value.

    Called before the render that follows a kept baseline. See the call site.
    """
    keys = set(getattr(view, "_context_processor_keys", None) or ())
    keys.add("request")
    forced = view.__dict__.get("_djust_forced_changed_keys")
    view.__dict__["_djust_forced_changed_keys"] = keys | set(forced or ())


def prepare_baseline(view: Any, request: Any) -> bool:
    """Render ``view`` before its handler; ``True`` if the client shows that render.

    ``True`` leaves the renderer holding that render: the render after the
    handler returns the patches from it. ``False`` means no render was made, or
    it was discarded: the renderer is a new one and the request answers exactly
    as it did before this existed. Never raises.
    """
    supplied = supplied_digest(request)
    if (
        supplied is None
        or not patches_enabled(view)
        or _consumed_by_a_render(view)
        or _embeds_views(view)
    ):
        # No render: nothing was consumed and nothing has to be undone.
        return False

    messages = getattr(request, "_messages", None)
    messages_used = getattr(messages, "used", None)
    lazy_thunks = view.__dict__.get("_lazy_thunks")
    lazy_before = list(lazy_thunks) if isinstance(lazy_thunks, list) else None
    matched = False
    try:
        from ._child_rendering import render_view_with_diff

        with view._processor_context(request):
            view._prepare_child_ids()
            html, _, _ = render_view_with_diff(view, request)
        if (
            isinstance(html, str)
            and not _holds_other_views(view)
            and not any(marker in html for marker in _CLIENT_OWNED_MARKERS)
        ):
            matched = digests_match(render_digest(view, request, html), supplied)
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
        # The render after the handler stashes its own lazy slots.
        if lazy_before is not None:
            view.__dict__["_lazy_thunks"] = lazy_before
        if not matched:
            try:
                _discard(view)
            except Exception as exc:  # noqa: BLE001 - never the request's failure
                from ._exposure_diagnostics import log_failure_for

                log_failure_for(logger, (view,), exc, "HTTP fallback baseline reset failed")
    return matched
