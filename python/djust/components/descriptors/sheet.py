"""
Sheet — descriptor-based slide-out sheet component.

Usage::

    class MyPage(LiveView):
        sidebar = Sheet(side="left")

        # self.sidebar.is_open → False
        # self.sidebar.side → "left"
        # toggle_sheet event auto-registered
"""

from .base import LiveComponent, TypedState

__all__ = ["Sheet"]


class Sheet(LiveComponent):
    """Slide-out sheet component with open/close toggle and side positioning."""

    class State(TypedState):
        is_open: bool = False
        side: str = "right"

    class Meta:
        event = "toggle_sheet"

    def _handle_event(self, state, **kwargs):
        state.is_open = not state.is_open
