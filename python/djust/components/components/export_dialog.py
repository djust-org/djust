"""Export dialog component for format picker and column selection."""

import html

from djust import Component


class ExportDialog(Component):
    """Style-agnostic export dialog component.

    Modal with format picker and column selection for data export.

    Usage in a LiveView::

        self.exporter = ExportDialog(
            formats=["csv", "xlsx", "json"],
            columns=[
                {"id": "name", "label": "Name", "checked": True},
                {"id": "email", "label": "Email", "checked": True},
                {"id": "phone", "label": "Phone", "checked": False},
            ],
            event="export",
            open=True,
        )

    In template::

        {{ exporter|safe }}

    Args:
        formats: List of available export formats
        columns: List of column dicts with id, label, checked
        event: djust event for triggering export
        open: Whether the dialog is visible
        close_event: djust event for closing
        selected_format: Currently selected format
        title: Dialog title
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        formats: list = None,
        columns: list = None,
        event: str = "export",
        open: bool = False,
        close_event: str = "close_export",
        selected_format: str = "",
        title: str = "Export Data",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            formats=formats,
            columns=columns,
            event=event,
            open=open,
            close_event=close_event,
            selected_format=selected_format,
            title=title,
            custom_class=custom_class,
            **kwargs,
        )
        self.formats = formats or []
        self.columns = columns or []
        self.event = event
        self.open = open
        self.close_event = close_event
        self.selected_format = selected_format or (formats[0] if formats else "")
        self.title = title
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        if not self.open:
            return ""

        classes = ["dj-export-dialog"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_title = html.escape(self.title)
        e_event = html.escape(self.event)
        e_close = html.escape(self.close_event)

        # Format picker
        format_options = []
        for fmt in self.formats:
            e_fmt = html.escape(str(fmt))
            checked = " checked" if fmt == self.selected_format else ""
            format_options.append(
                f'<label class="dj-export-dialog__format">'
                f'<input type="radio" name="export_format" value="{e_fmt}"{checked}>'
                f'<span class="dj-export-dialog__format-label">{e_fmt.upper()}</span>'
                f"</label>"
            )
        format_section = (
            f'<div class="dj-export-dialog__formats">'
            f'<h4 class="dj-export-dialog__section-title">Format</h4>'
            f"{''.join(format_options)}</div>"
        )

        # Column picker
        col_options = []
        for col in self.columns:
            e_id = html.escape(str(col.get("id", "")))
            e_label = html.escape(str(col.get("label", "")))
            checked = " checked" if col.get("checked", True) else ""
            col_options.append(
                f'<label class="dj-export-dialog__column">'
                f'<input type="checkbox" name="export_col" value="{e_id}"{checked}>'
                f"<span>{e_label}</span></label>"
            )
        col_section = (
            f'<div class="dj-export-dialog__columns">'
            f'<h4 class="dj-export-dialog__section-title">Columns</h4>'
            f"{''.join(col_options)}</div>"
        )

        return (
            f'<div class="dj-export-dialog__backdrop" dj-click="{e_close}">'
            f'<div class="{class_str}" onclick="event.stopPropagation()">'
            f'<div class="dj-export-dialog__header">'
            f"<h3>{e_title}</h3>"
            f'<button class="dj-export-dialog__close" dj-click="{e_close}">&times;</button>'
            f"</div>"
            f'<div class="dj-export-dialog__body">'
            f"{format_section}{col_section}</div>"
            f'<div class="dj-export-dialog__footer">'
            f'<button class="dj-export-dialog__cancel" dj-click="{e_close}">Cancel</button>'
            f'<button class="dj-export-dialog__submit" dj-click="{e_event}">Export</button>'
            f"</div></div></div>"
        )
