"""
Modal — descriptor-based modal dialog component.

Usage::

    class MyPage(LiveView):
        confirm = Modal()

        # self.confirm.is_open → False
        # toggle_modal event auto-registered
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Modal"]


class Modal(LiveComponent):
    """Modal dialog component with open/close toggle."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        event = "toggle_modal"
        # The ``{% modal %}`` tag's default close event and its open
        # counterpart (#4037): both answered here, no app handler needed.
        events = ("open_modal", "close_modal")

    def _handle_event(self, state: "State", **kwargs: Any) -> None:
        state.is_open = not state.is_open

    @event_handler
    def open_modal(self, **kwargs: Any) -> None:
        """Open the modal."""
        bound_state(self).is_open = True

    @event_handler
    def close_modal(self, **kwargs: Any) -> None:
        """Close the modal (the ``{% modal %}`` tag's default ``close_event``)."""
        bound_state(self).is_open = False
