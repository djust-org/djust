"""
TabsMixin — tab state management for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Tabs`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Tabs

        class MyPage(LiveView):
            nav = Tabs(active="overview")
            # self.nav.active → "overview"

Legacy usage::

    class MyPage(TabsMixin, LiveView):
        def mount(self, request, **kwargs: Any) -> None:
            self.init_tabs("nav", active="overview")
            self.nav = self.get_tabs_ctx("nav")
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState
from typing import Any, Dict, Optional


__all__ = ["TabsMixin", "TabsState"]


class TabsState(TypedState):
    """Typed state for a single tabs instance."""

    active: str = ""


class TabsMixin(ComponentMixin):
    """Mixin adding tab state management and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Tabs`` instead.
    """

    component_name = "tabs"
    tabs_instances: Optional[Dict[str, "TabsState"]] = None

    def init_tabs(self, instance_id: str, active: str = "") -> None:
        """Register a tabs instance.

        Args:
            instance_id: Unique identifier for this tab group.
            active: Initially active tab ID.
        """
        if self.tabs_instances is None:
            self.tabs_instances = {}
        self.tabs_instances[instance_id] = TabsState(active=active)

    @event_handler
    def set_tab(self, value: str = "", component_id: str = "", **kwargs: Any) -> None:
        """Set the active tab for a tab group."""
        component_id = self._resolve_component_id(component_id) or ""
        inst = self._get_typed_instance(component_id, TabsState)
        if inst is None:
            return
        inst.active = value

    def get_tabs_ctx(self, instance_id: str) -> Dict[str, Any]:
        """Return template context dict for a tabs instance."""
        inst = self._get_typed_instance(instance_id, TabsState)
        if inst is None:
            return {"active": "", "event": "set_tab", "component_id": instance_id}
        return {
            "active": inst.active,
            "event": "set_tab",
            "component_id": instance_id,
        }
