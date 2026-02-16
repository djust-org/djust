"""
DataTable Component

Displays data in a responsive table format with hover effects and optional actions.
Used for property lists, tenant lists, lease lists, maintenance lists, etc.
"""

from djust.components.base import Component
from django.utils.html import format_html, escape
from django.utils.safestring import mark_safe
from typing import List, Dict, Any, Optional


class DataTable(Component):
    """
    A responsive table component for displaying structured data.

    Features:
    - Responsive design (scrollable on mobile)
    - Hover effects
    - Action column support
    - Empty state handling
    - Full dark/light mode support

    Example:
        table = DataTable(
            headers=["Property", "Address", "Status", "Rent", "Actions"],
            rows=[
                {
                    "Property": "Sunset Apartments",
                    "Address": "123 Main St",
                    "Status": '<span class="badge">Active</span>',  # Can include HTML
                    "Rent": "$1,200/mo",
                    "Actions": '<a href="/edit/1">Edit</a>'
                }
            ],
            empty_message="No properties found"
        )
        # In template: {{ table.render }}
    """

    def __init__(
        self,
        headers: List[str],
        rows: List[Dict[str, Any]],
        empty_message: str = "No data available",
        hover: bool = True,
        striped: bool = False
    ):
        self.headers = headers
        self.rows = rows
        self.empty_message = empty_message
        self.hover = hover
        self.striped = striped

    def render(self) -> str:
        """Render the data table component."""

        # If no rows, show empty state
        if not self.rows:
            return format_html(
                '<div class="bg-card border border-border rounded-lg p-12 text-center">'
                '<i data-lucide="inbox" class="w-12 h-12 mx-auto mb-4 text-muted-foreground"></i>'
                '<p class="text-muted-foreground text-lg">{}</p>'
                '</div>',
                self.empty_message
            )

        # Build table headers (escape header text to prevent XSS)
        header_cells = []
        for header in self.headers:
            header_cells.append(
                format_html(
                    '<th class="px-4 py-3 text-left text-sm font-medium text-muted-foreground">{}</th>',
                    header
                )
            )

        # Build table rows
        row_html_list = []
        for idx, row in enumerate(self.rows):
            # Row classes
            row_classes = ["border-t border-border"]
            if self.hover:
                row_classes.append("hover:bg-accent/50 transition-colors cursor-pointer")
            if self.striped and idx % 2 == 1:
                row_classes.append("bg-muted/20")

            # Build cells for this row (escape cell values to prevent XSS)
            cells = []
            for header in self.headers:
                cell_value = row.get(header, "")
                cells.append(
                    format_html(
                        '<td class="px-4 py-3 text-sm text-card-foreground">{}</td>',
                        cell_value
                    )
                )

            row_html_list.append(
                format_html(
                    '<tr class="{}">{}</tr>',
                    ' '.join(row_classes),
                    mark_safe(''.join(str(cell) for cell in cells))
                )
            )

        # Combine all HTML parts
        header_html = mark_safe(''.join(str(h) for h in header_cells))
        rows_html = mark_safe(''.join(str(r) for r in row_html_list))

        return format_html(
            '<div class="bg-card border border-border rounded-lg overflow-hidden">'
            '<div class="overflow-x-auto">'
            '<table class="w-full">'
            '<thead class="bg-muted/30 border-b border-border"><tr>{}</tr></thead>'
            '<tbody>{}</tbody>'
            '</table>'
            '</div>'
            '</div>',
            header_html,
            rows_html
        )
