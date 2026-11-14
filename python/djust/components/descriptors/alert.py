"""
Alert — descriptor for a dismissible ``{% alert %}`` or ``{% page_alert %}`` (#4134).

Usage::

    class MyPage(LiveView):
        notice = Alert()

    # template:
    # {% if not notice.dismissed %}{% alert dismissible=True %}...{% endalert %}{% endif %}
    # ``dismiss_alert`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Alert"]


class Alert(LiveComponent):
    """Whether a dismissible ``{% alert %}`` / ``{% page_alert %}`` was dismissed.

    The tags have no hidden state of their own: render one only while
    ``dismissed`` is false. As with ``AnnouncementBar``, the dismissal lasts as
    long as the view's state does; store it yourself to remember it across
    visits."""

    class State(TypedState):
        dismissed: bool = False

    class Meta:
        # ``{% alert %}``'s default ``event`` and ``{% page_alert %}``'s default
        # ``dismiss_event`` (#4134).
        events = ("dismiss_alert",)

    @event_handler
    def dismiss_alert(self, **kwargs: Any) -> None:
        """Dismiss the alert (the tags' default dismiss event)."""
        bound_state(self).dismissed = True
