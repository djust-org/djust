"""
Sheet — descriptor-based slide-out sheet component.

Usage::

    class MyPage(LiveView):
        sidebar = Sheet(side="left")

        # self.sidebar.is_open → False
        # self.sidebar.side → "left"
        # toggle_sheet event auto-registered
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Sheet"]


class Sheet(LiveComponent):
    """Slide-out sheet component with open/close toggle and side positioning."""

    class State(TypedState):
        is_open: bool = False
        side: str = "right"

    class Meta:
        event = "toggle_sheet"
        # The ``{% sheet %}`` tag's default close event and its open
        # counterpart (#4037).
        events = ("open_sheet", "close_sheet")

    def _handle_event(self, state: "State", **kwargs: Any) -> None:
        state.is_open = not state.is_open

    @event_handler
    def open_sheet(self, **kwargs: Any) -> None:
        """Open the sheet."""
        bound_state(self).is_open = True

    @event_handler
    def close_sheet(self, **kwargs: Any) -> None:
        """Close the sheet (the ``{% sheet %}`` tag's default ``close_event``)."""
        bound_state(self).is_open = False
