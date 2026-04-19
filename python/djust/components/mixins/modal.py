"""
ModalMixin — modal dialog state management for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Modal`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Modal

        class MyPage(LiveView):
            confirm = Modal()
            # self.confirm.is_open → False

Legacy usage::

    class MyPage(ModalMixin, LiveView):
        def mount(self, request, **kwargs):
            self.init_modal("confirm")
            self.confirm = self.get_modal_ctx("confirm")
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState

__all__ = ["ModalMixin", "ModalState"]


class ModalState(TypedState):
    """Typed state for a single modal instance."""

    is_open: bool = False


class ModalMixin(ComponentMixin):
    """Mixin adding modal state management and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Modal`` instead.
    """

    component_name = "modal"
    modal_instances = None

    def init_modal(self, instance_id, is_open=False):
        """Register a modal instance.

        Args:
            instance_id: Unique identifier for this modal.
            is_open: Whether the modal is initially open.
        """
        if self.modal_instances is None:
            self.modal_instances = {}
        self.modal_instances[instance_id] = ModalState(is_open=bool(is_open))

    @event_handler
    def open_modal(self, component_id="", **kwargs):
        """Open a modal by component_id."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, ModalState)
        if inst is None:
            return
        inst.is_open = True

    @event_handler
    def close_modal(self, component_id="", **kwargs):
        """Close a modal by component_id."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, ModalState)
        if inst is None:
            return
        inst.is_open = False

    @event_handler
    def toggle_modal(self, component_id="", **kwargs):
        """Toggle a modal open/closed."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, ModalState)
        if inst is None:
            return
        inst.is_open = not inst.is_open

    def get_modal_ctx(self, instance_id):
        """Return template context dict for a modal instance."""
        inst = self._get_typed_instance(instance_id, ModalState)
        if inst is None:
            return {
                "is_open": False,
                "open_event": "open_modal",
                "close_event": "close_modal",
                "toggle_event": "toggle_modal",
                "component_id": instance_id,
            }
        return {
            "is_open": inst.is_open,
            "open_event": "open_modal",
            "close_event": "close_modal",
            "toggle_event": "toggle_modal",
            "component_id": instance_id,
        }
