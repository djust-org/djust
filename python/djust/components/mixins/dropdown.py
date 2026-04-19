"""
DropdownMixin — dropdown menu state management for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Dropdown`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Dropdown

        class MyPage(LiveView):
            menu = Dropdown()
            # self.menu.is_open → False

Legacy usage::

    class MyPage(DropdownMixin, LiveView):
        def mount(self, request, **kwargs):
            self.init_dropdown("actions")
            self.actions = self.get_dropdown_ctx("actions")
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState

__all__ = ["DropdownMixin", "DropdownState"]


class DropdownState(TypedState):
    """Typed state for a single dropdown instance."""

    is_open: bool = False


class DropdownMixin(ComponentMixin):
    """Mixin adding dropdown menu state management and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Dropdown`` instead.
    """

    component_name = "dropdown"
    dropdown_instances = None

    def init_dropdown(self, instance_id, is_open=False):
        """Register a dropdown instance.

        Args:
            instance_id: Unique identifier for this dropdown.
            is_open: Whether the dropdown is initially open.
        """
        if self.dropdown_instances is None:
            self.dropdown_instances = {}
        self.dropdown_instances[instance_id] = DropdownState(is_open=bool(is_open))

    @event_handler
    def toggle_dropdown(self, component_id="", **kwargs):
        """Toggle a dropdown open/closed."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, DropdownState)
        if inst is None:
            return
        inst.is_open = not inst.is_open

    @event_handler
    def close_dropdown(self, component_id="", **kwargs):
        """Close a dropdown by component_id."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, DropdownState)
        if inst is None:
            return
        inst.is_open = False

    def get_dropdown_ctx(self, instance_id):
        """Return template context dict for a dropdown instance."""
        inst = self._get_typed_instance(instance_id, DropdownState)
        if inst is None:
            return {
                "is_open": False,
                "toggle_event": "toggle_dropdown",
                "close_event": "close_dropdown",
                "component_id": instance_id,
            }
        return {
            "is_open": inst.is_open,
            "toggle_event": "toggle_dropdown",
            "close_event": "close_dropdown",
            "component_id": instance_id,
        }
