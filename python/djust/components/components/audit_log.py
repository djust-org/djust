"""Audit log table component for displaying audit trail entries."""

import html

from djust import Component


class AuditLog(Component):
    """Style-agnostic audit log table component.

    Pre-configured data table for audit log entries with streaming support.

    Usage in a LiveView::

        self.log = AuditLog(
            entries=[
                {"timestamp": "2026-03-25 14:30", "user": "admin",
                 "action": "update", "resource": "User #42"},
            ],
            stream_event="new_entry",
        )

    In template::

        {{ log|safe }}

    Args:
        entries: List of entry dicts with timestamp, user, action, resource, detail
        stream_event: djust event for new entry streaming
        columns: List of column names to show (default: all)
        allowed_actions: Set of action values permitted for CSS class injection
            (default: create, read, update, delete, login, logout, export, import, approve, reject)
        custom_class: Additional CSS classes
    """

    DEFAULT_COLUMNS = ["timestamp", "user", "action", "resource", "detail"]
    DEFAULT_ALLOWED_ACTIONS = frozenset(
        {
            "create",
            "read",
            "update",
            "delete",
            "login",
            "logout",
            "export",
            "import",
            "approve",
            "reject",
        }
    )

    def __init__(
        self,
        entries: list = None,
        stream_event: str = "",
        columns: list = None,
        allowed_actions: set = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            entries=entries,
            stream_event=stream_event,
            columns=columns,
            custom_class=custom_class,
            **kwargs,
        )
        self.entries = entries or []
        self.stream_event = stream_event
        self.columns = columns or self.DEFAULT_COLUMNS
        self.allowed_actions = (
            allowed_actions if allowed_actions is not None else self.DEFAULT_ALLOWED_ACTIONS
        )
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-audit-log"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        stream_attr = ""
        if self.stream_event:
            e_stream = html.escape(self.stream_event)
            stream_attr = f' data-stream-event="{e_stream}"'

        # Column labels
        col_labels = {
            "timestamp": "Timestamp",
            "user": "User",
            "action": "Action",
            "resource": "Resource",
            "detail": "Detail",
        }

        # Header
        headers = []
        for col in self.columns:
            label = html.escape(col_labels.get(col, col.title()))
            headers.append(f'<th class="dj-audit-log__th">{label}</th>')
        thead = f"<thead><tr>{''.join(headers)}</tr></thead>"

        # Rows
        rows = []
        for entry in self.entries:
            cells = []
            for col in self.columns:
                val = html.escape(str(entry.get(col, "")))
                cell_cls = f"dj-audit-log__td dj-audit-log__td--{col}"
                if col == "action":
                    action_val = str(entry.get("action", ""))
                    if action_val in self.allowed_actions:
                        cell_cls += f" dj-audit-log__action--{action_val}"
                cells.append(f'<td class="{cell_cls}">{val}</td>')
            rows.append(f'<tr class="dj-audit-log__row">{"".join(cells)}</tr>')

        tbody = f"<tbody>{''.join(rows)}</tbody>"

        empty_msg = ""
        if not self.entries:
            col_count = len(self.columns)
            empty_msg = (
                f'<tbody><tr><td colspan="{col_count}" '
                f'class="dj-audit-log__empty">No entries</td></tr></tbody>'
            )
            tbody = empty_msg

        return (
            f'<div class="{class_str}"{stream_attr}>'
            f'<table class="dj-audit-log__table">'
            f"{thead}{tbody}</table></div>"
        )
