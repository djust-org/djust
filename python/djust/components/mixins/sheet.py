"""
SheetMixin — slide-out sheet/drawer state management for djust LiveViews.

.. deprecated::
    Use the descriptor-based ``Sheet`` component from
    ``djust_components.descriptors`` instead (DEP-002)::

        from djust.components.descriptors import Sheet

        class MyPage(LiveView):
            sidebar = Sheet(side="right")
            # self.sidebar.is_open → False

Legacy usage::

    class MyPage(SheetMixin, LiveView):
        def mount(self, request, **kwargs: Any) -> None:
            self.init_sheet("settings", side="right")
            self.settings = self.get_sheet_ctx("settings")
"""

from djust.decorators import event_handler

from .base import ComponentMixin, TypedState
from typing import Any, Dict, Optional


__all__ = ["SheetMixin", "SheetState"]


class SheetState(TypedState):
    """Typed state for a single sheet instance."""

    is_open: bool = False
    side: str = "right"


class SheetMixin(ComponentMixin):
    """Mixin adding sheet/drawer state management and event handlers.

    .. deprecated:: Use ``djust_components.descriptors.Sheet`` instead.
    """

    component_name = "sheet"
    sheet_instances: Optional[Dict[str, "SheetState"]] = None

    def init_sheet(self, instance_id: str, is_open: bool = False, side: str = "right") -> None:
        """Register a sheet instance.

        Args:
            instance_id: Unique identifier for this sheet.
            is_open: Whether the sheet is initially open.
            side: Which side the sheet slides from (left, right).
        """
        if self.sheet_instances is None:
            self.sheet_instances = {}
        self.sheet_instances[instance_id] = SheetState(
            is_open=bool(is_open),
            side=side if side in ("left", "right") else "right",
        )

    @event_handler
    def open_sheet(self, component_id: str = "", **kwargs: Any) -> None:
        """Open a sheet by component_id."""
        component_id = self._resolve_component_id(component_id) or ""
        inst = self._get_typed_instance(component_id, SheetState)
        if inst is None:
            return
        inst.is_open = True

    @event_handler
    def close_sheet(self, component_id: str = "", **kwargs: Any) -> None:
        """Close a sheet by component_id."""
        component_id = self._resolve_component_id(component_id) or ""
        inst = self._get_typed_instance(component_id, SheetState)
        if inst is None:
            return
        inst.is_open = False

    def get_sheet_ctx(self, instance_id: str) -> Dict[str, Any]:
        """Return template context dict for a sheet instance."""
        inst = self._get_typed_instance(instance_id, SheetState)
        if inst is None:
            return {
                "is_open": False,
                "side": "right",
                "open_event": "open_sheet",
                "close_event": "close_sheet",
                "component_id": instance_id,
            }
        return {
            "is_open": inst.is_open,
            "side": inst.side,
            "open_event": "open_sheet",
            "close_event": "close_sheet",
            "component_id": instance_id,
        }
