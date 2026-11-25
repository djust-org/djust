"""
Dropdown component for djust - stateless.

Provides button-triggered dropdown menus for display purposes.

One renderer (#4002): ``_render_custom`` returns the markup of ``DropdownComponent``, built from
this class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from typing import Any, Dict, List, Optional, Union


from ..base import Component
from ._twin import plain_text, render_twin
from .dropdown import DropdownComponent, DropdownItem


class Dropdown(Component):
    """
    Simple, stateless dropdown component, drawn by ``DropdownComponent``.

    Args:
        label: Button text (escaped, whatever it is marked as)
        items: List of menu item dicts. Each becomes a ``DropdownItem``: ``label`` is its
            ``text`` (escaped), ``divider`` its ``divider``; the ``DropdownItem`` keys
            ``text``, ``action``, ``icon``, ``variant``, ``data``, ``url`` (checked and
            escaped; ``javascript:`` becomes ``#``) and ``disabled`` pass through.
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
        size: Button size (sm, md, lg)
        split: Use split button style
        direction: Dropdown direction (down, up, start, end)
        id: Dropdown ID (``DropdownComponent``'s ``component_id``; defaults to ``"dropdown"``)

    Examples:
        items = [
            {'label': 'Edit', 'action': 'edit_item'},
            {'label': 'Docs', 'url': '/docs/'},
            {'divider': True},
            {'label': 'Delete', 'action': 'delete_item', 'variant': 'danger'},
        ]
        dropdown = Dropdown(label="Menu", items=items)
        html = dropdown.render()

        Dropdown("Primary", items=items, split=True)
        Dropdown("Dropup", items=items, direction="up")
        Dropdown("Small Menu", items=items, size="sm")
    """

    # Note: Not using template because loops are needed for items
    # Using Python _render_custom() which is still fast (~50-100μs)

    def __init__(
        self,
        label: str,
        items: List[Dict[str, Union[str, bool]]],
        variant: str = "primary",
        size: str = "md",
        split: bool = False,
        direction: str = "down",
        id: Optional[str] = None,
    ):
        """
        Initialize dropdown component.

        Args:
            label: Button text
            items: List of menu item dicts
            variant: Color variant
            size: Button size (sm, md, lg)
            split: Use split button style
            direction: Dropdown direction (down, up, start, end)
            id: Dropdown ID (defaults to "dropdown")
        """
        super().__init__(
            label=label,
            items=items,
            variant=variant,
            size=size,
            split=split,
            direction=direction,
            id=id,
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.label = label
        self.items = items
        self.variant = variant
        self.size = size
        self.split = split
        self.direction = direction

    def _render_custom(self) -> str:
        return render_twin(
            DropdownComponent,
            self.id,
            label=plain_text(self.label),
            items=[_dropdown_item(item) for item in self.items],
            variant=self.variant,
            size=self.size,
            split=bool(self.split),
            direction=self.direction,
        )


def _dropdown_item(item: Dict[str, Any]) -> DropdownItem:
    """One simple-dropdown item dict as the ``DropdownItem`` the twin draws."""
    if item.get("divider"):
        return DropdownItem(divider=True)
    return DropdownItem(
        text=plain_text(item.get("text", item.get("label", ""))),
        action=item.get("action") or "",
        data=item.get("data"),
        icon=plain_text(item.get("icon", "")),
        variant=item.get("variant") or "",
        url=item.get("url") or "",
        disabled=bool(item.get("disabled")),
    )
