"""
Tooltip — descriptor-based tooltip component.

Usage::

    class MyPage(LiveView):
        hint = Tooltip()

        # self.hint.is_visible → False
        # toggle_tooltip event auto-registered
"""

from .base import LiveComponent, TypedState
from typing import Any


__all__ = ["Tooltip"]


class Tooltip(LiveComponent):
    """Tooltip component with visibility toggle (client-tier)."""

    class State(TypedState):
        is_visible: bool = False

    class Meta:
        event = "toggle_tooltip"
        tier = "client"
        # TODO(DEP-002 7.4): Client-tier WebSocket skip — when djust core client
        # JS supports it, client-tier components should skip the WebSocket send
        # entirely and handle state purely in the browser.
        # TODO(DEP-002 7.5): Add dj-update="ignore" to client-tier component
        # wrapper divs so server re-renders don't clobber client-managed state.

    def _handle_event(self, state: "State", **kwargs: Any) -> None:
        state.is_visible = not state.is_visible
