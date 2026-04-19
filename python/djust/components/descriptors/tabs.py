"""
Tabs — descriptor-based tab component.

Usage::

    class MyPage(LiveView):
        nav = Tabs(active="overview")

        # self.nav.active → "overview"
        # set_tab event auto-registered
"""

from .base import LiveComponent, TypedState

__all__ = ["Tabs"]


class Tabs(LiveComponent):
    """Tab component with active tab state management."""

    class State(TypedState):
        active: str = ""

    class Meta:
        event = "set_tab"

    def _handle_event(self, state, value="", **kwargs):
        state.active = value
