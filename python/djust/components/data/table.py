"""
Table component for djust.

Provides data tables with sorting, selection, and actions.
"""

from typing import Dict, Any
from ..base import LiveComponent


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

    def mount(self, **kwargs):
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
        self.selected_rows = kwargs.get("selected_rows", [])

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
        }

    def sort_by(self, column_key: str):
        """Sort table by column"""
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

    def render(self) -> str:
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
            html += '<th><input type="checkbox" class="form-check-input"></th>'

        for col in self.columns:
            key = col["key"]
            label = col["label"]
            sortable = col.get("sortable", False)

            if sortable:
                sort_icon = ""
                if self.sort_column == key:
                    sort_icon = " ▲" if self.sort_direction == "asc" else " ▼"

                html += f'<th style="cursor: pointer" dj-click="sort_by" data-column="{key}">{label}{sort_icon}</th>'
            else:
                html += f"<th>{label}</th>"

        html += "</tr></thead>"

        # Body
        html += "<tbody>"

        for row in self.rows:
            html += "<tr>"

            if self.selectable:
                html += '<td><input type="checkbox" class="form-check-input"></td>'

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
            html += '<th class="px-6 py-3 text-left"><input type="checkbox" class="rounded border-gray-300"></th>'

        for col in self.columns:
            key = col["key"]
            label = col["label"]
            sortable = col.get("sortable", False)

            th_class = (
                "px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
            )

            if sortable:
                sort_icon = ""
                if self.sort_column == key:
                    sort_icon = " ▲" if self.sort_direction == "asc" else " ▼"

                html += f'<th class="{th_class} cursor-pointer" dj-click="sort_by" data-column="{key}">{label}{sort_icon}</th>'
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
                html += '<td class="px-6 py-4"><input type="checkbox" class="rounded border-gray-300"></td>'

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
            html += '<th><input type="checkbox"></th>'

        for col in self.columns:
            key = col["key"]
            label = col["label"]
            sortable = col.get("sortable", False)

            if sortable:
                sort_icon = ""
                if self.sort_column == key:
                    sort_icon = " ▲" if self.sort_direction == "asc" else " ▼"

                html += f'<th dj-click="sort_by" data-column="{key}">{label}{sort_icon}</th>'
            else:
                html += f"<th>{label}</th>"

        html += "</tr></thead>"

        # Body
        html += "<tbody>"

        for row in self.rows:
            html += "<tr>"

            if self.selectable:
                html += '<td><input type="checkbox"></td>'

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
