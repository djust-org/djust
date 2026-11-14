"""
AnnouncementBar — descriptor for the ``{% announcement_bar %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        notice = AnnouncementBar()

    # template: {% if not notice.dismissed %}{% announcement_bar dismissible=True %}...{% endannouncement_bar %}{% endif %}
    # ``dismiss_announcement`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["AnnouncementBar"]


class AnnouncementBar(LiveComponent):
    """Whether a dismissible ``{% announcement_bar %}`` was dismissed.

    The tag has no hidden state of its own: render it only while
    ``dismissed`` is false. The state lives on this view, so a dismissal
    lasts as long as the view's state does; store it yourself (session,
    user preference) to remember it across visits."""

    class State(TypedState):
        dismissed: bool = False

    class Meta:
        # The ``{% announcement_bar %}`` tag's default ``dismiss_event`` (#4126).
        events = ("dismiss_announcement",)

    @event_handler
    def dismiss_announcement(self, **kwargs: Any) -> None:
        """Dismiss the bar (the tag's default ``dismiss_event``)."""
        bound_state(self).dismissed = True
