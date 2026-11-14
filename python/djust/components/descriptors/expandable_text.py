"""
ExpandableText — descriptor for the ``{% expandable_text %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        bio = ExpandableText()

    # template: {% expandable_text expanded=bio.expanded %}...{% endexpandable_text %}
    # ``toggle_expand`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["ExpandableText"]


class ExpandableText(LiveComponent):
    """Expanded/collapsed state of ``{% expandable_text %}``."""

    class State(TypedState):
        expanded: bool = False

    class Meta:
        # The ``{% expandable_text %}`` tag's default ``toggle_event`` (#4126).
        events = ("toggle_expand",)

    @event_handler
    def toggle_expand(self, **kwargs: Any) -> None:
        """Show more or less (the tag's default ``toggle_event``)."""
        state = bound_state(self)
        state.expanded = not state.expanded
