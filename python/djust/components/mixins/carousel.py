"""
CarouselMixin — carousel/slideshow state management for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Carousel`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Carousel

        class MyPage(LiveView):
            slides = Carousel(total=5)
            # self.slides.active → 0

Legacy usage::

    class MyPage(CarouselMixin, LiveView):
        def mount(self, request, **kwargs):
            self.init_carousel("gallery", total=5)
            self.gallery = self.get_carousel_ctx("gallery")
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState

__all__ = ["CarouselMixin", "CarouselState"]


class CarouselState(TypedState):
    """Typed state for a single carousel instance."""

    active: int = 0
    total: int = 0


class CarouselMixin(ComponentMixin):
    """Mixin adding carousel state management and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Carousel`` instead.
    """

    component_name = "carousel"
    carousel_instances = None

    def init_carousel(self, instance_id, active=0, total=0):
        """Register a carousel instance.

        Args:
            instance_id: Unique identifier for this carousel.
            active: Initially active slide index (0-based).
            total: Total number of slides.
        """
        if self.carousel_instances is None:
            self.carousel_instances = {}
        self.carousel_instances[instance_id] = CarouselState(
            active=int(active),
            total=int(total),
        )

    @event_handler
    def carousel_prev(self, component_id="", **kwargs):
        """Go to the previous slide (wraps around)."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, CarouselState)
        if inst is None:
            return
        if inst.total > 0:
            inst.active = (inst.active - 1) % inst.total

    @event_handler
    def carousel_next(self, component_id="", **kwargs):
        """Go to the next slide (wraps around)."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, CarouselState)
        if inst is None:
            return
        if inst.total > 0:
            inst.active = (inst.active + 1) % inst.total

    @event_handler
    def carousel_go(self, value="0", component_id="", **kwargs):
        """Go to a specific slide by index."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, CarouselState)
        if inst is None:
            return
        try:
            index = int(value)
        except (ValueError, TypeError):
            return
        if inst.total > 0 and 0 <= index < inst.total:
            inst.active = index

    def get_carousel_ctx(self, instance_id):
        """Return template context dict for a carousel instance."""
        inst = self._get_typed_instance(instance_id, CarouselState)
        if inst is None:
            return {
                "active": 0,
                "total": 0,
                "prev_event": "carousel_prev",
                "next_event": "carousel_next",
                "go_event": "carousel_go",
                "component_id": instance_id,
            }
        return {
            "active": inst.active,
            "total": inst.total,
            "prev_event": "carousel_prev",
            "next_event": "carousel_next",
            "go_event": "carousel_go",
            "component_id": instance_id,
        }
