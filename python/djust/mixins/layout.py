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

from typing import Optional


class LayoutMixin:
    """Mixin exposing :meth:`set_layout` for runtime layout switching.

    The queue holds at most one pending path — repeated calls in the same
    handler overwrite (the "last write wins" reflects that the client
    only applies the final layout anyway).
    """

    def __init__(self, **kwargs) -> None:
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
