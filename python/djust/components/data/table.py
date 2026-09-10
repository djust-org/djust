"""
Table component for djust.

Provides data tables with sorting, selection, and actions.
"""

from typing import Any, Dict, List

from django.utils.html import escape
from django.utils.safestring import SafeString

from ...decorators import event_handler
from ..base import LiveComponent
from ..icons import render_icon


class TableComponent(LiveComponent):
    """
    Data table component with rich features.

    Displays tabular data with optional sorting, row selection, and actions.

    Usage:
        from djust.components import TableComponent

        # In your LiveView:
        def mount(self, request):
            self.users_table = TableComponent(
                columns=[
                    {'key': 'id', 'label': 'ID', 'sortable': True},
                    {'key': 'name', 'label': 'Name', 'sortable': True},
                    {'key': 'email', 'label': 'Email'},
                    {'key': 'status', 'label': 'Status', 'badge': True},
                ],
                rows=[
                    {'id': 1, 'name': 'John Doe', 'email': 'john@example.com', 'status': 'active'},
                    {'id': 2, 'name': 'Jane Smith', 'email': 'jane@example.com', 'status': 'inactive'},
                ],
                striped=True,
                hoverable=True,
                bordered=True
            )

        # In template:
        {{ users_table.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs: Any) -> None:
        """Initialize table state"""
        self.columns = kwargs.get("columns", [])  # List of {key, label, sortable, badge, action}
        self.rows = kwargs.get("rows", [])  # List of dicts with column keys
        self.striped = kwargs.get("striped", False)
        self.bordered = kwargs.get("bordered", False)
        self.hoverable = kwargs.get("hoverable", True)
        self.compact = kwargs.get("compact", False)
        self.sort_column = kwargs.get("sort_column", None)
        self.sort_direction = kwargs.get("sort_direction", "asc")  # asc, desc
        self.selectable = kwargs.get("selectable", False)
        # Row identity mirrors ``{% data_table %}`` / ``DataTableMixin``: the
        # row's ``row_key`` value (default ``"id"``), compared as a string
        # (``rust_handlers.py`` builds ``{str(v) for v in selected_rows}``).
        self.row_key = kwargs.get("row_key", "id")
        self.selected_rows = [str(v) for v in kwargs.get("selected_rows", [])]

    def get_context(self) -> Dict[str, Any]:
        """Get table context"""
        return {
            "columns": self.columns,
            "rows": self.rows,
            "striped": self.striped,
            "bordered": self.bordered,
            "hoverable": self.hoverable,
            "compact": self.compact,
            "sort_column": self.sort_column,
            "sort_direction": self.sort_direction,
            "selectable": self.selectable,
            "selected_rows": self.selected_rows,
        }

    # ------------------------------------------------------------------
    # Row selection (#2779)
    # ------------------------------------------------------------------

    def _row_id(self, row: Dict[str, Any]) -> str:
        """The identity a row's checkbox carries and ``selected_rows`` stores."""
        return str(row.get(self.row_key, ""))

    def _row_ids(self) -> List[str]:
        return [self._row_id(row) for row in self.rows]

    def _all_selected(self) -> bool:
        ids = self._row_ids()
        return bool(ids) and all(rid in self.selected_rows for rid in ids)

    def _row_checkbox_attr(self, row: Dict[str, Any]) -> str:
        """Routing half of a row checkbox: ``dj-change="toggle_row"`` paired with
        ``data-component-id`` (so the change is dispatched to THIS component,
        not the parent view — the #2776 lesson) and ``data-row-id`` carrying the
        identity the handler takes as ``row_id``."""
        row_id = self._row_id(row)
        checked = " checked" if row_id in self.selected_rows else ""
        return (
            f'dj-change="toggle_row" data-component-id="{self.component_id}" '
            f'data-row-id="{escape(row_id)}" aria-label="Select row"{checked}'
        )

    def _header_checkbox_attr(self) -> str:
        checked = " checked" if self._all_selected() else ""
        return (
            f'dj-change="toggle_all" data-component-id="{self.component_id}" '
            f'aria-label="Select all rows"{checked}'
        )

    @event_handler()
    def toggle_row(self, row_id: str = "", **kwargs: Any) -> None:
        """Toggle one row's membership in ``selected_rows`` (#2779).

        ``row_id`` is what the checkbox's ``data-row-id`` carries — the row's
        ``row_key`` value as a string. The checkbox's own ``value`` (its checked
        state) arrives in ``kwargs`` and is ignored: the server state is the
        truth, so a stale client cannot desynchronise it.
        """
        row_id = str(row_id)
        if row_id in self.selected_rows:
            self.selected_rows = [r for r in self.selected_rows if r != row_id]
        else:
            self.selected_rows = [*self.selected_rows, row_id]
        self.trigger_update()

    @event_handler()
    def toggle_all(self, **kwargs: Any) -> None:
        """Header checkbox: select every row, or clear the selection when every
        row is already selected (#2779). Mirrors ``DataTableMixin.on_table_select``
        for ``__all__``."""
        if self._all_selected():
            self.selected_rows = []
        else:
            self.selected_rows = self._row_ids()
        self.trigger_update()

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def _aria_sort(self, key: str) -> str:
        """``aria-sort`` for a sortable ``<th>`` (#2778): ``ascending`` /
        ``descending`` when the column is the active sort, else ``none``."""
        if self.sort_column != key:
            return "none"
        return "ascending" if self.sort_direction == "asc" else "descending"

    def _sort_icon(self, key: str, framework: str) -> str:
        """The visual sort affordance (#2778) — a neutral "sortable" mark on an
        unsorted column, an up/down mark on the active one — in each branch's
        existing icon convention: Bootstrap Icons classes on ``bootstrap5``
        (as ``BreadcrumbComponent`` / ``IconComponent`` already emit), the
        vendored heroicons SVG on ``tailwind`` (``djust.components.icons``),
        and unicode on ``plain``. Marked ``aria-hidden``: the accessible state
        is the ``<th>``'s ``aria-sort``."""
        state = self._aria_sort(key)
        if framework == "bootstrap5":
            name = {
                "none": "arrow-down-up",
                "ascending": "caret-up-fill",
                "descending": "caret-down-fill",
            }[state]
            return f' <i class="bi bi-{name} dj-table-sort-icon" aria-hidden="true"></i>'
        if framework == "tailwind":
            name = {
                "none": "arrows-up-down",
                "ascending": "arrow-up",
                "descending": "arrow-down",
            }[state]
            return " " + render_icon(name, size="xs", custom_class="inline dj-table-sort-icon")
        glyph = {"none": "⇅", "ascending": "▲", "descending": "▼"}[state]
        return f' <span class="dj-table-sort-icon" aria-hidden="true">{glyph}</span>'

    def _sort_attr(self, key: str) -> str:
        """The routing half of a sortable header: ``dj-click="sort_by"`` paired
        with ``data-component-id`` so the click is dispatched to THIS component
        rather than the parent view (#2776 — the parent has no ``sort_by``, so
        without it the click raised ``No handler found for event: sort_by``),
        and ``data-column`` carrying the key the handler takes as ``column``."""
        return f'dj-click="sort_by" data-component-id="{self.component_id}" data-column="{key}"'

    @event_handler()
    def sort_by(self, column: str = "", **kwargs: Any) -> None:
        """Sort table by column.

        The handler every framework branch's sortable header targets. Decorated
        because ``event_security`` defaults to strict (#2776); the parameter is
        named ``column`` because the header sends ``data-column``.
        """
        column_key = column
        if self.sort_column == column_key:
            # Toggle direction
            self.sort_direction = "desc" if self.sort_direction == "asc" else "asc"
        else:
            self.sort_column = column_key
            self.sort_direction = "asc"

        # Sort rows
        reverse = self.sort_direction == "desc"
        self.rows = sorted(self.rows, key=lambda x: x.get(column_key, ""), reverse=reverse)
        self.trigger_update()

    def render(self) -> SafeString:
        """Render table with inline HTML"""
        from django.utils.safestring import mark_safe
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 table"""
        classes = ["table"]
        if self.striped:
            classes.append("table-striped")
        if self.bordered:
            classes.append("table-bordered")
        if self.hoverable:
            classes.append("table-hover")
        if self.compact:
            classes.append("table-sm")

        table_class = " ".join(classes)

        html = f'<div class="table-responsive" id="{self.component_id}">'
        html += f'<table class="{table_class}">'

        # Header
        html += "<thead><tr>"

        if self.selectable:
            html += f'<th><input type="checkbox" class="form-check-input" {self._header_checkbox_attr()}></th>'

        for col in self.columns:
            key = col["key"]
            label = col["label"]
            sortable = col.get("sortable", False)

            if sortable:
                sort_icon = self._sort_icon(key, "bootstrap5")
                html += (
                    f'<th style="cursor: pointer" aria-sort="{self._aria_sort(key)}" '
                    f"{self._sort_attr(key)}>{label}{sort_icon}</th>"
                )
            else:
                html += f"<th>{label}</th>"

        html += "</tr></thead>"

        # Body
        html += "<tbody>"

        for row in self.rows:
            html += "<tr>"

            if self.selectable:
                html += f'<td><input type="checkbox" class="form-check-input" {self._row_checkbox_attr(row)}></td>'

            for col in self.columns:
                key = col["key"]
                value = row.get(key, "")

                # Badge rendering
                if col.get("badge"):
                    badge_variant = "success" if value == "active" else "secondary"
                    value = f'<span class="badge bg-{badge_variant}">{value}</span>'

                html += f"<td>{value}</td>"

            html += "</tr>"

        html += "</tbody>"
        html += "</table></div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS table"""
        html = f'<div class="overflow-x-auto" id="{self.component_id}">'
        html += '<table class="min-w-full divide-y divide-gray-200">'

        # Header
        html += '<thead class="bg-gray-50">'
        html += "<tr>"

        if self.selectable:
            html += f'<th class="px-6 py-3 text-left"><input type="checkbox" class="rounded border-gray-300" {self._header_checkbox_attr()}></th>'

        for col in self.columns:
            key = col["key"]
            label = col["label"]
            sortable = col.get("sortable", False)

            th_class = (
                "px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
            )

            if sortable:
                sort_icon = self._sort_icon(key, "tailwind")
                html += (
                    f'<th class="{th_class} cursor-pointer" aria-sort="{self._aria_sort(key)}" '
                    f"{self._sort_attr(key)}>{label}{sort_icon}</th>"
                )
            else:
                html += f'<th class="{th_class}">{label}</th>'

        html += "</tr></thead>"

        # Body
        body_class = "bg-white divide-y divide-gray-200"
        if self.striped:
            body_class = "divide-y divide-gray-200"

        html += f'<tbody class="{body_class}">'

        for idx, row in enumerate(self.rows):
            row_class = ""
            if self.striped and idx % 2 == 1:
                row_class = "bg-gray-50"
            if self.hoverable:
                row_class += " hover:bg-gray-100"

            html += f'<tr class="{row_class}">'

            if self.selectable:
                html += f'<td class="px-6 py-4"><input type="checkbox" class="rounded border-gray-300" {self._row_checkbox_attr(row)}></td>'

            for col in self.columns:
                key = col["key"]
                value = row.get(key, "")

                # Badge rendering
                if col.get("badge"):
                    badge_variant = (
                        "bg-green-100 text-green-800"
                        if value == "active"
                        else "bg-gray-100 text-gray-800"
                    )
                    value = f'<span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full {badge_variant}">{value}</span>'

                html += (
                    f'<td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{value}</td>'
                )

            html += "</tr>"

        html += "</tbody>"
        html += "</table></div>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML table"""
        classes = ["table"]
        if self.striped:
            classes.append("table-striped")
        if self.bordered:
            classes.append("table-bordered")

        table_class = " ".join(classes)

        html = f'<table class="{table_class}" id="{self.component_id}">'

        # Header
        html += "<thead><tr>"

        if self.selectable:
            html += f'<th><input type="checkbox" {self._header_checkbox_attr()}></th>'

        for col in self.columns:
            key = col["key"]
            label = col["label"]
            sortable = col.get("sortable", False)

            if sortable:
                sort_icon = self._sort_icon(key, "plain")
                html += f'<th aria-sort="{self._aria_sort(key)}" {self._sort_attr(key)}>{label}{sort_icon}</th>'
            else:
                html += f"<th>{label}</th>"

        html += "</tr></thead>"

        # Body
        html += "<tbody>"

        for row in self.rows:
            html += "<tr>"

            if self.selectable:
                html += f'<td><input type="checkbox" {self._row_checkbox_attr(row)}></td>'

            for col in self.columns:
                key = col["key"]
                value = row.get(key, "")

                # Badge rendering
                if col.get("badge"):
                    value = f'<span class="badge">{value}</span>'

                html += f"<td>{value}</td>"

            html += "</tr>"

        html += "</tbody>"
        html += "</table>"
        return html
