"""
Carousel — descriptor-based carousel/slider component.

Usage::

    class MyPage(LiveView):
        slides = Carousel(total=5)

        # self.slides.active → 0
        # carousel_go event auto-registered

``total`` is required: the number of slides the ``{% carousel %}`` shows.
Set it in the declaration or in ``mount()`` (``self.slides.total =
len(images)``). Without it previous, next and the dots cannot move, and the
first such click logs a warning naming the component (#4126).
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, attribute_name, bound_state, warn_once
from typing import Any


__all__ = ["Carousel"]


def _has_slides(component: Any, total: int) -> bool:
    """Whether ``total`` is set; warn once per declaration if it is not."""
    if total > 0:
        return True
    name = attribute_name(component)
    warn_once(
        component,
        "total",
        "Carousel %r has no total, so previous, next and the dots do nothing. "
        "Declare %s = Carousel(total=N), or set self.%s.total in mount().",
        name,
        name,
        name,
    )
    return False


class Carousel(LiveComponent):
    """Carousel component with slide navigation."""

    class State(TypedState):
        active: int = 0
        total: int = 0

    class Meta:
        event = "carousel_go"
        # The ``{% carousel %}`` tag's default ``prev_event`` / ``next_event``
        # (#4037); both wrap around, as the dots' ``carousel_go`` does.
        events = ("carousel_prev", "carousel_next")

    def _handle_event(self, state: "State", value: str = "", **kwargs: Any) -> None:
        if not _has_slides(self, state.total):
            return
        try:
            state.active = int(value) % state.total
        except (ValueError, TypeError):
            # Non-numeric value; keep current active index.
            pass

    @event_handler
    def carousel_prev(self, **kwargs: Any) -> None:
        """Go to the previous slide, wrapping to the last."""
        state = bound_state(self)
        if not _has_slides(self, state.total):
            return
        state.active = (state.active - 1) % state.total

    @event_handler
    def carousel_next(self, **kwargs: Any) -> None:
        """Go to the next slide, wrapping to the first."""
        state = bound_state(self)
        if not _has_slides(self, state.total):
            return
        state.active = (state.active + 1) % state.total
