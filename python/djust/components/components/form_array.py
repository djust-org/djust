"""Form array component for dynamic add/remove form rows."""

import html

from djust import Component


class FormArray(Component):
    """Style-agnostic form array component.

    Dynamic add/remove form rows with min/max constraints.

    Usage in a LiveView::

        self.items = FormArray(
            name="items",
            rows=[{"value": "Item 1"}, {"value": "Item 2"}],
            min=1,
            max=10,
            add_event="add_row",
            remove_event="remove_row",
        )

    In template::

        {{ items|safe }}

    Args:
        name: Field name prefix
        rows: List of row dicts with values
        min: Minimum number of rows (default: 1)
        max: Maximum number of rows (default: 10)
        add_event: djust event for adding a row
        remove_event: djust event for removing a row
        add_label: Add button text
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        name: str = "items",
        rows: list = None,
        min: int = 1,
        max: int = 10,
        add_event: str = "add_row",
        remove_event: str = "remove_row",
        add_label: str = "Add row",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            rows=rows,
            min=min,
            max=max,
            add_event=add_event,
            remove_event=remove_event,
            add_label=add_label,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.rows = rows if rows is not None else [{"value": ""}]
        self.min_rows = min
        self.max_rows = max
        self.add_event = add_event
        self.remove_event = remove_event
        self.add_label = add_label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-form-array"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_name = html.escape(self.name)
        e_add_event = html.escape(self.add_event)
        e_remove_event = html.escape(self.remove_event)
        e_add_label = html.escape(self.add_label)

        row_count = len(self.rows)
        can_add = row_count < self.max_rows
        can_remove = row_count > self.min_rows

        rows_html = []
        for i, row in enumerate(self.rows):
            val = html.escape(str(row.get("value", "")))
            remove_html = ""
            if can_remove:
                remove_html = (
                    f'<button class="dj-form-array__remove" type="button" '
                    f'dj-click="{e_remove_event}" data-value="{i}" '
                    f'aria-label="Remove row {i + 1}">&times;</button>'
                )
            rows_html.append(
                f'<div class="dj-form-array__row" data-index="{i}">'
                f'<input type="text" name="{e_name}[{i}]" value="{val}" '
                f'class="dj-form-array__input">'
                f"{remove_html}</div>"
            )

        add_disabled = "" if can_add else " disabled"
        add_html = (
            f'<button class="dj-form-array__add" type="button" '
            f'dj-click="{e_add_event}"{add_disabled}>'
            f"{e_add_label}</button>"
        )

        return (
            f'<div class="{class_str}" data-min="{self.min_rows}" '
            f'data-max="{self.max_rows}">'
            f'<div class="dj-form-array__rows">{"".join(rows_html)}</div>'
            f"{add_html}</div>"
        )
