"""Sortable List component — drag-and-drop reorderable list."""

import html
from typing import Any, Optional

from djust import Component
from djust.components.utils import unique_ids as _unique_ids


class SortableList(Component):
    """Drag-and-drop reorderable list.

    Uses ``dj-hook="SortableList"`` for client-side drag interactions; the
    page must include ``djust_components/sortable-list.js``. Items reorder by
    mouse drag, or by keyboard (Space grabs the focused item, the arrow keys
    move it, Enter drops it, Escape cancels). Fires ``move_event`` with
    ``order`` (the item ids in their new order) on drop. An app's own
    ``SortableList`` hook, in ``window.djust.hooks`` or ``window.DjustHooks``,
    replaces the shipped one (a hook assigned with ``window.DjustHooks = {...}``
    after the script also drops it; merge with ``Object.assign`` instead).

    ``order`` comes from the browser, so treat it as untrusted: accept it only
    if it is a permutation of the ids you rendered, and ignore it otherwise.
    A handler that merely filters it deletes data when a client sends a
    missing or repeated id::

        @event_handler()
        def reorder(self, order=None, **kwargs):
            by_id = {str(i["id"]): i for i in self.items}
            if not isinstance(order, list) or sorted(map(str, order)) != sorted(by_id):
                return
            self.items = [by_id[str(key)] for key in order]

    Use one ``move_event`` per list: the payload does not say which list sent
    it. Items need distinct, non-empty ``id`` values: the server's re-render
    can only follow the client's reorder when it can key the items, so a list
    where any ``id`` is empty, missing or repeated renders without ``data-key``
    and the hook leaves it inert (no drag, no keyboard reorder), logging one
    console warning.

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
        **kwargs: Any,
    ) -> None:
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

        keyed = _unique_ids(self.items)
        items_html = []
        for item in self.items:
            if not isinstance(item, dict):
                continue
            item_id = html.escape(str(item.get("id", "")))
            key_attr = f' data-key="{item_id}"' if keyed else ""
            label = html.escape(str(item.get("label", "")))
            handle_html = (
                '<span class="dj-sortable-list__handle" aria-hidden="true">&#x2630;</span> '
                if self.handle
                else ""
            )
            drag_attr = ' draggable="true"' if not self.disabled else ""
            items_html.append(
                f'<li class="dj-sortable-list__item" data-id="{item_id}"{key_attr}{drag_attr} '
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
