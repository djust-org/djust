"""
AccordionMixin — accordion state management for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Accordion`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Accordion

        class MyPage(LiveView):
            faq = Accordion(active="q1")
            # self.faq.active → "q1"

Legacy usage::

    class MyPage(AccordionMixin, LiveView):
        def mount(self, request, **kwargs):
            self.init_accordion("faq", active="q1")
            self.faq = self.get_accordion_ctx("faq")

    # In event handler or template:
    #   self.accordion_instances["faq"].active  → "q1"
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState
from typing import Any, Dict, List, Optional, Union, cast

__all__ = ["AccordionMixin", "AccordionState"]


class AccordionState(TypedState):
    """Typed state for a single accordion instance."""

    # ``active`` is a single item id (``str``) when ``multiple`` is False, or a
    # list of open item ids when ``multiple`` is True.
    active: Union[str, List[str]] = ""
    multiple: bool = False


class AccordionMixin(ComponentMixin):
    """Mixin adding accordion state management and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Accordion`` instead.
    """

    component_name = "accordion"
    accordion_instances: Optional[Dict[str, "AccordionState"]] = None

    def init_accordion(
        self,
        instance_id: str,
        active: Union[str, List[str]] = "",
        multiple: bool = False,
    ) -> None:
        """Register an accordion instance.

        Args:
            instance_id: Unique identifier for this accordion.
            active: Initially active item ID (str), or list if multiple=True.
            multiple: Whether multiple items can be open simultaneously.
        """
        if self.accordion_instances is None:
            self.accordion_instances = {}
        self.accordion_instances[instance_id] = AccordionState(
            active=list(active)
            if multiple and isinstance(active, (list, tuple))
            else (active if not multiple else []),
            multiple=multiple,
        )

    @event_handler
    def accordion_toggle(self, value: str = "", component_id: str = "", **kwargs: Any) -> None:
        """Toggle an accordion item open/closed."""
        component_id = self._resolve_component_id(component_id) or ""
        inst = self._get_typed_instance(component_id, AccordionState)
        if inst is None:
            return
        if inst.multiple:
            # In multiple mode ``active`` is always a list (see init_accordion).
            actives = cast(List[str], inst.active)
            if value in actives:
                actives.remove(value)
            else:
                actives.append(value)
        else:
            inst.active = "" if inst.active == value else value

    def get_accordion_ctx(self, instance_id: str) -> Dict[str, Any]:
        """Return template context dict for an accordion instance."""
        inst = self._get_typed_instance(instance_id, AccordionState)
        if inst is None:
            return {"active": "", "event": "accordion_toggle", "component_id": instance_id}
        return {
            "active": inst.active,
            "event": "accordion_toggle",
            "component_id": instance_id,
        }
