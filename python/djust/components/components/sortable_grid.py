"""Sortable Grid component — 2D drag-and-drop grid."""

import html
from typing import Optional

from djust import Component


class SortableGrid(Component):
    """2D drag-and-drop grid layout.

    Uses ``dj-hook="SortableGrid"`` for client-side drag interactions.
    Fires a server event with the new order on drop.

    Usage in a LiveView::

        self.grid = SortableGrid(
            items=[
                {"id": "1", "label": "Item A", "thumbnail": "/img/a.png"},
                {"id": "2", "label": "Item B"},
            ],
            columns=3,
            move_event="reorder",
        )

    In template::

        {{ grid|safe }}

    Args:
        items: list of dicts with ``id``, ``label``, optional ``thumbnail``
        columns: number of grid columns (default 3)
        move_event: djust event fired on reorder
        gap: CSS gap value (default "0.75rem")
        disabled: disable drag (default False)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        items: Optional[list] = None,
        columns: int = 3,
        move_event: str = "reorder",
        gap: str = "0.75rem",
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            columns=columns,
            move_event=move_event,
            gap=gap,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.columns = columns
        self.move_event = move_event
        self.gap = gap
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-sortable-grid"]
        if self.disabled:
            classes.append("dj-sortable-grid--disabled")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_event = html.escape(self.move_event)
        e_gap = html.escape(self.gap)

        items_html = []
        for item in self.items:
            if not isinstance(item, dict):
                continue
            item_id = html.escape(str(item.get("id", "")))
            label = html.escape(str(item.get("label", "")))
            thumbnail = item.get("thumbnail", "")
            thumb_html = ""
            if thumbnail:
                e_thumb = html.escape(str(thumbnail))
                thumb_html = (
                    f'<img class="dj-sortable-grid__thumb" '
                    f'src="{e_thumb}" alt="{label}" loading="lazy">'
                )
            drag_attr = ' draggable="true"' if not self.disabled else ""
            items_html.append(
                f'<div class="dj-sortable-grid__item" data-id="{item_id}"{drag_attr}>'
                f"{thumb_html}"
                f'<span class="dj-sortable-grid__label">{label}</span></div>'
            )

        disabled_attr = ' data-disabled="true"' if self.disabled else ""
        style = f'style="grid-template-columns:repeat({int(self.columns)},1fr);gap:{e_gap}"'

        return (
            f'<div class="{class_str}" dj-hook="SortableGrid" '
            f'data-move-event="{e_event}" data-columns="{int(self.columns)}" '
            f'{style} role="grid"{disabled_attr}>{"".join(items_html)}</div>'
        )
