"""Sortable List component — drag-and-drop reorderable list."""

import html
from typing import Optional

from djust import Component


class SortableList(Component):
    """Drag-and-drop reorderable list.

    Uses ``dj-hook="SortableList"`` for client-side drag interactions.
    Fires a server event with the new order on drop.

    Usage in a LiveView::

        self.todo_list = SortableList(
            items=[
                {"id": "1", "label": "Buy groceries"},
                {"id": "2", "label": "Walk the dog"},
            ],
            move_event="reorder",
        )

    In template::

        {{ todo_list|safe }}

    CSS Custom Properties::

        --dj-sortable-gap: gap between items
        --dj-sortable-radius: item border radius
        --dj-sortable-drag-bg: background while dragging

    Args:
        items: list of dicts with ``id`` and ``label`` keys
        move_event: djust event fired on reorder (receives ``order`` list)
        handle: show drag handle (default True)
        disabled: disable drag (default False)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        items: Optional[list] = None,
        move_event: str = "reorder",
        handle: bool = True,
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            move_event=move_event,
            handle=handle,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.move_event = move_event
        self.handle = handle
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-sortable-list"]
        if self.disabled:
            classes.append("dj-sortable-list--disabled")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_event = html.escape(self.move_event)

        items_html = []
        for item in self.items:
            if not isinstance(item, dict):
                continue
            item_id = html.escape(str(item.get("id", "")))
            label = html.escape(str(item.get("label", "")))
            handle_html = (
                '<span class="dj-sortable-list__handle" aria-hidden="true">&#x2630;</span> '
                if self.handle
                else ""
            )
            drag_attr = ' draggable="true"' if not self.disabled else ""
            items_html.append(
                f'<li class="dj-sortable-list__item" data-id="{item_id}"{drag_attr} '
                f'role="listitem">'
                f"{handle_html}"
                f'<span class="dj-sortable-list__label">{label}</span></li>'
            )

        disabled_attr = ' data-disabled="true"' if self.disabled else ""

        return (
            f'<ul class="{class_str}" dj-hook="SortableList" '
            f'data-move-event="{e_event}" '
            f'role="list"{disabled_attr}>{"".join(items_html)}</ul>'
        )
