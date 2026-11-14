"""
NotificationCenter — descriptor for the notification bell's open state (#4134).

Usage::

    class MyPage(LiveView):
        bell = NotificationCenter()

    # template: {% notification_center notifications=items is_open=bell.is_open %}
    #       or: {% notification_popover notifications=items open=bell.is_open %}
    # ``toggle_notifications`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["NotificationCenter"]


class NotificationCenter(LiveComponent):
    """Whether the ``{% notification_center %}`` / ``{% notification_popover %}`` list is open.

    Marking a notification read and clearing them stay the application's: the
    notifications are its data."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        # The bell's default event on both tags (#4134).
        events = ("toggle_notifications",)

    @event_handler
    def toggle_notifications(self, **kwargs: Any) -> None:
        """Open or close the list (the bell's default event)."""
        state = bound_state(self)
        state.is_open = not state.is_open
