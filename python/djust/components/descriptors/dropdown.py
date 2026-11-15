"""
Dropdown — descriptor-based dropdown menu component.

Usage::

    class MyPage(LiveView):
        menu = Dropdown()

        # self.menu.is_open → False
        # toggle_dropdown event auto-registered

It also answers ``{% dropdown_menu %}``'s ``toggle_menu`` and ``{% split_button %}``'s
``toggle_split_menu`` (#4134): ``{% dropdown_menu open=menu.is_open %}``,
``{% split_button is_open=menu.is_open %}``.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Dropdown"]


class Dropdown(LiveComponent):
    """Dropdown menu component with open/close toggle (client-tier)."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        event = "toggle_dropdown"
        # #4037; ``toggle_menu`` / ``toggle_split_menu`` are the ``{% dropdown_menu %}`` and
        # ``{% split_button %}`` tags' default ``toggle_event`` (#4134).
        events = ("close_dropdown", "toggle_menu", "toggle_split_menu")
        tier = "client"
        # TODO(DEP-002 7.4): Client-tier WebSocket skip — when djust core client
        # JS supports it, client-tier components should skip the WebSocket send
        # entirely and handle state purely in the browser.
        # TODO(DEP-002 7.5): Add dj-update="ignore" to client-tier component
        # wrapper divs so server re-renders don't clobber client-managed state.

    def _handle_event(self, state: "State", **kwargs: Any) -> None:
        state.is_open = not state.is_open

    @event_handler
    def close_dropdown(self, **kwargs: Any) -> None:
        """Close the dropdown."""
        bound_state(self).is_open = False

    @event_handler
    def toggle_menu(self, **kwargs: Any) -> None:
        """Open or close ``{% dropdown_menu %}`` (its default ``toggle_event``)."""
        state = bound_state(self)
        state.is_open = not state.is_open

    @event_handler
    def toggle_split_menu(self, **kwargs: Any) -> None:
        """Open or close ``{% split_button %}``'s menu (its default ``toggle_event``)."""
        state = bound_state(self)
        state.is_open = not state.is_open
