"""
Table-selection canary (#2781) — a selectable ``TableComponent`` end-to-end.

The smallest faithful browser surface for the multi-select state of
``TableComponent(selectable=True)``: three rows, the header checkbox, and a
server-truth readout of ``selected_rows`` so a Playwright test can assert the
DOM checkboxes AND the server agree after every click. Exercised by
``tests/playwright/test_table_select.py``.
"""

from djust import LiveView
from djust.components.data import TableComponent


class TableSelectView(LiveView):
    """Minimal LiveView holding one selectable three-row table."""

    template_name = "demos/table_select.html"

    def mount(self, request, **kwargs):
        self.table = TableComponent(
            columns=[
                {"key": "id", "label": "ID"},
                {"key": "name", "label": "Name"},
            ],
            rows=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
                {"id": 3, "name": "Carol"},
            ],
            selectable=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["selected_csv"] = ",".join(self.table.selected_rows)
        return context
