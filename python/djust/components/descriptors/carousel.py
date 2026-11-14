"""
Carousel — descriptor-based carousel/slider component.

Usage::

    class MyPage(LiveView):
        slides = Carousel(total=5)

        # self.slides.active → 0
        # carousel_go event auto-registered
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Carousel"]


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
        try:
            state.active = int(value) % state.total if state.total > 0 else 0
        except (ValueError, TypeError):
            # Non-numeric value; keep current active index.
            pass

    @event_handler
    def carousel_prev(self, **kwargs: Any) -> None:
        """Go to the previous slide, wrapping to the last."""
        state = bound_state(self)
        state.active = (state.active - 1) % state.total if state.total > 0 else 0

    @event_handler
    def carousel_next(self, **kwargs: Any) -> None:
        """Go to the next slide, wrapping to the first."""
        state = bound_state(self)
        state.active = (state.active + 1) % state.total if state.total > 0 else 0
