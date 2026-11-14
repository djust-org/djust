"""
TruncatedList — descriptor for the ``{% truncated_list %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        tags = TruncatedList()

    # template: {% truncated_list items=tags_list expanded=tags.expanded %}
    # ``toggle_list`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["TruncatedList"]


class TruncatedList(LiveComponent):
    """Expanded/collapsed state of ``{% truncated_list %}``."""

    class State(TypedState):
        expanded: bool = False

    class Meta:
        # The ``{% truncated_list %}`` tag's default ``toggle_event`` (#4126).
        events = ("toggle_list",)

    @event_handler
    def toggle_list(self, **kwargs: Any) -> None:
        """Show all items or the first few (the tag's default ``toggle_event``)."""
        state = bound_state(self)
        state.expanded = not state.expanded
