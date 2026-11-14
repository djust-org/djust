"""
MarkdownTextarea — descriptor for the ``{% markdown_textarea %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        editor = MarkdownTextarea()

    # template: {% markdown_textarea name="body" value=body preview=editor.preview %}
    # ``toggle_preview`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["MarkdownTextarea"]


class MarkdownTextarea(LiveComponent):
    """Write/Preview tab of ``{% markdown_textarea %}``.

    The Write and Preview tabs both send ``toggle_preview`` with their
    ``mode``; a click on the tab already shown changes nothing."""

    class State(TypedState):
        preview: bool = False

    class Meta:
        # The ``{% markdown_textarea %}`` tag's default ``toggle_event`` (#4126).
        events = ("toggle_preview",)

    @event_handler
    def toggle_preview(self, mode: str = "", **kwargs: Any) -> None:
        """Show the Write or the Preview tab (the tag's default ``toggle_event``)."""
        state = bound_state(self)
        if mode in ("write", "preview"):
            state.preview = mode == "preview"
        else:
            state.preview = not state.preview
