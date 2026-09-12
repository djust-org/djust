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
                bordered=True,
                filterable=True,  # one input matching any column (#2782)
            )
            # {'key': 'name', 'label': 'Name', 'filterable': True} adds a
            # per-column filter input under that header.

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
        # Additive CSS hooks keep framework defaults while allowing utilities
        # such as Bootstrap's align-middle/table-dark and caption-top.
        self.table_class = str(kwargs.get("table_class") or "")
        self.thead_class = str(kwargs.get("thead_class") or "")
        self.tbody_class = str(kwargs.get("tbody_class") or "")
        self.tfoot_class = str(kwargs.get("tfoot_class") or "")
        self.caption_class = str(kwargs.get("caption_class") or "")
        self.caption = kwargs.get("caption")
        self.footer = kwargs.get("footer")  # Optional mapping keyed like a row
        self.sort_column = kwargs.get("sort_column", None)
        self.sort_direction = kwargs.get("sort_direction", "asc")  # asc, desc
        self.selectable = kwargs.get("selectable", False)
        # Row identity mirrors ``{% data_table %}`` / ``DataTableMixin``: the
        # row's ``row_key`` value (default ``"id"``), compared as a string
        # (``rust_handlers.py`` builds ``{str(v) for v in selected_rows}``).
        self.row_key = kwargs.get("row_key", "id")
        self.selected_rows = [str(v) for v in kwargs.get("selected_rows", [])]
        # Filtering (#2782) mirrors ``DataTableMixin``: one global query that
        # matches ANY column case-insensitively (``on_table_search`` /
        # ``_apply_table_search``, ``mixins/data_table.py``) and per-column
        # filters keyed by column (``on_table_filter`` / ``_apply_table_filters``,
        # an empty value removes the key). ``filterable=True`` renders the global
        # input; ``{"key": ..., "filterable": True}`` on a column renders that
        # column's input.
        self.filterable = kwargs.get("filterable", False)
        self.filter_query = str(kwargs.get("filter_query", "") or "")
        self.column_filters: Dict[str, str] = {
            str(k): str(v) for k, v in (kwargs.get("column_filters") or {}).items() if v
        }

    def get_context(self) -> Dict[str, Any]:
        """Get table context"""
        return {
            "columns": self.columns,
            "rows": self.rows,
            "striped": self.striped,
            "bordered": self.bordered,
            "hoverable": self.hoverable,
            "compact": self.compact,
            "table_class": self.table_class,
            "thead_class": self.thead_class,
            "tbody_class": self.tbody_class,
            "tfoot_class": self.tfoot_class,
            "caption_class": self.caption_class,
            "caption": self.caption,
            "footer": self.footer,
            "sort_column": self.sort_column,
            "sort_direction": self.sort_direction,
            "selectable": self.selectable,
            "selected_rows": self.selected_rows,
            "filterable": self.filterable,
            "filter_query": self.filter_query,
            "column_filters": self.column_filters,
        }

    # ------------------------------------------------------------------
    # Filtering (#2782)
    # ------------------------------------------------------------------

    def _has_column_filters(self) -> bool:
        return any(col.get("filterable", False) for col in self.columns)

    def _matches(self, row: Dict[str, Any]) -> bool:
        """Case-insensitive substring match — the ``icontains`` rule
        ``DataTableMixin`` applies (``_apply_table_search`` /
        ``_apply_table_filters``). The global query matches when ANY column's
        string value contains it; a column filter must match THAT column."""
        query = self.filter_query.lower()
        if query and not any(query in str(row.get(col["key"], "")).lower() for col in self.columns):
            return False
        for key, needle in self.column_filters.items():
            if needle.lower() not in str(row.get(key, "")).lower():
                return False
        return True

    def _visible_rows(self) -> List[Dict[str, Any]]:
        """The rows the table renders: ``rows`` filtered, then sorted — the
        ``search -> filter -> sort`` order of ``DataTableMixin.refresh_table``.
        ``rows`` itself is never narrowed, so clearing a filter restores them."""
        visible = [row for row in self.rows if self._matches(row)]
        if self.sort_column:
            visible = sorted(
                visible,
                key=lambda x: x.get(self.sort_column, ""),
                reverse=self.sort_direction == "desc",
            )
        return visible

    def _global_filter_attr(self) -> str:
        """Routing half of the global filter input: ``dj-input="filter_rows"``
        with ``data-component-id`` (dispatched to THIS component — the #2776
        lesson) and ``dj-debounce`` as ``{% data_table %}`` renders its search
        box (``rust_handlers.py`` ``dj-debounce="{search_debounce}"``, 300ms).
        The client sends the input's text as ``value``."""
        return (
            f'role="searchbox" aria-label="Search table" placeholder="Search..." '
            f'value="{escape(self.filter_query)}" '
            f'dj-input="filter_rows" dj-debounce="300" data-component-id="{self.component_id}"'
        )

    def _column_filter_attr(self, col: Dict[str, Any]) -> str:
        """Routing half of a column filter input: ``dj-input="filter_column"``
        with ``data-component-id`` and ``data-column`` carrying the key the
        handler takes as ``column`` (the ``{% data_table %}`` filter-row shape)."""
        key = col["key"]
        return (
            f'aria-label="Filter {escape(str(col.get("label", key)))}" placeholder="Filter..." '
            f'value="{escape(self.column_filters.get(key, ""))}" '
            f'dj-input="filter_column" dj-debounce="300" '
            f'data-component-id="{self.component_id}" data-column="{escape(str(key))}"'
        )

    @event_handler()
    def filter_rows(self, value: str = "", **kwargs: Any) -> None:
        """Set the global filter query (#2782): rows whose ANY column contains
        ``value`` case-insensitively stay visible; an empty value shows every
        row. ``value`` is what ``dj-input`` sends — the same parameter
        ``DataTableMixin.on_table_search`` takes."""
        self.filter_query = str(value or "")
        self.trigger_update()

    @event_handler()
    def filter_column(self, value: str = "", column: str = "", **kwargs: Any) -> None:
        """Set one column's filter (#2782): ``column`` is the input's
        ``data-column``; an empty ``value`` removes the filter, as
        ``DataTableMixin.on_table_filter`` pops the key."""
        column = str(column)
        value = str(value or "")
        if value:
            self.column_filters = {**self.column_filters, column: value}
        else:
            self.column_filters = {k: v for k, v in self.column_filters.items() if k != column}
        self.trigger_update()

    # ------------------------------------------------------------------
    # Row selection (#2779)
    # ------------------------------------------------------------------

    def _row_id(self, row: Dict[str, Any]) -> str:
        """The identity a row's checkbox carries and ``selected_rows`` stores."""
        return str(row.get(self.row_key, ""))

    def _row_ids(self) -> List[str]:
        """Identities of the VISIBLE rows (after any filter, #2782)."""
        return [self._row_id(row) for row in self._visible_rows()]

    def _all_selected(self) -> bool:
        ids = self._row_ids()
        return bool(ids) and all(rid in self.selected_rows for rid in ids)

    def _row_checkbox_attr(self, row: Dict[str, Any]) -> str:
        """Routing half of a row checkbox: ``dj-change="toggle_row"`` paired with
        ``data-component-id`` (so the change is dispatched to THIS component,
        not the parent view — the #2776 lesson) and ``dj-value-row-id`` carrying
        the identity the handler takes as ``row_id``.

        ``dj-value-*``, NOT ``data-*`` (#2781): the client's form-event path
        (``buildFormEventParams``, 09-event-binding.js) sends ``value``,
        ``field``, ``component_id`` and the element's ``dj-value-*`` — it never
        reads ``data-*``; only ``dj-click`` does (``extractTypedParams``). The
        first cut carried ``data-row-id``, so every row click reached
        ``toggle_row`` as ``row_id=""`` and toggled the empty string: one row
        could never stay selected, and the header never derived a full set.
        Same convention as the package's other form-event controls
        (``templatetags/_forms.py``: ``dj-change=… dj-value-column=…``).
        """
        row_id = self._row_id(row)
        checked = " checked" if row_id in self.selected_rows else ""
        return (
            f'dj-change="toggle_row" data-component-id="{self.component_id}" '
            f'dj-value-row-id="{escape(row_id)}" aria-label="Select row"{checked}'
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

        ``row_id`` is what the checkbox's ``dj-value-row-id`` carries — the row's
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
        """Header checkbox: select every VISIBLE row, or clear the selection
        when every visible row is already selected (#2779). Under a filter
        (#2782) only the filtered-in rows are selected — the
        ``DataTableMixin.on_table_select`` ``__all__`` contract, which selects
        ``table_rows`` (the post-filter set). A hidden row that was selected
        before the filter stays selected; clearing removes every id."""
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

    def _filter_row(self, input_open: str, th_class: str = "") -> str:
        """The second ``<thead>`` row of per-column filter inputs — the
        ``{% data_table %}`` shape (``rust_handlers.py`` ``filter_cells``): one
        cell per column, an input only under a ``filterable`` column, an empty
        cell under the checkbox column. Nothing when no column is filterable."""
        if not self._has_column_filters():
            return ""
        th_open = f'<th class="{th_class}">' if th_class else "<th>"
        cells = [f"{th_open}</th>"] if self.selectable else []
        for col in self.columns:
            if col.get("filterable", False):
                cells.append(f"{th_open}{input_open}{self._column_filter_attr(col)}></th>")
            else:
                cells.append(f"{th_open}</th>")
        return f'<tr class="dj-table-filters">{"".join(cells)}</tr>'

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

    @staticmethod
    def _section_open(tag: str, custom_class: str, default_class: str = "") -> str:
        classes = " ".join(part for part in (default_class, custom_class) if part)
        return f'<{tag} class="{escape(classes)}">' if classes else f"<{tag}>"

    def _table_open(self, default_class: str) -> str:
        html = self._section_open("table", self.table_class, default_class)
        if self.caption is not None:
            html += self._section_open("caption", self.caption_class)
            html += f"{escape(self.caption)}</caption>"
        return html

    def _render_footer(self, cell_class: str = "") -> str:
        if self.footer is None:
            return ""
        cell = self._section_open("td", cell_class)
        cells = [f"{cell}</td>"] if self.selectable else []
        cells.extend(
            f"{cell}{escape(self.footer.get(col['key'], ''))}</td>" for col in self.columns
        )
        return (
            self._section_open("tfoot", self.tfoot_class)
            + "<tr>"
            + "".join(cells)
            + "</tr></tfoot>"
        )

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
        if self.filterable:
            html += (
                f'<div class="dj-table-filter mb-2"><input type="text" class="form-control" '
                f"{self._global_filter_attr()}></div>"
            )
        html += self._table_open(table_class)

        # Header
        html += self._section_open("thead", self.thead_class) + "<tr>"

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

        html += "</tr>"
        html += self._filter_row('<input type="text" class="form-control form-control-sm" ')
        html += "</thead>"

        # Body
        html += self._section_open("tbody", self.tbody_class)

        for row in self._visible_rows():
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
        html += self._render_footer()
        html += "</table></div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS table"""
        html = f'<div class="overflow-x-auto" id="{self.component_id}">'
        if self.filterable:
            html += (
                f'<div class="dj-table-filter mb-2"><input type="text" '
                f'class="block w-full rounded-md border-gray-300 shadow-sm text-sm" '
                f"{self._global_filter_attr()}></div>"
            )
        html += self._table_open("min-w-full divide-y divide-gray-200")

        # Header
        html += self._section_open("thead", self.thead_class, "bg-gray-50")
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

        html += "</tr>"
        html += self._filter_row(
            '<input type="text" class="block w-full rounded-md border-gray-300 shadow-sm text-sm" ',
            th_class="px-6 py-2",
        )
        html += "</thead>"

        # Body
        body_class = "bg-white divide-y divide-gray-200"
        if self.striped:
            body_class = "divide-y divide-gray-200"

        html += self._section_open("tbody", self.tbody_class, body_class)

        for idx, row in enumerate(self._visible_rows()):
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
        html += self._render_footer("px-6 py-4")
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

        html = f'<div class="dj-table" id="{self.component_id}">'
        if self.filterable:
            html += f'<div class="dj-table-filter"><input type="text" {self._global_filter_attr()}></div>'
        html += self._table_open(table_class)

        # Header
        html += self._section_open("thead", self.thead_class) + "<tr>"

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

        html += "</tr>"
        html += self._filter_row('<input type="text" ')
        html += "</thead>"

        # Body
        html += self._section_open("tbody", self.tbody_class)

        for row in self._visible_rows():
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
        html += self._render_footer()
        html += "</table></div>"
        return html
