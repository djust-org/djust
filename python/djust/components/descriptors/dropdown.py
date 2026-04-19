"""
Dropdown — descriptor-based dropdown menu component.

Usage::

    class MyPage(LiveView):
        menu = Dropdown()

        # self.menu.is_open → False
        # toggle_dropdown event auto-registered
"""

from .base import LiveComponent, TypedState

__all__ = ["Dropdown"]


class Dropdown(LiveComponent):
    """Dropdown menu component with open/close toggle (client-tier)."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        event = "toggle_dropdown"
        tier = "client"
        # TODO(DEP-002 7.4): Client-tier WebSocket skip — when djust core client
        # JS supports it, client-tier components should skip the WebSocket send
        # entirely and handle state purely in the browser.
        # TODO(DEP-002 7.5): Add dj-update="ignore" to client-tier component
        # wrapper divs so server re-renders don't clobber client-managed state.

    def _handle_event(self, state, **kwargs):
        state.is_open = not state.is_open
