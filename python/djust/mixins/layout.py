"""
LayoutMixin — Runtime layout switching for LiveView (v0.6.0).

Lets an event handler swap the surrounding layout template (nav, sidebar,
footer, etc.) without a full page reload, preserving the inner LiveView
state::

    class EditorView(LiveView):
        template_name = "editor/page.html"

        @event_handler
        def enter_fullscreen(self, **kwargs):
            self.fullscreen = True
            self.set_layout("layouts/fullscreen.html")

        @event_handler
        def exit_fullscreen(self, **kwargs):
            self.fullscreen = False
            self.set_layout("layouts/app.html")

The mixin queues a pending layout path; the WebSocket consumer drains the
queue after the next ``_send_update`` and emits a discrete
``{"type": "layout", "path": ..., "html": ...}`` frame. The client then
swaps the document body while preserving the live ``[dj-root]`` element's
identity (and therefore all inner LiveView state — form values, scroll
position, focused elements, dj-hook bookkeeping).

Phoenix 1.1 added runtime layout support; this is the djust equivalent.
"""

from typing import Any, Optional


class LayoutMixin:
    """Mixin exposing :meth:`set_layout` for runtime layout switching.

    The queue holds at most one pending path — repeated calls in the same
    handler overwrite (the "last write wins" reflects that the client
    only applies the final layout anyway).
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pending_layout: Optional[str] = None

    def set_layout(self, template_path: str) -> None:
        """Queue a layout swap to be applied after the current handler returns.

        Args:
            template_path: Django template path (e.g.
                ``"layouts/fullscreen.html"``). The template is resolved
                via Django's template loader, so it must be findable by
                the configured loaders.
        """
        self._pending_layout = template_path

    def _drain_pending_layout(self) -> Optional[str]:
        """Return the pending layout path and reset the queue.

        Called by the WebSocket consumer after each ``_send_update``.
        """
        path = self._pending_layout
        self._pending_layout = None
        return path


def render_pending_layout(view: Any, layout_path: str) -> str:
    """Render ``layout_path`` with ``view``'s current context (sync).

    The one body both ``_flush_pending_layout`` twins (the WebSocket
    consumer's and ``ViewRuntime``'s) run. Callers run it in ONE
    ``sync_to_async`` hop, like every other render path (#3178): the context
    build and the template render stay off the event-loop thread and, with
    ``LIVEVIEW_CONFIG["worker_threads"]``, on the session's pinned worker, so
    ORM access in ``get_context_data`` sees the session's thread and its DB
    connection. Exceptions propagate to the caller's error handling.
    """
    from django.template.loader import render_to_string

    context = view.get_context_data() if hasattr(view, "get_context_data") else {}
    html: str = render_to_string(layout_path, context)
    return html
