"""
Carousel — descriptor-based carousel/slider component.

Usage::

    class MyPage(LiveView):
        slides = Carousel(total=5)

        # self.slides.active → 0
        # carousel_go event auto-registered
"""

from .base import LiveComponent, TypedState

__all__ = ["Carousel"]


class Carousel(LiveComponent):
    """Carousel component with slide navigation."""

    class State(TypedState):
        active: int = 0
        total: int = 0

    class Meta:
        event = "carousel_go"

    def _handle_event(self, state, value="", **kwargs):
        try:
            state.active = int(value) % state.total if state.total > 0 else 0
        except (ValueError, TypeError):
            pass
