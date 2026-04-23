"""Pivot Table component — configurable pivot/cross-tab table."""

import html
from typing import Optional

from djust import Component


class PivotTable(Component):
    """Configurable pivot table that aggregates data by row/column dimensions.

    Usage in a LiveView::

        self.pivot = PivotTable(
            data=[
                {"category": "A", "quarter": "Q1", "revenue": 100},
                {"category": "A", "quarter": "Q2", "revenue": 150},
                {"category": "B", "quarter": "Q1", "revenue": 200},
                {"category": "B", "quarter": "Q2", "revenue": 250},
            ],
            rows="category",
            cols="quarter",
            values="revenue",
            agg="sum",
        )

    In template::

        {{ pivot|safe }}

    Args:
        data: List of dicts (flat records)
        rows: Field name for row grouping
        cols: Field name for column grouping
        values: Field name for the numeric value
        agg: Aggregation function — "sum", "avg", "count", "min", "max" (default: "sum")
        title: Optional table title
        show_totals: Show row/column totals (default: True)
        custom_class: Additional CSS classes
    """

    AGG_FUNCS = {
        "sum": sum,
        "avg": lambda vals: sum(vals) / len(vals) if vals else 0,
        "count": len,
        "min": lambda vals: min(vals) if vals else 0,
        "max": lambda vals: max(vals) if vals else 0,
    }

    def __init__(
        self,
        data: Optional[list] = None,
        rows: str = "",
        cols: str = "",
        values: str = "",
        agg: str = "sum",
        title: Optional[str] = None,
        show_totals: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            data=data,
            rows=rows,
            cols=cols,
            values=values,
            agg=agg,
            title=title,
            show_totals=show_totals,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data or []
        self.rows = str(rows)
        self.cols = str(cols)
        self.values = str(values)
        self.agg = agg if agg in self.AGG_FUNCS else "sum"
        self.title = title
        self.show_totals = show_totals
        self.custom_class = custom_class

    def _pivot(self):
        """Build pivot structure: row_keys, col_keys, cells dict."""
        row_keys_set = []
        col_keys_set = []
        cells = {}  # (row_key, col_key) -> list of values

        for record in self.data:
            if not isinstance(record, dict):
                continue
            rk = str(record.get(self.rows, ""))
            ck = str(record.get(self.cols, ""))
            try:
                val = float(record.get(self.values, 0))
            except (ValueError, TypeError):
                val = 0

            if rk not in row_keys_set:
                row_keys_set.append(rk)
            if ck not in col_keys_set:
                col_keys_set.append(ck)

            cells.setdefault((rk, ck), []).append(val)

        agg_fn = self.AGG_FUNCS[self.agg]
        agg_cells = {}
        for key, vals in cells.items():
            agg_cells[key] = agg_fn(vals)

        return row_keys_set, col_keys_set, agg_cells

    def _format_val(self, v):
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}"

    def _render_custom(self) -> str:
        classes = ["dj-pivot"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        if not self.data or not self.rows or not self.cols or not self.values:
            return f'<div class="{class_str}"><table class="dj-pivot__table"></table></div>'

        row_keys, col_keys, agg_cells = self._pivot()

        parts = []

        if self.title:
            parts.append(
                f'<caption class="dj-pivot__title">{html.escape(str(self.title))}</caption>'
            )

        # Header row
        header_cells = [f'<th class="dj-pivot__corner">{html.escape(self.rows)}</th>']
        for ck in col_keys:
            header_cells.append(f'<th class="dj-pivot__colheader">{html.escape(ck)}</th>')
        if self.show_totals:
            header_cells.append('<th class="dj-pivot__colheader dj-pivot__total-header">Total</th>')
        parts.append(f"<thead><tr>{''.join(header_cells)}</tr></thead>")

        # Body rows
        body_rows = []
        col_totals = {ck: 0 for ck in col_keys}
        grand_total = 0

        for rk in row_keys:
            row_cells = [f'<th class="dj-pivot__rowheader">{html.escape(rk)}</th>']
            row_total = 0
            for ck in col_keys:
                val = agg_cells.get((rk, ck), 0)
                row_total += val
                col_totals[ck] += val
                row_cells.append(f'<td class="dj-pivot__cell">{self._format_val(val)}</td>')
            if self.show_totals:
                grand_total += row_total
                row_cells.append(
                    f'<td class="dj-pivot__cell dj-pivot__row-total">{self._format_val(row_total)}</td>'
                )
            body_rows.append(f"<tr>{''.join(row_cells)}</tr>")

        parts.append(f"<tbody>{''.join(body_rows)}</tbody>")

        # Totals footer
        if self.show_totals:
            foot_cells = ['<th class="dj-pivot__rowheader">Total</th>']
            for ck in col_keys:
                foot_cells.append(
                    f'<td class="dj-pivot__cell dj-pivot__col-total">'
                    f"{self._format_val(col_totals[ck])}</td>"
                )
            foot_cells.append(
                f'<td class="dj-pivot__cell dj-pivot__grand-total">'
                f"{self._format_val(grand_total)}</td>"
            )
            parts.append(f"<tfoot><tr>{''.join(foot_cells)}</tr></tfoot>")

        return (
            f'<div class="{class_str}">'
            f'<table class="dj-pivot__table" role="grid">{"".join(parts)}</table></div>'
        )
