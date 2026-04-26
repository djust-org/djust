"""
DataTableMixin — server-side data table logic for djust LiveViews.

Provides automatic sort, search, filter, select, and pagination event handlers
that pair with the ``{% data_table %}`` template tag and ``DataTableHandler``
Rust handler.

Usage::

    class UserListView(DataTableMixin, LiveView):
        table_model = User
        table_columns = [
            {"key": "username", "label": "Username", "sortable": True, "filterable": True},
            {"key": "email", "label": "Email", "sortable": True},
        ]
        table_page_size = 25
        table_default_sort = "username"
        table_searchable_fields = ["username", "email"]

        def mount(self, **kwargs):
            self.init_table_state()
            self.refresh_table()

        def get_template_context(self):
            ctx = super().get_template_context()
            ctx.update(self.get_table_context())
            return ctx
"""

import csv
import io
import json
import math
import re

from djust.components.utils import format_cell, interpolate_color_gradient
from djust.decorators import event_handler

__all__ = ["DataTableMixin"]


# ---------------------------------------------------------------------------
# Safe arithmetic expression evaluator (replaces eval())
# ---------------------------------------------------------------------------
# Tokeniser and recursive-descent parser that only allows:
#   - numeric literals (int and float)
#   - column-name identifiers (Python identifiers)
#   - binary operators: + - * / %
#   - unary minus
#   - parenthesised sub-expressions

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<NUMBER>[0-9]+(?:\.[0-9]*)?)   # numeric literal
      | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*) # identifier (column name)
      | (?P<OP>[+\-*/%()]))               # operator or paren
    """,
    re.VERBOSE,
)


def _tokenize(expression):
    """Yield (type, value) tokens from *expression*.

    Raises ValueError on illegal characters.
    """
    pos = 0
    for m in _TOKEN_RE.finditer(expression):
        if m.start() != pos:
            # Gap between last match end and this match start → illegal chars
            gap = expression[pos : m.start()].strip()
            if gap:
                raise ValueError(f"Illegal characters in expression: {gap!r}")
        if m.group("NUMBER") is not None:
            yield ("NUMBER", float(m.group("NUMBER")))
        elif m.group("IDENT") is not None:
            yield ("IDENT", m.group("IDENT"))
        elif m.group("OP") is not None:
            yield ("OP", m.group("OP"))
        pos = m.end()
    # Check for trailing illegal chars
    trailing = expression[pos:].strip()
    if trailing:
        raise ValueError(f"Illegal trailing characters: {trailing!r}")
    yield ("EOF", None)


class _Parser:
    """Recursive-descent arithmetic parser.

    Grammar::

        expr   → term (('+' | '-') term)*
        term   → unary (('*' | '/' | '%') unary)*
        unary  → '-' unary | atom
        atom   → NUMBER | IDENT | '(' expr ')'
    """

    def __init__(self, tokens, namespace):
        self.tokens = list(tokens)
        self.namespace = namespace
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos]

    def _advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self):
        result = self._expr()
        if self._peek()[0] != "EOF":
            raise ValueError("Unexpected token after expression")
        return result

    def _expr(self):
        left = self._term()
        while self._peek() == ("OP", "+") or self._peek() == ("OP", "-"):
            op = self._advance()[1]
            right = self._term()
            if op == "+":
                left = left + right
            else:
                left = left - right
        return left

    def _term(self):
        left = self._unary()
        while (
            self._peek() == ("OP", "*")
            or self._peek() == ("OP", "/")
            or self._peek() == ("OP", "%")
        ):
            op = self._advance()[1]
            right = self._unary()
            if op == "*":
                left = left * right
            elif op == "/":
                if right == 0:
                    raise ValueError("Division by zero")
                left = left / right
            else:  # %
                if right == 0:
                    raise ValueError("Modulo by zero")
                left = left % right
        return left

    def _unary(self):
        if self._peek() == ("OP", "-"):
            self._advance()
            return -self._unary()
        return self._atom()

    def _atom(self):
        tok_type, tok_val = self._peek()
        if tok_type == "NUMBER":
            self._advance()
            return tok_val
        if tok_type == "IDENT":
            self._advance()
            if tok_val not in self.namespace:
                raise ValueError(f"Unknown column: {tok_val!r}")
            return self.namespace[tok_val]
        if tok_type == "OP" and tok_val == "(":
            self._advance()  # consume '('
            result = self._expr()
            if self._peek() != ("OP", ")"):
                raise ValueError("Expected closing parenthesis")
            self._advance()  # consume ')'
            return result
        raise ValueError(f"Unexpected token: {tok_type}={tok_val!r}")


def _safe_eval_arithmetic(expression, namespace):
    """Evaluate *expression* using only arithmetic ops and *namespace* lookups.

    This replaces ``eval()`` with a safe recursive-descent parser.
    """
    tokens = _tokenize(expression)
    parser = _Parser(tokens, namespace)
    return parser.parse()


# Minimal context returned by ``DataTableMixin.get_table_context()`` when
# ``init_table_state()`` hasn't been called yet (e.g. during the pre-mount
# ``get_context_data()`` build of the initial Rust VDOM snapshot — see
# #1114). The ``{% data_table %}`` template tag must render this as an
# empty table without raising.
_PRE_MOUNT_TABLE_CONTEXT = {
    "rows": [],
    "columns": [],
    "sort_by": "",
    "sort_desc": False,
    "sort_event": "table_sort",
    "selectable": False,
    "selected_rows": [],
    "select_event": "table_select",
    "row_key": "id",
    "search": False,
    "search_query": "",
    "search_event": "table_search",
    "search_debounce": 300,
    "filters": {},
    "filter_event": "table_filter",
    "loading": False,
    "empty_title": "No data",
    "empty_description": "",
    "empty_icon": "",
    "paginate": False,
    "page": 1,
    "total_pages": 1,
    "page_event": "table_page",
    "striped": False,
    "compact": False,
    # Phase 2-5 keys default to falsy/empty so the template tag's
    # ``{% if %}`` guards short-circuit and no event handlers wire up.
    "editable_columns": [],
    "edit_event": "table_cell_edit",
    "resizable": False,
    "reorderable": False,
    "reorder_event": "table_reorder",
    "frozen_left": 0,
    "frozen_right": 0,
    "column_visibility": False,
    "visibility_event": "table_visibility",
    "density": "comfortable",
    "density_toggle": False,
    "density_event": "table_density",
    "responsive_cards": False,
    "editable_rows": False,
    "edit_row_event": "table_row_edit",
    "save_row_event": "table_row_save",
    "cancel_row_event": "table_row_cancel",
    "editing_rows": [],
    "expandable": False,
    "expand_event": "table_expand",
    "expanded_rows": [],
    "bulk_actions": [],
    "bulk_action_event": "table_bulk_action",
    "exportable": False,
    "export_event": "table_export",
    "export_formats": ["csv", "json"],
    "group_by": "",
    "group_event": "table_group",
    "group_toggle_event": "table_group_toggle",
    "collapsible_groups": True,
    "collapsed_groups": [],
    "keyboard_nav": False,
    "virtual_scroll": False,
    "virtual_row_height": 40,
    "virtual_buffer": 5,
    "server_mode": False,
    "facets": False,
    "facet_counts": {},
    "persist_key": "",
    "printable": False,
    "show_stats": False,
    "column_stats": {},
    "footer_aggregations": {},
    "row_class_map": {},
    "column_groups": [],
    "row_drag": False,
    "row_drag_event": "table_row_drag",
    "copyable": False,
    "copy_event": "table_copy",
    "copy_format": "csv",
    "importable": False,
    "import_event": "table_import",
    "import_formats": ["csv", "json"],
    "import_preview": True,
    "import_preview_data": [],
    "import_errors": [],
    "import_pending": False,
    "computed_columns": [],
    "cell_merge_key": "_merge",
    "column_expressions": {},
    "expression_event": "table_expression",
    "active_expressions": {},
    "conditional_formatting": [],
    "row_order": [],
    "current_group_by": "",
    "column_order": [],
    "visible_columns": [],
    "current_density": "comfortable",
}


class DataTableMixin:
    """Mixin for LiveViews that provides automatic data table event handlers.

    .. note::
       **LiveView vs Component lifecycle (#1114)**

       This mixin was originally designed for the ``Component`` API, where
       ``get_template_context()`` runs only after ``__init__()`` has
       completed. When used with ``LiveView`` (i.e. a class that mixes in
       ``DataTableMixin`` AND ``LiveView``), djust's WebSocket consumer
       calls ``get_context_data()`` BEFORE ``mount()`` runs, to build the
       initial Rust VDOM snapshot. ``init_table_state()`` (typically called
       from ``mount()``) hasn't run yet, so instance attributes like
       ``self.table_rows`` don't exist.

       The mixin handles this automatically via a pre-mount guard in
       ``get_table_context()`` — the first call returns an empty-table
       default; subsequent calls (after ``mount()``) return real state.

       **All ``on_table_*`` event handlers below are decorated with
       ``@event_handler()``** so they work under the default
       ``event_security="strict"`` mode without per-view boilerplate.

       For LiveView use cases that need FK traversal or large datasets,
       prefer passing the queryset directly via ``get_context_data()`` and
       defining ``@event_handler()``-decorated methods on your view —
       djust's JIT serialization handles ORM objects natively without the
       JSON-round-trip cost of ``self.table_rows``.
    """

    # ── Class-level configuration ──
    # Note: mutable defaults (lists/dicts) are set to None here to avoid
    # the shared-mutable-default pitfall.  ``init_table_state()`` resolves
    # None → empty list/dict on the *instance*.
    table_model = None
    table_queryset = None
    table_columns = None
    table_page_size = 25
    table_default_sort = ""
    table_default_sort_desc = False
    table_searchable_fields = None
    table_row_key = "id"
    table_selectable = False

    # Event name configuration (overridable)
    table_sort_event = "table_sort"
    table_search_event = "table_search"
    table_filter_event = "table_filter"
    table_select_event = "table_select"
    table_page_event = "table_page"

    # Phase 2 class-level configuration
    table_editable_columns = None
    table_edit_event = "table_cell_edit"
    table_resizable = False
    table_reorderable = False
    table_reorder_event = "table_reorder"
    table_frozen_left = 0
    table_frozen_right = 0
    table_column_visibility = False
    table_visibility_event = "table_visibility"
    table_density = "comfortable"
    table_density_toggle = False
    table_density_event = "table_density"
    table_responsive_cards = False
    table_editable_rows = False
    table_edit_row_event = "table_row_edit"
    table_save_row_event = "table_row_save"
    table_cancel_row_event = "table_row_cancel"

    # Phase 3 class-level configuration
    table_expandable = False
    table_expand_event = "table_expand"
    table_bulk_actions = None
    table_bulk_action_event = "table_bulk_action"
    table_exportable = False
    table_export_event = "table_export"
    table_export_formats = None  # defaults to ["csv", "json"]
    table_group_by = ""
    table_group_event = "table_group"
    table_group_toggle_event = "table_group_toggle"
    table_collapsible_groups = True
    table_keyboard_nav = False
    table_virtual_scroll = False
    table_virtual_row_height = 40
    table_virtual_buffer = 5
    table_server_mode = False
    table_facets = False
    table_persist_key = ""
    table_printable = False
    table_show_stats = False

    # Phase 4 class-level configuration
    table_footer_aggregations = None  # {col_key: "sum"|"avg"|"count"|"min"|"max"}
    table_row_class_map = None  # {col_key: {value: css_class}} or callable(row) -> css_class
    table_column_groups = None  # list of {"label": "Q1", "columns": ["jan","feb","mar"]}
    table_row_drag = False
    table_row_drag_event = "table_row_drag"
    table_copyable = False
    table_copy_event = "table_copy"
    table_copy_format = "csv"  # "csv" or "tsv"

    # Phase 5 class-level configuration
    table_importable = False
    table_import_event = "table_import"
    table_import_formats = None  # defaults to ["csv", "json"]
    table_import_preview = True  # preview before confirming
    table_computed_columns = None  # list of {"key": ..., "label": ..., "expression": ...}
    table_cell_merge_key = "_merge"  # row data key for colspan info
    table_column_expressions = None  # {col_key: expression_string} for advanced filtering
    table_expression_event = "table_expression"
    table_conditional_formatting = None  # list of formatting preset dicts

    def init_table_state(self):
        """Initialize instance state. Call from mount()."""
        # Resolve None class-level defaults to fresh instances
        if self.table_columns is None:
            self.table_columns = []
        if self.table_searchable_fields is None:
            self.table_searchable_fields = []
        if self.table_editable_columns is None:
            self.table_editable_columns = []
        if self.table_bulk_actions is None:
            self.table_bulk_actions = []
        if self.table_export_formats is None:
            self.table_export_formats = ["csv", "json"]
        if self.table_footer_aggregations is None:
            self.table_footer_aggregations = {}
        if self.table_row_class_map is None:
            self.table_row_class_map = {}
        if self.table_column_groups is None:
            self.table_column_groups = []
        if self.table_import_formats is None:
            self.table_import_formats = ["csv", "json"]
        if self.table_computed_columns is None:
            self.table_computed_columns = []
        if self.table_column_expressions is None:
            self.table_column_expressions = {}
        if self.table_conditional_formatting is None:
            self.table_conditional_formatting = []

        self.table_sort_by = self.table_default_sort
        self.table_sort_desc = self.table_default_sort_desc
        self.table_search_query = ""
        self.table_filters = {}
        self.table_selected_rows = []
        self.table_page = 1
        self.table_total_pages = 1
        self.table_rows = []
        self.table_loading = False
        # Phase 2 state
        self.table_editing_rows = []
        self.table_column_order = [
            col.get("key", col) if isinstance(col, dict) else col for col in self.table_columns
        ]
        self.table_visible_columns = list(self.table_column_order)
        self.table_current_density = self.table_density
        # Phase 3 state
        self.table_expanded_rows = []
        self.table_collapsed_groups = []
        self.table_current_group_by = self.table_group_by
        self.table_facet_counts = {}
        self.table_column_stats = {}
        # Phase 4 state
        self.table_row_order = []  # for drag reorder tracking
        # Phase 5 state
        self.table_import_preview_data = []  # staged rows before confirm
        self.table_import_errors = []  # validation errors from last import
        self.table_import_pending = False  # True when preview is shown, awaiting confirm
        self.table_active_expressions = {}  # {col_key: expression_string}

    # ── Event Handlers ──

    @event_handler()
    def on_table_sort(self, value, **kwargs):
        """Handle sort event: toggle direction or switch column."""
        column = str(value)
        if self.table_sort_by == column:
            self.table_sort_desc = not self.table_sort_desc
        else:
            self.table_sort_by = column
            self.table_sort_desc = False

    @event_handler()
    def on_table_search(self, value, **kwargs):
        """Handle search event: update query, reset to page 1."""
        self.table_search_query = str(value)
        self.table_page = 1

    @event_handler()
    def on_table_filter(self, value, column=None, **kwargs):
        """Handle filter event: set or clear per-column filter, reset to page 1."""
        if column is None:
            column = kwargs.get("data-column", kwargs.get("data_column", ""))
        column = str(column)
        value = str(value)
        if value:
            self.table_filters[column] = value
        else:
            self.table_filters.pop(column, None)
        self.table_page = 1

    @event_handler()
    def on_table_select(self, value, **kwargs):
        """Handle selection event: toggle row or select/deselect all."""
        value = str(value)
        if value == "__all__":
            # Toggle all visible rows
            if self.table_selected_rows:
                self.table_selected_rows = []
            else:
                self.table_selected_rows = [
                    str(row.get(self.table_row_key, "")) for row in self.table_rows
                ]
        else:
            if value in self.table_selected_rows:
                self.table_selected_rows.remove(value)
            else:
                self.table_selected_rows.append(value)

    @event_handler()
    def on_table_page(self, value, **kwargs):
        """Handle page event: navigate to page number."""
        try:
            self.table_page = int(value)
        except (ValueError, TypeError):
            # Ignore non-integer page values; keep current page.
            pass

    # ── Phase 2 Event Handlers ──

    @event_handler()
    def on_table_cell_edit(self, value, **kwargs):
        """Handle inline cell edit. value is JSON: {row_key, column, value}."""
        try:
            data = json.loads(str(value)) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(data, dict):
            self.handle_cell_edit(
                row_key=data.get("row_key", ""),
                column=data.get("column", ""),
                value=data.get("value", ""),
            )

    def handle_cell_edit(self, row_key, column, value):
        """Override this to persist inline cell edits. Called by on_table_cell_edit."""
        pass

    @event_handler()
    def on_table_reorder(self, value, **kwargs):
        """Handle column reorder. value is comma-separated column keys."""
        new_order = [k.strip() for k in str(value).split(",") if k.strip()]
        if new_order:
            self.table_column_order = new_order

    @event_handler()
    def on_table_visibility(self, value, **kwargs):
        """Handle column visibility toggle. value is comma-separated visible keys."""
        visible = [k.strip() for k in str(value).split(",") if k.strip()]
        self.table_visible_columns = visible

    @event_handler()
    def on_table_density(self, value, **kwargs):
        """Handle density toggle. value is 'compact', 'comfortable', or 'spacious'."""
        val = str(value)
        if val in ("compact", "comfortable", "spacious"):
            self.table_current_density = val

    @event_handler()
    def on_table_row_edit(self, value, **kwargs):
        """Handle entering row edit mode."""
        row_id = str(value)
        if row_id not in self.table_editing_rows:
            self.table_editing_rows.append(row_id)

    @event_handler()
    def on_table_row_save(self, value, **kwargs):
        """Handle saving an edited row. Override handle_row_save to persist."""
        row_id = str(value)
        self.handle_row_save(row_id, kwargs)
        if row_id in self.table_editing_rows:
            self.table_editing_rows.remove(row_id)

    @event_handler()
    def on_table_row_cancel(self, value, **kwargs):
        """Handle cancelling row edit."""
        row_id = str(value)
        if row_id in self.table_editing_rows:
            self.table_editing_rows.remove(row_id)

    def handle_row_save(self, row_key, data):
        """Override this to persist row edits. Called by on_table_row_save."""
        pass

    # ── Phase 3 Event Handlers ──

    @event_handler()
    def on_table_expand(self, value, **kwargs):
        """Handle row expansion toggle."""
        row_id = str(value)
        if row_id in self.table_expanded_rows:
            self.table_expanded_rows.remove(row_id)
        else:
            self.table_expanded_rows.append(row_id)

    @event_handler()
    def on_table_bulk_action(self, value, **kwargs):
        """Handle bulk action on selected rows."""
        action = str(value)
        self.handle_bulk_action(action, list(self.table_selected_rows))

    def handle_bulk_action(self, action, selected_rows):
        """Override this to handle bulk actions. Called with action key and selected row IDs."""
        pass

    @event_handler()
    def on_table_export(self, value, **kwargs):
        """Handle export request. value is the format (csv/json)."""
        fmt = str(value)
        self.handle_export(fmt)

    def handle_export(self, fmt):
        """Override this to handle exports. Called with 'csv' or 'json'.

        Default implementation generates data and stores in table_export_data.
        """
        rows = self.table_rows
        columns = self.table_columns
        col_keys = [col.get("key", col) if isinstance(col, dict) else str(col) for col in columns]
        col_labels = [
            col.get("label", col.get("key", "")) if isinstance(col, dict) else str(col)
            for col in columns
        ]

        if fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(col_labels)
            for row in rows:
                writer.writerow([row.get(k, "") for k in col_keys])
            self.table_export_data = output.getvalue()
            self.table_export_format = "csv"
        elif fmt == "json":
            export_rows = [{k: row.get(k, "") for k in col_keys} for row in rows]
            self.table_export_data = json.dumps(export_rows, default=str)
            self.table_export_format = "json"

    @event_handler()
    def on_table_group(self, value, **kwargs):
        """Handle grouping by column."""
        self.table_current_group_by = str(value)

    @event_handler()
    def on_table_group_toggle(self, value, **kwargs):
        """Handle group collapse/expand toggle."""
        group_key = str(value)
        if group_key in self.table_collapsed_groups:
            self.table_collapsed_groups.remove(group_key)
        else:
            self.table_collapsed_groups.append(group_key)

    # ── Phase 4 Event Handlers ──

    @event_handler()
    def on_table_row_drag(self, value, **kwargs):
        """Handle row drag-and-drop reorder. value is JSON: {old_index, new_index}."""
        try:
            data = json.loads(str(value)) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(data, dict):
            try:
                old_idx = int(data.get("old_index", -1))
                new_idx = int(data.get("new_index", -1))
            except (ValueError, TypeError):
                return
            if 0 <= old_idx < len(self.table_rows) and 0 <= new_idx < len(self.table_rows):
                row = self.table_rows.pop(old_idx)
                self.table_rows.insert(new_idx, row)
                self.handle_row_drag(old_idx, new_idx)

    def handle_row_drag(self, old_index, new_index):
        """Override this to persist row reorder. Called by on_table_row_drag."""
        pass

    @event_handler()
    def on_table_copy(self, value, **kwargs):
        """Handle copy event. value is JSON list of row keys to copy."""
        try:
            data = json.loads(str(value)) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, list):
            row_keys = [str(k) for k in data]
        else:
            row_keys = list(str(v) for v in self.table_selected_rows)
        self.handle_copy(row_keys)

    def handle_copy(self, row_keys):
        """Override this to handle copy. Default generates CSV/TSV in table_copy_data."""
        rows_to_copy = (
            [row for row in self.table_rows if str(row.get(self.table_row_key, "")) in row_keys]
            if row_keys
            else self.table_rows
        )
        col_keys = [
            col.get("key", col) if isinstance(col, dict) else str(col) for col in self.table_columns
        ]
        col_labels = [
            col.get("label", col.get("key", "")) if isinstance(col, dict) else str(col)
            for col in self.table_columns
        ]
        sep = "\t" if self.table_copy_format == "tsv" else ","
        lines = [sep.join(col_labels)]
        for row in rows_to_copy:
            lines.append(sep.join(str(row.get(k, "")) for k in col_keys))
        self.table_copy_data = "\n".join(lines)

    # ── Phase 5 Event Handlers ──

    @event_handler()
    def on_table_import(self, value, **kwargs):
        """Handle import event. value is JSON: {format, data, confirm}.

        When confirm=False (or absent), parses and stages preview.
        When confirm=True, commits the previewed rows.
        """
        try:
            data = json.loads(str(value)) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict):
            return

        confirm = data.get("confirm", False)
        if confirm and self.table_import_pending:
            self._confirm_import()
            return

        fmt = str(data.get("format", "csv"))
        raw = data.get("data", "")
        self._parse_import(fmt, raw)

    def _parse_import(self, fmt, raw):
        """Parse imported data and stage for preview."""
        self.table_import_errors = []
        self.table_import_preview_data = []
        self.table_import_pending = False

        col_keys = [
            col.get("key", col) if isinstance(col, dict) else str(col) for col in self.table_columns
        ]

        try:
            if fmt == "csv":
                reader = csv.DictReader(io.StringIO(str(raw)))
                rows = list(reader)
            elif fmt == "json":
                parsed = json.loads(str(raw))
                if not isinstance(parsed, list):
                    self.table_import_errors.append("JSON must be an array of objects")
                    return
                rows = parsed
            else:
                self.table_import_errors.append(f"Unsupported format: {fmt}")
                return
        except Exception as e:
            self.table_import_errors.append(f"Parse error: {str(e)}")
            return

        # Validate rows have at least some known columns
        valid_rows = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                self.table_import_errors.append(f"Row {i + 1}: not a dict")
                continue
            # Keep only known column keys
            cleaned = {k: row.get(k, "") for k in col_keys if k in row}
            if cleaned:
                valid_rows.append(cleaned)
            else:
                self.table_import_errors.append(f"Row {i + 1}: no matching columns")

        self.table_import_preview_data = valid_rows
        if valid_rows:
            self.table_import_pending = True if self.table_import_preview else False
            if not self.table_import_preview:
                self._confirm_import()

    def _confirm_import(self):
        """Commit previewed import rows."""
        imported = list(self.table_import_preview_data)
        self.table_import_preview_data = []
        self.table_import_pending = False
        self.handle_import(imported)

    def handle_import(self, rows):
        """Override this to persist imported rows. Default appends to table_rows."""
        self.table_rows.extend(rows)

    @event_handler()
    def on_table_expression(self, value, **kwargs):
        """Handle column expression filter. value is JSON: {column, expression}."""
        try:
            data = json.loads(str(value)) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict):
            return
        column = str(data.get("column", ""))
        expression = str(data.get("expression", ""))
        if column:
            if expression:
                self.table_active_expressions[column] = expression
            else:
                self.table_active_expressions.pop(column, None)
            self.table_page = 1

    # ── Phase 5 Computed Helpers ──

    def evaluate_computed_columns(self, rows):
        """Evaluate computed columns and inject values into rows.

        Each computed column has:
          - key: virtual column key
          - expression: string like "revenue - cost" referencing other column keys

        Returns the rows with computed values injected.
        """
        if not self.table_computed_columns:
            return rows
        result = []
        for row in rows:
            row_copy = dict(row)
            for cc in self.table_computed_columns:
                if not isinstance(cc, dict):
                    continue
                key = cc.get("key", "")
                expr = cc.get("expression", "")
                if key and expr:
                    row_copy[key] = self._eval_expression(expr, row_copy)
            result.append(row_copy)
        return result

    def _eval_expression(self, expression, row):
        """Safely evaluate a computed column expression against a row.

        Uses an AST-based parser that only permits arithmetic operations
        (+, -, *, /, %, parentheses) and column references. No eval().

        Supports: column references, numeric literals, +, -, *, /, %,
        unary minus, and parenthesised sub-expressions.
        """
        # Build namespace of numeric row values
        namespace = {}
        for k, v in row.items():
            try:
                namespace[k] = float(v)
            except (ValueError, TypeError):
                namespace[k] = 0
        try:
            result = _safe_eval_arithmetic(expression, namespace)
            if isinstance(result, float) and result == int(result):
                return int(result)
            return round(result, 2) if isinstance(result, float) else result
        except Exception:
            return ""

    def evaluate_expression_filter(self, value, expression):
        """Evaluate a column expression filter against a cell value.

        Supported expression syntax:
          > N, >= N, < N, <= N, = N, != N  (numeric comparison)
          contains "text"                   (substring match)
          startswith "text"                 (prefix match)
          endswith "text"                   (suffix match)
          between N and M                   (range, inclusive)
          empty / not empty                 (null/blank check)

        Returns True if value passes the filter, False otherwise.
        """
        expr = expression.strip()
        if not expr:
            return True

        str_val = str(value)

        # empty / not empty
        if expr.lower() == "empty":
            return str_val == "" or value is None
        if expr.lower() == "not empty":
            return str_val != "" and value is not None

        # contains "text"
        if expr.lower().startswith("contains "):
            text = expr[9:].strip().strip('"').strip("'")
            return text.lower() in str_val.lower()

        # startswith "text"
        if expr.lower().startswith("startswith "):
            text = expr[11:].strip().strip('"').strip("'")
            return str_val.lower().startswith(text.lower())

        # endswith "text"
        if expr.lower().startswith("endswith "):
            text = expr[9:].strip().strip('"').strip("'")
            return str_val.lower().endswith(text.lower())

        # between N and M
        if expr.lower().startswith("between "):
            parts = expr[8:].lower().split(" and ")
            if len(parts) == 2:
                try:
                    lo = float(parts[0].strip())
                    hi = float(parts[1].strip())
                    num_val = float(value)
                    return lo <= num_val <= hi
                except (ValueError, TypeError):
                    return False

        # Numeric comparisons: >=, <=, !=, >, <, =
        for op in (">=", "<=", "!=", ">", "<", "="):
            if expr.startswith(op):
                rest = expr[len(op) :].strip()
                try:
                    threshold = float(rest)
                    num_val = float(value)
                    if op == ">":
                        return num_val > threshold
                    elif op == ">=":
                        return num_val >= threshold
                    elif op == "<":
                        return num_val < threshold
                    elif op == "<=":
                        return num_val <= threshold
                    elif op == "=":
                        return num_val == threshold
                    elif op == "!=":
                        return num_val != threshold
                except (ValueError, TypeError):
                    return False

        return True

    def apply_expression_filters(self, rows):
        """Filter rows using active column expressions."""
        if not self.table_active_expressions:
            return rows
        result = []
        for row in rows:
            passes = True
            for col_key, expr in self.table_active_expressions.items():
                val = row.get(col_key, "")
                if not self.evaluate_expression_filter(val, expr):
                    passes = False
                    break
            if passes:
                result.append(row)
        return result

    def get_conditional_formatting(self, value, col_key):
        """Evaluate conditional formatting presets for a cell value.

        Each preset in table_conditional_formatting is a dict:
          {
            "column": col_key,
            "type": "data_bar" | "color_scale" | "icon_set",
            "min": 0, "max": 100,        # for data_bar / color_scale
            "colors": ["#f00", "#0f0"],   # for color_scale (2-3 colors)
            "icons": ["▼", "▶", "▲"],    # for icon_set
            "thresholds": [33, 66],       # boundaries for icon_set
          }

        Returns dict with formatting info or empty dict.
        """
        for preset in self.table_conditional_formatting:
            if not isinstance(preset, dict):
                continue
            if preset.get("column") != col_key:
                continue
            fmt_type = preset.get("type", "")
            try:
                num_val = float(value)
            except (ValueError, TypeError):
                continue
            pmin = float(preset.get("min", 0))
            pmax = float(preset.get("max", 100))
            span = pmax - pmin if pmax != pmin else 1

            if fmt_type == "data_bar":
                pct = max(0, min(100, ((num_val - pmin) / span) * 100))
                return {"type": "data_bar", "percent": round(pct, 1)}
            elif fmt_type == "color_scale":
                colors = preset.get("colors", ["#ff0000", "#00ff00"])
                ratio = max(0.0, min(1.0, (num_val - pmin) / span))
                color = self._interpolate_color(colors, ratio)
                return {"type": "color_scale", "color": color}
            elif fmt_type == "icon_set":
                icons = preset.get("icons", ["▼", "▶", "▲"])
                thresholds = preset.get("thresholds", [])
                icon = icons[-1] if icons else ""
                for i, t in enumerate(thresholds):
                    if num_val < float(t):
                        icon = icons[i] if i < len(icons) else icons[-1]
                        break
                return {"type": "icon_set", "icon": icon}
        return {}

    def _interpolate_color(self, colors, ratio):
        """Interpolate between hex colors based on ratio (0.0-1.0)."""
        return interpolate_color_gradient(colors, ratio)

    def get_cell_merge(self, row, col_key):
        """Get colspan for a cell from row's _merge data.

        Row merge data format: {_merge: {col_key: colspan_int, ...}}
        Returns colspan int (1 = normal, >1 = spans multiple columns, 0 = hidden/merged).
        """
        merge_data = row.get(self.table_cell_merge_key, {})
        if not isinstance(merge_data, dict):
            return 1
        return merge_data.get(col_key, 1)

    # ── Phase 4 Computed Helpers ──

    def get_footer_aggregations(self):
        """Compute footer aggregation values based on table_footer_aggregations config.

        Returns dict of {col_key: formatted_value}.
        """
        result = {}
        for col_key, agg_type in self.table_footer_aggregations.items():
            values = []
            for row in self.table_rows:
                val = row.get(col_key)
                if val is not None:
                    try:
                        values.append(float(val))
                    except (ValueError, TypeError):
                        # Skip non-numeric cells during aggregation.
                        continue
            if not values:
                result[col_key] = ""
                continue
            if agg_type == "sum":
                result[col_key] = sum(values)
            elif agg_type == "avg":
                result[col_key] = round(sum(values) / len(values), 2)
            elif agg_type == "count":
                result[col_key] = len(values)
            elif agg_type == "min":
                result[col_key] = min(values)
            elif agg_type == "max":
                result[col_key] = max(values)
            else:
                result[col_key] = ""
        return result

    def get_row_class(self, row):
        """Compute CSS class for a row based on row_class_map.

        table_row_class_map can be:
          - a dict: {col_key: {value: css_class}}
          - a callable: fn(row) -> css_class_string
        """
        if callable(self.table_row_class_map):
            return self.table_row_class_map(row)
        classes = []
        for col_key, value_map in self.table_row_class_map.items():
            val = str(row.get(col_key, ""))
            if isinstance(value_map, dict) and val in value_map:
                classes.append(value_map[val])
        return " ".join(classes)

    def _format_cell_value(self, value, col):
        """Format a cell value based on column type declaration.

        Supported types: number, currency, date, percentage, boolean.
        """
        return format_cell(value, col)

    # ── Phase 3 Computed Helpers ──

    def get_facet_counts(self):
        """Compute facet counts for filterable columns from current rows."""
        counts = {}
        for col in self.table_columns:
            if not isinstance(col, dict):
                continue
            if not col.get("filterable", False):
                continue
            key = col.get("key", "")
            col_counts = {}
            for row in self.table_rows:
                val = str(row.get(key, ""))
                if val:
                    col_counts[val] = col_counts.get(val, 0) + 1
            counts[key] = col_counts
        self.table_facet_counts = counts
        return counts

    def get_column_stats(self):
        """Compute column statistics (min, max, avg, sum, count) for numeric columns."""
        stats = {}
        for col in self.table_columns:
            if not isinstance(col, dict):
                continue
            if not col.get("stats", False):
                continue
            key = col.get("key", "")
            values = []
            for row in self.table_rows:
                val = row.get(key)
                if val is not None:
                    try:
                        values.append(float(val))
                    except (ValueError, TypeError):
                        # Skip non-numeric cells when computing column stats.
                        continue
            if values:
                stats[key] = {
                    "min": min(values),
                    "max": max(values),
                    "avg": round(sum(values) / len(values), 2),
                    "sum": sum(values),
                    "count": len(values),
                }
            else:
                stats[key] = {
                    "min": None,
                    "max": None,
                    "avg": None,
                    "sum": None,
                    "count": 0,
                }
        self.table_column_stats = stats
        return stats

    def _group_rows(self, rows):
        """Group rows by the current group_by column. Returns list of (group_value, rows) tuples."""
        if not self.table_current_group_by:
            return [("", rows)]
        groups = {}
        group_order = []
        for row in rows:
            val = str(row.get(self.table_current_group_by, ""))
            if val not in groups:
                groups[val] = []
                group_order.append(val)
            groups[val].append(row)
        return [(k, groups[k]) for k in group_order]

    # ── Context Generation ──

    def get_table_context(self):
        """Return a dict suitable for the ``{% data_table %}`` template tag.

        **Pre-mount safety (closes #1114)**: djust's WebSocket consumer calls
        ``get_context_data()`` (which often calls this) BEFORE ``mount()``
        runs, to build the initial Rust VDOM snapshot. Without ``mount()``,
        ``init_table_state()`` hasn't run, so instance attributes like
        ``self.table_rows``, ``self.table_sort_by``, etc. don't exist yet —
        and a raw attribute read raises ``AttributeError`` (which djust
        catches silently → empty initial VDOM → all subsequent patches diff
        against empty content).

        Guard: if ``init_table_state()`` hasn't run, return a minimal
        pre-mount default that the ``{% data_table %}`` template tag can
        render as an empty table without errors. The first patch after
        ``mount()`` resolves to the real state.

        Use a sentinel attribute (``table_rows`` is set as the LAST line
        of ``init_table_state()``) to detect the pre-mount state cheaply.
        """
        if not hasattr(self, "table_rows"):
            return _PRE_MOUNT_TABLE_CONTEXT
        return {
            "rows": self.table_rows,
            "columns": self.table_columns,
            "sort_by": self.table_sort_by,
            "sort_desc": self.table_sort_desc,
            "sort_event": self.table_sort_event,
            "selectable": self.table_selectable,
            "selected_rows": self.table_selected_rows,
            "select_event": self.table_select_event,
            "row_key": self.table_row_key,
            "search": bool(self.table_searchable_fields),
            "search_query": self.table_search_query,
            "search_event": self.table_search_event,
            "search_debounce": 300,
            "filters": self.table_filters,
            "filter_event": self.table_filter_event,
            "loading": self.table_loading,
            "empty_title": "No data",
            "empty_description": "",
            "empty_icon": "",
            "paginate": self.table_page_size > 0,
            "page": self.table_page,
            "total_pages": self.table_total_pages,
            "page_event": self.table_page_event,
            "striped": False,
            "compact": False,
            # Phase 2
            "editable_columns": self.table_editable_columns,
            "edit_event": self.table_edit_event,
            "resizable": self.table_resizable,
            "reorderable": self.table_reorderable,
            "reorder_event": self.table_reorder_event,
            "frozen_left": self.table_frozen_left,
            "frozen_right": self.table_frozen_right,
            "column_visibility": self.table_column_visibility,
            "visibility_event": self.table_visibility_event,
            "density": self.table_current_density,
            "density_toggle": self.table_density_toggle,
            "density_event": self.table_density_event,
            "responsive_cards": self.table_responsive_cards,
            "editable_rows": self.table_editable_rows,
            "edit_row_event": self.table_edit_row_event,
            "save_row_event": self.table_save_row_event,
            "cancel_row_event": self.table_cancel_row_event,
            "editing_rows": self.table_editing_rows,
            # Phase 3
            "expandable": self.table_expandable,
            "expand_event": self.table_expand_event,
            "expanded_rows": self.table_expanded_rows,
            "bulk_actions": self.table_bulk_actions,
            "bulk_action_event": self.table_bulk_action_event,
            "exportable": self.table_exportable,
            "export_event": self.table_export_event,
            "export_formats": self.table_export_formats,
            "group_by": self.table_current_group_by,
            "group_event": self.table_group_event,
            "group_toggle_event": self.table_group_toggle_event,
            "collapsible_groups": self.table_collapsible_groups,
            "collapsed_groups": self.table_collapsed_groups,
            "keyboard_nav": self.table_keyboard_nav,
            "virtual_scroll": self.table_virtual_scroll,
            "virtual_row_height": self.table_virtual_row_height,
            "virtual_buffer": self.table_virtual_buffer,
            "server_mode": self.table_server_mode,
            "facets": self.table_facets,
            "facet_counts": self.table_facet_counts,
            "persist_key": self.table_persist_key,
            "printable": self.table_printable,
            "column_stats": self.table_column_stats,
            # Phase 4
            "footer_aggregations": self.table_footer_aggregations,
            "row_class_map": self.table_row_class_map,
            "column_groups": self.table_column_groups,
            "row_drag": self.table_row_drag,
            "row_drag_event": self.table_row_drag_event,
            "copyable": self.table_copyable,
            "copy_event": self.table_copy_event,
            "copy_format": self.table_copy_format,
            # Phase 5
            "importable": self.table_importable,
            "import_event": self.table_import_event,
            "import_formats": self.table_import_formats,
            "import_preview": self.table_import_preview,
            "import_preview_data": self.table_import_preview_data,
            "import_errors": self.table_import_errors,
            "import_pending": self.table_import_pending,
            "computed_columns": self.table_computed_columns,
            "cell_merge_key": self.table_cell_merge_key,
            "column_expressions": self.table_column_expressions,
            "expression_event": self.table_expression_event,
            "active_expressions": self.table_active_expressions,
            "conditional_formatting": self.table_conditional_formatting,
        }

    # ── Queryset Pipeline ──

    def get_table_queryset(self):
        """Return the base queryset."""
        if self.table_queryset is not None:
            return self.table_queryset
        if self.table_model is not None:
            return self.table_model.objects.all()
        return []

    def _apply_table_search(self, qs):
        """Apply global search across searchable fields."""
        if not self.table_search_query or not self.table_searchable_fields:
            return qs
        from django.db.models import Q

        q = Q()
        for field in self.table_searchable_fields:
            q |= Q(**{f"{field}__icontains": self.table_search_query})
        return qs.filter(q)

    def _apply_table_filters(self, qs):
        """Apply per-column filters."""
        for col_key, value in self.table_filters.items():
            if value:
                qs = qs.filter(**{f"{col_key}__icontains": value})
        return qs

    def _apply_table_sort(self, qs):
        """Apply sort ordering."""
        if not self.table_sort_by:
            return qs
        order = f"-{self.table_sort_by}" if self.table_sort_desc else self.table_sort_by
        return qs.order_by(order)

    def _apply_table_pagination(self, qs):
        """Slice queryset for current page and set total_pages."""
        if self.table_page_size <= 0:
            return qs
        total = qs.count()
        self.table_total_pages = max(1, math.ceil(total / self.table_page_size))
        start = (self.table_page - 1) * self.table_page_size
        end = start + self.table_page_size
        return qs[start:end]

    def refresh_table(self):
        """Run the full pipeline: queryset -> search -> filter -> sort -> paginate -> serialize.

        When table_server_mode is True, calls refresh_table_server() instead.
        """
        if self.table_server_mode:
            self.refresh_table_server()
            return
        qs = self.get_table_queryset()
        qs = self._apply_table_search(qs)
        qs = self._apply_table_filters(qs)
        qs = self._apply_table_sort(qs)
        qs = self._apply_table_pagination(qs)
        # Serialize to list of dicts
        self.table_rows = list(qs.values()) if hasattr(qs, "values") else list(qs)
        # Compute facets and stats if enabled
        if self.table_facets:
            self.get_facet_counts()
        if self.table_show_stats:
            self.get_column_stats()

    def refresh_table_server(self):
        """Override this in server_mode to populate table_rows, table_total_pages, etc.

        Called by refresh_table() when table_server_mode is True.
        """
        pass
