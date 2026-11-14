"""
NavMenu — descriptor for the ``{% nav_menu %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        nav = NavMenu()

    # template: {% nav_menu mobile_open=nav.mobile_open %}...{% endnav_menu %}
    # ``toggle_nav`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["NavMenu"]


class NavMenu(LiveComponent):
    """Whether the ``{% nav_menu %}`` list is open on a narrow screen."""

    class State(TypedState):
        mobile_open: bool = False

    class Meta:
        # The ``{% nav_menu %}`` tag's default ``toggle_event`` (#4126).
        events = ("toggle_nav",)

    @event_handler
    def toggle_nav(self, **kwargs: Any) -> None:
        """Open or close the menu (the hamburger's default ``toggle_event``)."""
        state = bound_state(self)
        state.mobile_open = not state.mobile_open
