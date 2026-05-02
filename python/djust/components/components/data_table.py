"""DataTable component."""

import html

from djust import Component


class DataTable(Component):
    """Data table component for tabular data display.

    Args:
        columns: list of dicts with keys: key, label
        rows: list of dicts keyed by column keys
        sort_by: column key to sort by
        sort_desc: sort descending
        sort_event: dj-click event for sorting
        striped: alternating row backgrounds
        compact: reduced padding"""

    def __init__(
        self,
        columns: list = None,
        rows: list = None,
        sort_by: str = "",
        sort_desc: bool = False,
        sort_event: str = "on_table_sort",
        striped: bool = False,
        compact: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            columns=columns,
            rows=rows,
            sort_by=sort_by,
            sort_desc=sort_desc,
            sort_event=sort_event,
            striped=striped,
            compact=compact,
            custom_class=custom_class,
            **kwargs,
        )
        self.columns = columns or []
        self.rows = rows or []
        self.sort_by = sort_by
        self.sort_desc = sort_desc
        self.sort_event = sort_event
        self.striped = striped
        self.compact = compact
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the datatable HTML."""
        columns = self.columns or []
        rows = self.rows or []
        cls = "dj-data-table"
        if self.striped:
            cls += " dj-data-table--striped"
        if self.compact:
            cls += " dj-data-table--compact"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_sort = html.escape(self.sort_event)
        # Header
        headers = ""
        for col in columns:
            if not isinstance(col, dict):
                continue
            key = html.escape(str(col.get("key", "")))
            label = html.escape(str(col.get("label", col.get("key", ""))))
            sort_cls = (
                " dj-data-table__th--sorted" if str(col.get("key", "")) == self.sort_by else ""
            )
            headers += f'<th class="dj-data-table__th{sort_cls}" dj-click="{e_sort}" data-value="{key}">{label}</th>'
        # Body
        body_rows = ""
        for row in rows:
            if not isinstance(row, dict):
                continue
            cells = ""
            for col in columns:
                if not isinstance(col, dict):
                    continue
                val = html.escape(str(row.get(col.get("key", ""), "")))
                cells += f'<td class="dj-data-table__td">{val}</td>'
            body_rows += f'<tr class="dj-data-table__tr">{cells}</tr>'
        return (
            f'<div class="{cls}">'
            f'<table class="dj-data-table__table">'
            f"<thead><tr>{headers}</tr></thead>"
            f"<tbody>{body_rows}</tbody>"
            f"</table></div>"
        )
