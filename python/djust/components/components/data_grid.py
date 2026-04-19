"""Data Grid component for programmatic use in LiveViews."""

import html
from typing import Optional

from djust import Component


class DataGrid(Component):
    """Editable spreadsheet-like data grid with cell editing, column resize,
    and frozen columns.

    Usage in a LiveView::

        self.grid = DataGrid(
            columns=[
                {"key": "name", "label": "Name"},
                {"key": "email", "label": "Email"},
                {"key": "role", "label": "Role", "type": "select",
                 "options": ["Admin", "User", "Guest"]},
            ],
            rows=[
                {"id": "1", "name": "Alice", "email": "alice@ex.com", "role": "Admin"},
                {"id": "2", "name": "Bob", "email": "bob@ex.com", "role": "User"},
            ],
            edit_event="grid_edit",
        )

    In template::

        {{ grid|safe }}

    Args:
        columns: list of dicts with keys: key, label, width (opt),
                 editable (bool, default True), type (text|number|select),
                 options (for select type)
        rows: list of dicts keyed by column keys
        row_key: key field for row identity
        edit_event: dj-click event on cell edit commit
        resizable: enable column resize handles
        frozen_left: columns frozen on the left
        frozen_right: columns frozen on the right
        striped: alternating row backgrounds
        compact: reduced cell padding
        keyboard_nav: enable arrow-key cell navigation
        new_row_event: event for Add Row button
        delete_row_event: event for row deletion
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        columns: Optional[list] = None,
        rows: Optional[list] = None,
        row_key: str = "id",
        edit_event: str = "grid_cell_edit",
        resizable: bool = True,
        frozen_left: int = 0,
        frozen_right: int = 0,
        striped: bool = False,
        compact: bool = False,
        keyboard_nav: bool = True,
        new_row_event: str = "",
        delete_row_event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            columns=columns,
            rows=rows,
            row_key=row_key,
            edit_event=edit_event,
            resizable=resizable,
            frozen_left=frozen_left,
            frozen_right=frozen_right,
            striped=striped,
            compact=compact,
            keyboard_nav=keyboard_nav,
            new_row_event=new_row_event,
            delete_row_event=delete_row_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.columns = columns or []
        self.rows = rows or []
        self.row_key = row_key
        self.edit_event = edit_event
        self.resizable = resizable
        self.frozen_left = frozen_left
        self.frozen_right = frozen_right
        self.striped = striped
        self.compact = compact
        self.keyboard_nav = keyboard_nav
        self.new_row_event = new_row_event
        self.delete_row_event = delete_row_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the data grid HTML."""
        e_edit_event = html.escape(self.edit_event)

        wrapper_cls = "data-grid-wrapper"
        if self.striped:
            wrapper_cls += " data-grid-striped"
        if self.compact:
            wrapper_cls += " data-grid-compact"
        if self.custom_class:
            wrapper_cls += f" {html.escape(self.custom_class)}"

        # Header
        header_cells = []
        for idx, col in enumerate(self.columns):
            if not isinstance(col, dict):
                continue
            col_key = html.escape(str(col.get("key", "")))
            col_label = html.escape(str(col.get("label", col.get("key", ""))))
            width = col.get("width", "")
            style = f' style="width:{html.escape(str(width))}"' if width else ""
            frozen_cls = ""
            if idx < self.frozen_left:
                frozen_cls = " data-grid-frozen-left"
            elif self.frozen_right and idx >= len(self.columns) - self.frozen_right:
                frozen_cls = " data-grid-frozen-right"
            header_cells.append(
                f'<th class="data-grid-header-cell{frozen_cls}" '
                f'data-col-key="{col_key}"{style}>{col_label}</th>'
            )

        if self.delete_row_event:
            header_cells.append('<th class="data-grid-header-cell data-grid-actions-col"></th>')

        # Body rows
        body_rows = []
        for row in self.rows:
            if not isinstance(row, dict):
                continue
            rk = html.escape(str(row.get(self.row_key, "")))
            cells = []
            for idx, col in enumerate(self.columns):
                if not isinstance(col, dict):
                    continue
                col_key_raw = str(col.get("key", ""))
                col_key = html.escape(col_key_raw)
                cell_val = html.escape(str(row.get(col_key_raw, "")))
                editable = col.get("editable", True)
                frozen_cls = ""
                if idx < self.frozen_left:
                    frozen_cls = " data-grid-frozen-left"
                elif self.frozen_right and idx >= len(self.columns) - self.frozen_right:
                    frozen_cls = " data-grid-frozen-right"
                edit_attr = ' data-editable="true"' if editable else ""
                cells.append(
                    f'<td class="data-grid-cell{frozen_cls}" '
                    f'data-col-key="{col_key}" tabindex="-1"{edit_attr}>'
                    f"{cell_val}</td>"
                )

            if self.delete_row_event:
                cells.append(
                    f'<td class="data-grid-cell data-grid-actions-col">'
                    f'<button class="data-grid-delete-btn" '
                    f'dj-click="{html.escape(self.delete_row_event)}" '
                    f'data-value="{rk}">&times;</button>'
                    f"</td>"
                )

            body_rows.append(f'<tr class="data-grid-row" data-row-key="{rk}">{"".join(cells)}</tr>')

        return (
            f'<div class="{wrapper_cls}" data-edit-event="{e_edit_event}">'
            f'<div class="data-grid-scroll">'
            f'<table class="data-grid" role="grid">'
            f"<thead><tr>{''.join(header_cells)}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            f"</table>"
            f"</div>"
            f"</div>"
        )
