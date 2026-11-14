"""
Sidebar — descriptor for the ``{% sidebar %}`` tag's UI state (#4134).

Usage::

    class MyPage(LiveView):
        side = Sidebar()

    # template: {% sidebar title="Menu" collapsed=side.collapsed %}...{% endsidebar %}
    # ``toggle_sidebar`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Sidebar"]


class Sidebar(LiveComponent):
    """Whether ``{% sidebar %}`` is collapsed."""

    class State(TypedState):
        collapsed: bool = False

    class Meta:
        # The ``{% sidebar %}`` tag's default ``toggle_event`` (#4134).
        events = ("toggle_sidebar",)

    @event_handler
    def toggle_sidebar(self, **kwargs: Any) -> None:
        """Collapse or expand the sidebar (the toggle's default ``toggle_event``)."""
        state = bound_state(self)
        state.collapsed = not state.collapsed
