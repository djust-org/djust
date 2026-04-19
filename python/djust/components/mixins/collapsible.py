"""
CollapsibleMixin — collapsible section state management for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Collapsible`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Collapsible

        class MyPage(LiveView):
            details = Collapsible()
            # self.details.is_open → False

Legacy usage::

    class MyPage(CollapsibleMixin, LiveView):
        def mount(self, request, **kwargs):
            self.init_collapsible("details", is_open=True)
            self.details = self.get_collapsible_ctx("details")
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState

__all__ = ["CollapsibleMixin", "CollapsibleState"]


class CollapsibleState(TypedState):
    """Typed state for a single collapsible instance."""

    is_open: bool = False


class CollapsibleMixin(ComponentMixin):
    """Mixin adding collapsible section state management and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Collapsible`` instead.
    """

    component_name = "collapsible"
    collapsible_instances = None

    def init_collapsible(self, instance_id, is_open=False):
        """Register a collapsible instance.

        Args:
            instance_id: Unique identifier for this collapsible section.
            is_open: Whether the section is initially open.
        """
        if self.collapsible_instances is None:
            self.collapsible_instances = {}
        self.collapsible_instances[instance_id] = CollapsibleState(is_open=bool(is_open))

    @event_handler
    def toggle_collapsible(self, component_id="", **kwargs):
        """Toggle a collapsible section open/closed."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, CollapsibleState)
        if inst is None:
            return
        inst.is_open = not inst.is_open

    def get_collapsible_ctx(self, instance_id):
        """Return template context dict for a collapsible instance."""
        inst = self._get_typed_instance(instance_id, CollapsibleState)
        if inst is None:
            return {"is_open": False, "event": "toggle_collapsible", "component_id": instance_id}
        return {
            "is_open": inst.is_open,
            "event": "toggle_collapsible",
            "component_id": instance_id,
        }
