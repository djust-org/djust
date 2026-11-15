"""
DatePicker — descriptor for the month ``{% date_picker %}`` shows (#4134).

Usage::

    class MyPage(LiveView):
        cal = DatePicker()

    # template: {% date_picker year=cal.year month=cal.month selected=day %}
    # ``date_prev_month`` / ``date_next_month`` answered here, no app handler needed.
"""

import datetime

from djust.components.utils import calendar_month
from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["DatePicker"]


def _shown(state: Any) -> tuple[int, int]:
    """The (year, month) the calendar shows: each field the state's, or today's
    when unset (0) or out of range, as the tag reads them (#4134)."""
    return calendar_month(state.year, state.month)


def _step(state: Any, delta: int) -> None:
    year, month = _shown(state)
    index = year * 12 + (month - 1) + delta
    year, month = divmod(index, 12)
    if not datetime.MINYEAR <= year <= datetime.MAXYEAR:
        return  # the calendar has no month there
    state.year, state.month = year, month + 1


class DatePicker(LiveComponent):
    """The year and month ``{% date_picker %}`` shows.

    ``year`` / ``month`` 0 (the default) show this year / this month, each on its
    own, as the tag does (``DatePicker(year=2030)`` shows this month of 2030); the
    previous / next buttons step from there, across years. Picking a day
    (``date_select``) stays the application's: the chosen date is its data."""

    class State(TypedState):
        year: int = 0
        month: int = 0

    class Meta:
        # The ``{% date_picker %}`` tag's default ``prev_event`` / ``next_event`` (#4134).
        events = ("date_prev_month", "date_next_month")

    @event_handler
    def date_prev_month(self, **kwargs: Any) -> None:
        """Show the previous month (the tag's default ``prev_event``)."""
        _step(bound_state(self), -1)

    @event_handler
    def date_next_month(self, **kwargs: Any) -> None:
        """Show the next month (the tag's default ``next_event``)."""
        _step(bound_state(self), 1)
