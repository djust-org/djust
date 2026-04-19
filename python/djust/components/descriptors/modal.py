"""
Modal — descriptor-based modal dialog component.

Usage::

    class MyPage(LiveView):
        confirm = Modal()

        # self.confirm.is_open → False
        # toggle_modal event auto-registered
"""

from .base import LiveComponent, TypedState

__all__ = ["Modal"]


class Modal(LiveComponent):
    """Modal dialog component with open/close toggle."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        event = "toggle_modal"

    def _handle_event(self, state, **kwargs):
        state.is_open = not state.is_open
