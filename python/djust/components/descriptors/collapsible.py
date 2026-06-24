"""
Collapsible — descriptor-based collapsible section component.

Usage::

    class MyPage(LiveView):
        details = Collapsible()

        # self.details.is_open → False
        # toggle_collapsible event auto-registered
"""

from .base import LiveComponent, TypedState
from typing import Any


__all__ = ["Collapsible"]


class Collapsible(LiveComponent):
    """Collapsible section component with open/close toggle."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        event = "toggle_collapsible"

    def _handle_event(self, state: "State", **kwargs: Any) -> None:
        state.is_open = not state.is_open
