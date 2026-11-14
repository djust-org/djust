"""
CommandPalette — descriptor for the ``{% command_palette %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        palette = CommandPalette()

    # template: {% command_palette open=palette.is_open %}...{% endcommand_palette %}
    # ``open_palette, close_palette`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["CommandPalette"]


class CommandPalette(LiveComponent):
    """Open state of ``{% command_palette %}``.

    ``search_event`` (``palette_search``) stays the application's: what a
    query matches is its data."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        # The ``{% command_palette %}`` tag's default ``close_event`` (#4126).
        events = ("open_palette", "close_palette")

    @event_handler
    def open_palette(self, **kwargs: Any) -> None:
        """Open the palette."""
        bound_state(self).is_open = True

    @event_handler
    def close_palette(self, **kwargs: Any) -> None:
        """Close the palette (the tag's default ``close_event``)."""
        bound_state(self).is_open = False
