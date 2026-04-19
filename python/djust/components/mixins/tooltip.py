"""
TooltipMixin — server-managed tooltip state for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Tooltip`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Tooltip

        class MyPage(LiveView):
            hint = Tooltip()
            # self.hint.is_visible → False

Most tooltips are CSS-only, but this mixin handles cases where tooltip
visibility needs to be tracked on the server (e.g. for analytics or
conditional content loading).

Legacy usage::

    class MyPage(TooltipMixin, LiveView):
        def mount(self, request, **kwargs):
            self.init_tooltip("help")
            self.help_tip = self.get_tooltip_ctx("help")
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState

__all__ = ["TooltipMixin", "TooltipState"]


class TooltipState(TypedState):
    """Typed state for a single tooltip instance."""

    is_visible: bool = False


class TooltipMixin(ComponentMixin):
    """Mixin adding server-managed tooltip state and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Tooltip`` instead.
    """

    component_name = "tooltip"
    tooltip_instances = None

    def init_tooltip(self, instance_id, is_visible=False):
        """Register a tooltip instance.

        Args:
            instance_id: Unique identifier for this tooltip.
            is_visible: Whether the tooltip is initially visible.
        """
        if self.tooltip_instances is None:
            self.tooltip_instances = {}
        self.tooltip_instances[instance_id] = TooltipState(is_visible=bool(is_visible))

    @event_handler
    def show_tooltip(self, component_id="", **kwargs):
        """Show a tooltip by component_id."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, TooltipState)
        if inst is None:
            return
        inst.is_visible = True

    @event_handler
    def hide_tooltip(self, component_id="", **kwargs):
        """Hide a tooltip by component_id."""
        component_id = self._resolve_component_id(component_id)
        inst = self._get_typed_instance(component_id, TooltipState)
        if inst is None:
            return
        inst.is_visible = False

    def get_tooltip_ctx(self, instance_id):
        """Return template context dict for a tooltip instance."""
        inst = self._get_typed_instance(instance_id, TooltipState)
        if inst is None:
            return {
                "is_visible": False,
                "show_event": "show_tooltip",
                "hide_event": "hide_tooltip",
                "component_id": instance_id,
            }
        return {
            "is_visible": inst.is_visible,
            "show_event": "show_tooltip",
            "hide_event": "hide_tooltip",
            "component_id": instance_id,
        }
